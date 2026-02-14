"""Sensor platform for State Grid Info integration."""

import logging
import json
import asyncio
from datetime import datetime, timedelta
import paho.mqtt.client as mqtt

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import Throttle
from homeassistant.const import CONF_NAME

from .const import (
    DOMAIN,
    DATA_SOURCE_HASSBOX,
    DATA_SOURCE_QINGLONG,
    BILLING_STANDARD_YEAR_阶梯,
    BILLING_STANDARD_YEAR_阶梯_峰平谷,
    BILLING_STANDARD_MONTH_阶梯,
    BILLING_STANDARD_MONTH_阶梯_峰平谷,
    BILLING_STANDARD_MONTH_阶梯_峰平谷_变动价格,
    BILLING_STANDARD_OTHER_平均单价,
    CONF_DATA_SOURCE,
    CONF_BILLING_STANDARD,
    CONF_CONSUMER_NUMBER,
    CONF_CONSUMER_NUMBER_INDEX,
    CONF_CONSUMER_NAME,
    CONF_MQTT_HOST,
    CONF_MQTT_PORT,
    CONF_MQTT_USERNAME,
    CONF_MQTT_PASSWORD,
    CONF_STATE_GRID_ID,
    CONF_LADDER_LEVEL_1,
    CONF_LADDER_LEVEL_2,
    CONF_LADDER_PRICE_1,
    CONF_LADDER_PRICE_2,
    CONF_LADDER_PRICE_3,
    CONF_YEAR_LADDER_START,
    CONF_PRICE_PEAK,
    CONF_PRICE_FLAT,
    CONF_PRICE_VALLEY,
    CONF_PRICE_TIP,
    CONF_MONTH_PRICES,
    CONF_AVERAGE_PRICE,
    CONF_IS_PREPAID,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the State Grid Info sensor."""
    config = entry.data

    db = hass.data[DOMAIN][entry.entry_id].get("database")

    coordinator = StateGridInfoDataCoordinator(hass, config, db)
    await coordinator.async_config_entry_first_refresh()

    sensor = StateGridInfoSensor(coordinator, config)

    if DOMAIN in hass.data and entry.entry_id in hass.data[DOMAIN]:
        hass.data[DOMAIN][entry.entry_id]["entities"] = [sensor]

    async_add_entities([sensor], True)


class StateGridInfoDataCoordinator(DataUpdateCoordinator):
    """Class to manage fetching State Grid Info data."""

    def __init__(self, hass: HomeAssistant, config: dict, db=None):
        """Initialize the data coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=10),
        )
        self.config = config
        self.data = None
        self.mqtt_client = None
        self.last_update_time = datetime.now()
        self.db = db
        self._setup_data_source()

    def _setup_data_source(self):
        """Set up the data source based on configuration."""
        if self.config.get(CONF_DATA_SOURCE) == DATA_SOURCE_HASSBOX:
            # HassBox集成数据源不需要特殊设置
            pass
        elif self.config.get(CONF_DATA_SOURCE) == DATA_SOURCE_QINGLONG:
            # 设置MQTT客户端
            self._setup_mqtt_client()

    def _setup_mqtt_client(self):
        """Set up MQTT client for Qinglong script data source."""
        try:
            # 如果已有客户端，先停止并断开连接
            if self.mqtt_client:
                try:
                    self.mqtt_client.loop_stop()
                    self.mqtt_client.disconnect()
                    _LOGGER.info("断开旧的MQTT连接")
                except Exception as ex:
                    _LOGGER.warning("断开旧MQTT连接时出错: %s", ex)

            # 创建新的MQTT客户端，使用随机客户端ID避免冲突
            client_id = f"state_grid_client_{self.config.get(CONF_STATE_GRID_ID)}_{int(datetime.now().timestamp())}"
            client = mqtt.Client(client_id=client_id, clean_session=True)

            # 设置认证信息
            if self.config.get(CONF_MQTT_USERNAME) and self.config.get(
                CONF_MQTT_PASSWORD
            ):
                client.username_pw_set(
                    self.config.get(CONF_MQTT_USERNAME),
                    self.config.get(CONF_MQTT_PASSWORD),
                )

            # 设置回调函数
            client.on_connect = self._on_mqtt_connect
            client.on_message = self._on_mqtt_message
            client.on_disconnect = self._on_mqtt_disconnect

            # 设置自动重连
            client.reconnect_delay_set(min_delay=1, max_delay=120)

            # 连接MQTT服务器
            _LOGGER.info(
                "正在连接MQTT服务器: %s:%s",
                self.config.get(CONF_MQTT_HOST),
                self.config.get(CONF_MQTT_PORT, 1883),
            )

            try:
                client.connect(
                    self.config.get(CONF_MQTT_HOST),
                    self.config.get(CONF_MQTT_PORT, 1883),
                    keepalive=60,
                )
                client.loop_start()
                self.mqtt_client = client
                _LOGGER.info("MQTT客户端设置完成，客户端ID: %s", client_id)
            except Exception as connect_ex:
                _LOGGER.error("MQTT连接失败，将在下次更新时重试: %s", connect_ex)
        except Exception as ex:
            _LOGGER.error("设置MQTT客户端失败: %s", ex)

    def _on_mqtt_connect(self, client, userdata, flags, rc):
        """Handle MQTT connection."""
        rc_codes = {
            0: "连接成功",
            1: "连接被拒绝-协议版本错误",
            2: "连接被拒绝-无效的客户端标识符",
            3: "连接被拒绝-服务器不可用",
            4: "连接被拒绝-用户名或密码错误",
            5: "连接被拒绝-未授权",
        }

        rc_message = rc_codes.get(rc, f"未知错误代码: {rc}")
        _LOGGER.info("MQTT连接状态: %s (代码: %s)", rc_message, rc)

        if rc == 0:
            # 订阅国网ID对应的主题
            topic = f"nodejs/state-grid/{self.config.get(CONF_STATE_GRID_ID)}"
            result, mid = client.subscribe(topic)
            if result == 0:
                _LOGGER.info("成功订阅主题: %s (消息ID: %s)", topic, mid)
            else:
                _LOGGER.error("订阅主题失败: %s (结果代码: %s)", topic, result)
        else:
            _LOGGER.error("MQTT连接失败，将在下次更新时重试")

    def _on_mqtt_message(self, client, userdata, msg):
        """Handle MQTT message."""
        try:
            _LOGGER.info("📨 收到来自主题 %s 的消息", msg.topic)

            receive_time = datetime.now()

            payload = json.loads(msg.payload.decode())
            _LOGGER.info("📦 MQTT消息内容: %s", json.dumps(payload, ensure_ascii=False)[:500])
            
            processed_data = self._process_qinglong_data(payload)
            _LOGGER.info("🔄 处理后的数据 - dayList条数: %d, monthList条数: %d", 
                        len(processed_data.get("dayList", [])),
                        len(processed_data.get("monthList", [])))

            self.data = processed_data
            self.last_update_time = receive_time

            if self.db:
                cons_no = self.config.get(CONF_STATE_GRID_ID, "")
                _LOGGER.info("🔍 检查数据库连接 - db存在: True, cons_no: %s", cons_no)
                if cons_no:
                    self.db.save_daily_data(cons_no, processed_data.get("dayList", []))
                    self.db.save_monthly_data(
                        cons_no, processed_data.get("monthList", [])
                    )
                    self.db.save_metadata(
                        cons_no,
                        processed_data.get("balance", 0),
                        processed_data.get("date", ""),
                    )
                    self.db.cleanup_old_data(cons_no, 365)
                    _LOGGER.info("✅ 数据已保存到SQLite数据库")
                else:
                    _LOGGER.warning("⚠️ cons_no 为空，无法保存到数据库")
            else:
                _LOGGER.warning("⚠️ 数据库对象不存在，无法保存数据")

            self.async_set_updated_data(self.data)

            _LOGGER.info(
                "成功更新MQTT数据，接收时间: %s",
                receive_time.strftime("%Y-%m-%d %H:%M:%S"),
            )
        except json.JSONDecodeError as json_err:
            _LOGGER.error("MQTT消息JSON解析错误: %s", json_err)
        except Exception as ex:
            _LOGGER.error("处理MQTT消息时出错: %s", ex)

    def _on_mqtt_disconnect(self, client, userdata, rc):
        """Handle MQTT disconnection."""
        if rc == 0:
            _LOGGER.info("MQTT客户端正常断开连接")
        else:
            _LOGGER.warning("MQTT客户端意外断开连接，代码: %s，将尝试自动重连", rc)

    async def async_unload(self):
        """Clean up resources when unloading."""
        _LOGGER.info("正在清理State Grid Info资源")
        if self.mqtt_client:
            try:
                self.mqtt_client.loop_stop()
                self.mqtt_client.disconnect()
                _LOGGER.info("已断开MQTT连接")
            except Exception as ex:
                _LOGGER.warning("断开MQTT连接时出错: %s", ex)

    async def _async_update_data(self):
        """Fetch data from the appropriate source."""
        try:
            # 记录当前更新时间
            current_time = datetime.now()
            time_diff = current_time - self.last_update_time

            # 记录日志
            _LOGGER.debug(
                "执行数据更新，距离上次更新已过 %s 分钟", time_diff.total_seconds() / 60
            )

            # 更新时间戳
            self.last_update_time = current_time

            if self.config.get(CONF_DATA_SOURCE) == DATA_SOURCE_HASSBOX:
                # 每次都重新从文件读取最新数据
                # 强制刷新标志 - 如果超过10分钟没有更新，强制刷新
                force_refresh = time_diff.total_seconds() > 600  # 10分钟
                if force_refresh:
                    _LOGGER.info("已超过10分钟未更新数据，强制刷新")

                # 使用异步执行器运行文件读取操作
                hassbox_data = await self.hass.async_add_executor_job(
                    self._fetch_hassbox_data
                )

                # 更新数据时间戳
                self.last_update_time = current_time

                # 清除过期标志
                if isinstance(hassbox_data, dict):
                    hassbox_data["data_expired"] = False

                # 更新 self.data
                self.data = hassbox_data
                return hassbox_data

            elif self.config.get(CONF_DATA_SOURCE) == DATA_SOURCE_QINGLONG:
                if not self.mqtt_client:
                    _LOGGER.info("MQTT客户端不存在，创建新客户端")
                    self._setup_mqtt_client()
                elif not self.mqtt_client.is_connected():
                    _LOGGER.info("MQTT客户端未连接，尝试重新连接")
                    self._setup_mqtt_client()
                else:
                    if time_diff.total_seconds() > 600:
                        _LOGGER.info("长时间未收到数据更新，尝试重新订阅主题")
                        topic = (
                            f"nodejs/state-grid/{self.config.get(CONF_STATE_GRID_ID)}"
                        )
                        self.mqtt_client.unsubscribe(topic)
                        self.mqtt_client.subscribe(topic)
                        _LOGGER.info("已重新订阅主题: %s", topic)

                if not self.data or (time_diff.total_seconds() > 1800):
                    _LOGGER.info("MQTT数据为空或已过期，尝试从数据库加载历史数据")
                    if self.db:
                        cons_no = self.config.get(CONF_STATE_GRID_ID, "")
                        if cons_no:
                            db_data = self.db.get_all_data(cons_no, 365)
                            if db_data.get("dayList"):
                                _LOGGER.info(
                                    "从数据库加载了 %d 条历史数据",
                                    len(db_data.get("dayList", [])),
                                )
                                self.data = db_data
                                return db_data
                    return {}

                return self.data
            return {}
        except Exception as ex:
            _LOGGER.error("Error updating State Grid Info data: %s", ex)
            raise UpdateFailed(f"Error updating data: {ex}")

    def _fetch_hassbox_data(self):
        """Fetch data from HassBox integration."""
        try:
            import os
            import time
            import random
            from datetime import datetime

            # 添加随机参数防止缓存
            random_param = random.randint(1, 100000)

            # 获取标准配置路径
            config_path = self.hass.config.path(".storage", "state_grid.config")
            _LOGGER.debug(
                "尝试读取HassBox配置文件: %s (随机参数: %s)", config_path, random_param
            )

            # 检查文件是否存在
            if os.path.exists(config_path):
                # 检查文件修改时间
                try:
                    file_mod_time = os.path.getmtime(config_path)
                    file_mod_datetime = datetime.fromtimestamp(file_mod_time)
                    now = datetime.now()
                    time_diff = now - file_mod_datetime

                    _LOGGER.info(
                        "HassBox配置文件存在，最后修改时间: %s (距现在 %.1f 小时)",
                        file_mod_datetime.strftime("%Y-%m-%d %H:%M:%S"),
                        time_diff.total_seconds() / 3600,
                    )

                    # 如果文件超过24小时未更新，记录警告
                    if time_diff.total_seconds() > 86400:  # 24小时
                        _LOGGER.warning(
                            "HassBox配置文件已超过24小时未更新，可能需要检查HassBox集成是否正常工作"
                        )
                except Exception as time_err:
                    _LOGGER.warning("获取文件修改时间失败: %s", time_err)

                # 读取文件内容
                try:
                    # 清除系统缓存，确保读取最新文件
                    try:
                        os.stat(config_path)  # 刷新文件状态
                    except Exception:
                        pass

                    # 使用二进制模式打开，避免缓存问题
                    with open(config_path, "rb") as file:
                        _LOGGER.debug("成功打开配置文件")
                        file_content = file.read().decode("utf-8")

                        # 检查文件内容是否为空
                        if not file_content.strip():
                            _LOGGER.error("HassBox配置文件内容为空")
                            return {}

                        # 解析JSON数据
                        try:
                            config_data = json.loads(file_content)
                            _LOGGER.debug("成功解析JSON数据")
                        except json.JSONDecodeError as json_err:
                            _LOGGER.error("JSON解析错误: %s", json_err)
                            # 记录文件内容的前100个字符，帮助诊断
                            _LOGGER.debug("文件内容前100个字符: %s", file_content[:100])
                            return {}

                        # 验证数据结构
                        if "data" in config_data:
                            _LOGGER.debug("配置包含data字段")
                            if "powerUserList" in config_data["data"]:
                                _LOGGER.debug("配置包含powerUserList字段")
                                index = self.config.get(CONF_CONSUMER_NUMBER_INDEX, 0)
                                power_user_list = config_data["data"]["powerUserList"]

                                if not power_user_list:
                                    _LOGGER.error("用户列表为空")
                                    return {}

                                _LOGGER.info(
                                    "找到用户列表，共%d个用户", len(power_user_list)
                                )

                                if 0 <= index < len(power_user_list):
                                    _LOGGER.info("成功获取索引为%d的用户数据", index)

                                    # 检查用户数据是否包含必要字段
                                    user_data = power_user_list[index]
                                    if not user_data:
                                        _LOGGER.error("用户数据为空")
                                        return {}

                                    # 检查刷新时间
                                    refresh_time = user_data.get("refresh_time", "")
                                    if refresh_time:
                                        _LOGGER.info("数据刷新时间: %s", refresh_time)
                                    else:
                                        _LOGGER.warning("数据没有刷新时间信息")

                                    return self._process_hassbox_data(user_data)
                                else:
                                    _LOGGER.error(
                                        "用户索引%d超出范围(0-%d)",
                                        index,
                                        len(power_user_list) - 1,
                                    )
                            else:
                                _LOGGER.error("配置中缺少powerUserList字段")
                        else:
                            _LOGGER.error("配置中缺少data字段")
                except Exception as read_err:
                    _LOGGER.error("读取文件错误: %s", read_err)
            else:
                _LOGGER.error("HassBox配置文件不存在: %s", config_path)
                # 尝试列出目录内容
                try:
                    storage_dir = self.hass.config.path(".storage")
                    if os.path.exists(storage_dir):
                        files = [
                            f
                            for f in os.listdir(storage_dir)
                            if f.startswith("state_grid") or "grid" in f
                        ]
                        if files:
                            _LOGGER.info("找到可能相关的文件: %s", files)
                        else:
                            _LOGGER.info("存储目录中没有找到相关文件")
                    else:
                        _LOGGER.error("存储目录不存在: %s", storage_dir)
                except Exception as dir_err:
                    _LOGGER.error("无法列出目录内容: %s", dir_err)

            return {}
        except Exception as ex:
            _LOGGER.error("获取HassBox数据时发生错误: %s", ex)
            return {}

    def _process_hassbox_data(self, power_user_data):
        """Process HassBox integration data."""
        try:
            # 获取户名并记录日志
            consumer_name = power_user_data.get("consName_dst", "")

            # 提取文件json日用电数据（大概是最近40天左右）
            daily_bill_list = power_user_data.get("daily_bill_list", [])
            # 重写结构
            daylist1 = []
            for item in daily_bill_list:
                day = item.get("day", "")
                if day:
                    day = f"{day[0:4]}-{day[4:6]}-{day[6:]}"
                    # 处理"-"值，将其转换为0
                    day_ele_pq = item.get("dayElePq", "0")
                    this_tpq = item.get("thisTPq", "0")
                    this_ppq = item.get("thisPPq", "0")
                    this_npq = item.get("thisNPq", "0")
                    this_vpq = item.get("thisVPq", "0")

                    daylist1.append(
                        {
                            "day": day,
                            "dayEleNum": (
                                float(day_ele_pq) if day_ele_pq != "-" else 0.0
                            ),
                            "dayTPq": float(this_tpq) if this_tpq != "-" else 0.0,
                            "dayPPq": float(this_ppq) if this_ppq != "-" else 0.0,
                            "dayNPq": float(this_npq) if this_npq != "-" else 0.0,
                            "dayVPq": float(this_vpq) if this_vpq != "-" else 0.0,
                        }
                    )

            # 提取所有的日用电，在每个月用电下面，需要for循环提取出来
            daylist2 = []
            month_bill_list = power_user_data.get("month_bill_list", [])
            for month_data in month_bill_list:
                if "daily_ele" in month_data:
                    daylist2.append(month_data["daily_ele"])

            daylist3 = [item for sublist in daylist2 for item in sublist]  # 将数据展平

            # 重写结构
            daylist4 = []
            for item in daylist3:
                day = item.get("day", "")
                if day:
                    day = f"{day[0:4]}-{day[4:6]}-{day[6:]}"
                    # 处理"-"值，将其转换为0
                    day_ele_pq = item.get("dayElePq", "0")
                    this_tpq = item.get("thisTPq", "0")
                    this_ppq = item.get("thisPPq", "0")
                    this_npq = item.get("thisNPq", "0")
                    this_vpq = item.get("thisVPq", "0")

                    daylist4.append(
                        {
                            "day": day,
                            "dayEleNum": (
                                float(day_ele_pq) if day_ele_pq != "-" else 0.0
                            ),
                            "dayTPq": float(this_tpq) if this_tpq != "-" else 0.0,
                            "dayPPq": float(this_ppq) if this_ppq != "-" else 0.0,
                            "dayNPq": float(this_npq) if this_npq != "-" else 0.0,
                            "dayVPq": float(this_vpq) if this_vpq != "-" else 0.0,
                        }
                    )

            # 合并daylist4和daylist1
            daylist1.extend(daylist4)

            # 筛选重复，只保留数值大的数
            daylist5 = {}
            for item in daylist1:
                day = item["day"]
                day_ele_num = item["dayEleNum"]
                if day not in daylist5 or day_ele_num > daylist5[day]["dayEleNum"]:
                    daylist5[day] = item

            # 按日期重新排序
            daylist6 = sorted(daylist5.values(), key=lambda x: x["day"])

            # 取最新的370个数据（总数大概370-390波动）
            daylist7 = list(reversed(daylist6))

            # 先创建一个临时数据结构，用于计算电费
            # 这样可以确保在计算电费时 self.data 不为 None
            temp_data = {
                "date": power_user_data.get("refresh_time", ""),
                "balance": float(power_user_data.get("balance", 0)),
                "dayList": daylist7,
            }

            # 临时保存当前的 self.data
            original_data = self.data

            # 设置临时数据用于计算
            self.data = temp_data

            # 计算每日电费
            dayList = self._calculate_daily_cost(daylist7)

            # 删除尾部的连续全0数据
            # 从后往前遍历，找到第一个非0的数据项
            last_non_zero_index = len(dayList) - 1
            while last_non_zero_index >= 0:
                item = dayList[last_non_zero_index]
                # 检查所有数值字段是否都为0
                if (
                    item.get("dayEleNum", 0) == 0
                    and item.get("dayEleCost", 0) == 0
                    and item.get("dayTPq", 0) == 0
                    and item.get("dayPPq", 0) == 0
                    and item.get("dayNPq", 0) == 0
                    and item.get("dayVPq", 0) == 0
                ):
                    last_non_zero_index -= 1
                else:
                    break
            # 只保留到第一个非0数据项
            if last_non_zero_index < len(dayList) - 1:
                dayList = dayList[: last_non_zero_index + 1]

            # 恢复原始数据
            self.data = original_data

            # 重写月用电结构
            monthList = []
            for item in month_bill_list:
                month = item.get("month", "")
                if month:
                    month = f"{month[0:4]}-{month[4:]}"
                    monthList.append(
                        {
                            "month": month,
                            "monthEleNum": float(item.get("monthEleNum", 0)),
                            "monthEleCost": float(item.get("monthEleCost", 0)),
                            "monthTPq": float(item.get("month_t_ele_num", 0)),
                            "monthPPq": float(item.get("month_p_ele_num", 0)),
                            "monthNPq": float(item.get("month_n_ele_num", 0)),
                            "monthVPq": float(item.get("month_v_ele_num", 0)),
                        }
                    )

            # 处理月数据
            monthList = self._process_month_data(dayList, monthList)

            # 处理年数据
            yearList = self._process_year_data(monthList)

            return {
                "date": power_user_data.get("refresh_time", ""),
                "balance": float(power_user_data.get("balance", 0)),
                "dayList": dayList,
                "monthList": monthList,
                "yearList": yearList,
                "consumer_name": power_user_data.get("consName_dst", ""),
            }
        except Exception as ex:
            _LOGGER.error("Error processing HassBox data: %s", ex)
            return {}

    def _process_qinglong_data(self, payload):
        """Process Qinglong script data."""
        try:
            dayList_ori = payload.get("dayList", [])

            # 重写日结构
            dayList7 = []
            for item in dayList_ori:
                dayList7.append(
                    {
                        "day": item.get("day", ""),
                        "dayEleNum": float(item.get("dayElePq", 0)),
                        "dayTPq": float(item.get("thisTPq", 0)),
                        "dayPPq": float(item.get("thisPPq", 0)),
                        "dayNPq": float(item.get("thisNPq", 0)),
                        "dayVPq": float(item.get("thisVPq", 0)),
                    }
                )

            # 先创建一个临时数据结构，用于计算电费
            # 这样可以确保在计算电费时 self.data 不为 None
            temp_data = {
                "date": payload.get("date", ""),
                "balance": float(payload.get("sumMoney", 0)),
                "dayList": dayList7,
            }

            # 临时保存当前的 self.data
            original_data = self.data

            # 设置临时数据用于计算
            self.data = temp_data

            # 计算每日电费
            dayList = self._calculate_daily_cost(dayList7)

            # 恢复原始数据
            self.data = original_data

            # 处理月数据
            monthList_ori = payload.get("monthList", [])
            monthList = self._process_month_data(dayList, monthList_ori)

            # 处理年数据
            yearList = self._process_year_data(monthList)

            return {
                "date": payload.get("date", ""),
                "balance": float(payload.get("sumMoney", 0)),
                "dayList": dayList,
                "monthList": monthList,
                "yearList": yearList,
            }
        except Exception as ex:
            _LOGGER.error("Error processing Qinglong data: %s", ex)
            return {}

    def _calculate_daily_cost(self, day_list):
        """Calculate daily electricity cost based on billing standard."""
        try:
            billing_standard = self.config.get(CONF_BILLING_STANDARD)

            result = []
            for item in day_list:
                day_cost = 0

                # 根据计费标准计算每日电费
                day_cost = self._calculate_cost_by_standard(item, billing_standard)

                # 添加电费到结果中，调整字段顺序
                result.append(
                    {
                        "day": item["day"],
                        "dayEleNum": item["dayEleNum"],
                        "dayEleCost": round(day_cost, 2),
                        "dayTPq": item["dayTPq"],
                        "dayPPq": item["dayPPq"],
                        "dayNPq": item["dayNPq"],
                        "dayVPq": item["dayVPq"],
                    }
                )

            return result
        except Exception as ex:
            _LOGGER.error("Error calculating daily cost: %s", ex)
            return day_list

    def _calculate_cost_by_standard(self, day_data, standard):
        """Calculate cost based on specific billing standard."""
        try:
            day_ele_num = day_data["dayEleNum"]
            day_tpq = day_data["dayTPq"]
            day_ppq = day_data["dayPPq"]
            day_npq = day_data["dayNPq"]
            day_vpq = day_data["dayVPq"]

            if standard == BILLING_STANDARD_YEAR_阶梯:
                # 年阶梯计费
                ladder_level_1 = self.config.get(CONF_LADDER_LEVEL_1, 2160)
                ladder_level_2 = self.config.get(CONF_LADDER_LEVEL_2, 4200)
                price_1 = self.config.get(CONF_LADDER_PRICE_1, 0.4983)
                price_2 = self.config.get(CONF_LADDER_PRICE_2, 0.5483)
                price_3 = self.config.get(CONF_LADDER_PRICE_3, 0.7983)

                # 获取年阶梯起始日期（默认为当年1月1日）
                year_ladder_start = self.config.get(CONF_YEAR_LADDER_START, "0101")

                # 获取当前日期和年份
                current_day = day_data["day"]
                current_year = int(current_day.split("-")[0])
                current_month = int(current_day.split("-")[1])
                current_day_int = int(current_day.split("-")[2])

                # 解析年阶梯起始日期
                start_month = int(year_ladder_start[:2])
                start_day_int = int(year_ladder_start[2:])

                # 推算年阶梯起始日期的年份
                # 如果当前日期小于起始日期，则是上一年的起始日期
                if (current_month < start_month) or (
                    current_month == start_month and current_day_int < start_day_int
                ):
                    ladder_year = current_year - 1
                else:
                    ladder_year = current_year

                # 格式化年阶梯起始日期和结束日期
                year_ladder_start_date = (
                    f"{ladder_year}-{year_ladder_start[:2]}-{year_ladder_start[2:]}"
                )
                year_ladder_end_date = (
                    f"{ladder_year + 1}-{year_ladder_start[:2]}-{year_ladder_start[2:]}"
                )

                # 计算累计用电量
                year_accumulated = 0

                if self.data is not None:
                    for data in self.data.get("dayList", []):
                        # 计算年累计用电量：从年阶梯起始日期到当前日期
                        if (
                            data["day"] >= year_ladder_start_date
                            and data["day"] <= current_day
                        ):
                            year_accumulated += data["dayEleNum"]

                # 根据累计用电量计算阶梯电价
                if year_accumulated <= ladder_level_1:
                    # 第一阶梯
                    return day_ele_num * price_1
                elif year_accumulated <= ladder_level_2:
                    # 第二阶梯
                    # 检查是否跨阶梯
                    if year_accumulated - day_ele_num <= ladder_level_1:
                        # 跨阶梯，部分第一阶梯，部分第二阶梯
                        first_part = ladder_level_1 - (year_accumulated - day_ele_num)
                        second_part = day_ele_num - first_part
                        return first_part * price_1 + second_part * price_2
                    else:
                        # 完全在第二阶梯
                        return day_ele_num * price_2
                else:
                    # 第三阶梯
                    # 检查是否跨阶梯
                    if year_accumulated - day_ele_num <= ladder_level_1:
                        # 跨越第一、第二、第三阶梯
                        first_part = ladder_level_1 - (year_accumulated - day_ele_num)
                        remaining = day_ele_num - first_part
                        if (
                            year_accumulated - day_ele_num + first_part + remaining
                            <= ladder_level_2
                        ):
                            # 部分在第二阶梯
                            second_part = ladder_level_2 - (
                                year_accumulated - day_ele_num + first_part
                            )
                            third_part = remaining - second_part
                            return (
                                first_part * price_1
                                + second_part * price_2
                                + third_part * price_3
                            )
                        else:
                            # 完全在第三阶梯
                            return day_ele_num * price_3
                    elif year_accumulated - day_ele_num <= ladder_level_2:
                        # 跨越第二、第三阶梯
                        second_part = ladder_level_2 - (year_accumulated - day_ele_num)
                        third_part = day_ele_num - second_part
                        return second_part * price_2 + third_part * price_3
                    else:
                        # 完全在第三阶梯
                        return day_ele_num * price_3

            elif standard == BILLING_STANDARD_YEAR_阶梯_峰平谷:
                # 年阶梯+峰平谷计费
                ladder_level_1 = self.config.get(CONF_LADDER_LEVEL_1, 2160)
                ladder_level_2 = self.config.get(CONF_LADDER_LEVEL_2, 4200)

                # 各阶梯的峰平谷价
                price_tip_1 = self.config.get(
                    f"{CONF_LADDER_PRICE_1}_{CONF_PRICE_TIP}", 0.5483
                )
                price_peak_1 = self.config.get(
                    f"{CONF_LADDER_PRICE_1}_{CONF_PRICE_PEAK}", 0.5483
                )
                price_flat_1 = self.config.get(
                    f"{CONF_LADDER_PRICE_1}_{CONF_PRICE_FLAT}", 0.5483
                )
                price_valley_1 = self.config.get(
                    f"{CONF_LADDER_PRICE_1}_{CONF_PRICE_VALLEY}", 0.2983
                )

                price_tip_2 = self.config.get(
                    f"{CONF_LADDER_PRICE_2}_{CONF_PRICE_TIP}", 0.5983
                )
                price_peak_2 = self.config.get(
                    f"{CONF_LADDER_PRICE_2}_{CONF_PRICE_PEAK}", 0.5983
                )
                price_flat_2 = self.config.get(
                    f"{CONF_LADDER_PRICE_2}_{CONF_PRICE_FLAT}", 0.5983
                )
                price_valley_2 = self.config.get(
                    f"{CONF_LADDER_PRICE_2}_{CONF_PRICE_VALLEY}", 0.3483
                )

                price_tip_3 = self.config.get(
                    f"{CONF_LADDER_PRICE_3}_{CONF_PRICE_TIP}", 0.8483
                )
                price_peak_3 = self.config.get(
                    f"{CONF_LADDER_PRICE_3}_{CONF_PRICE_PEAK}", 0.8483
                )
                price_flat_3 = self.config.get(
                    f"{CONF_LADDER_PRICE_3}_{CONF_PRICE_FLAT}", 0.8483
                )
                price_valley_3 = self.config.get(
                    f"{CONF_LADDER_PRICE_3}_{CONF_PRICE_VALLEY}", 0.5983
                )

                # 获取年阶梯起始日期（默认为当年1月1日）
                year_ladder_start = self.config.get(CONF_YEAR_LADDER_START, "0101")

                # 获取当前日期和年份
                current_day = day_data["day"]
                current_year = int(current_day.split("-")[0])
                current_month = int(current_day.split("-")[1])
                current_day_int = int(current_day.split("-")[2])

                # 解析年阶梯起始日期
                start_month = int(year_ladder_start[:2])
                start_day_int = int(year_ladder_start[2:])

                # 推算年阶梯起始日期的年份
                # 如果当前日期小于起始日期，则是上一年的起始日期
                if (current_month < start_month) or (
                    current_month == start_month and current_day_int < start_day_int
                ):
                    ladder_year = current_year - 1
                else:
                    ladder_year = current_year

                # 格式化年阶梯起始日期和结束日期
                year_ladder_start_date = (
                    f"{ladder_year}-{year_ladder_start[:2]}-{year_ladder_start[2:]}"
                )
                year_ladder_end_date = (
                    f"{ladder_year + 1}-{year_ladder_start[:2]}-{year_ladder_start[2:]}"
                )

                # 计算累计用电量
                year_accumulated = 0

                if self.data is not None:
                    for data in self.data.get("dayList", []):
                        # 计算年累计用电量：从年阶梯起始日期到当前日期
                        if (
                            data["day"] >= year_ladder_start_date
                            and data["day"] <= current_day
                        ):
                            year_accumulated += data["dayEleNum"]

                # 根据累计用电量确定当前阶梯
                if year_accumulated <= ladder_level_1:
                    # 第一阶梯
                    return (
                        day_tpq * price_tip_1
                        + day_ppq * price_peak_1
                        + day_npq * price_flat_1
                        + day_vpq * price_valley_1
                    )
                elif year_accumulated <= ladder_level_2:
                    # 第二阶梯
                    # 检查是否跨阶梯
                    if year_accumulated - day_ele_num <= ladder_level_1:
                        # 跨阶梯，需要按比例计算
                        ratio_1 = (
                            ladder_level_1 - (year_accumulated - day_ele_num)
                        ) / day_ele_num
                        ratio_2 = 1 - ratio_1

                        # 第一阶梯部分
                        cost_1 = (
                            day_tpq * price_tip_1 * ratio_1
                            + day_ppq * price_peak_1 * ratio_1
                            + day_npq * price_flat_1 * ratio_1
                            + day_vpq * price_valley_1 * ratio_1
                        )

                        # 第二阶梯部分
                        cost_2 = (
                            day_tpq * price_tip_2 * ratio_2
                            + day_ppq * price_peak_2 * ratio_2
                            + day_npq * price_flat_2 * ratio_2
                            + day_vpq * price_valley_2 * ratio_2
                        )

                        return cost_1 + cost_2
                    else:
                        # 完全在第二阶梯
                        return (
                            day_tpq * price_tip_2
                            + day_ppq * price_peak_2
                            + day_npq * price_flat_2
                            + day_vpq * price_valley_2
                        )
                else:
                    # 第三阶梯或跨阶梯
                    if year_accumulated - day_ele_num <= ladder_level_1:
                        # 跨越三个阶梯，需要按比例计算
                        remaining = year_accumulated - day_ele_num
                        ratio_1 = (ladder_level_1 - remaining) / day_ele_num
                        ratio_2 = (
                            (ladder_level_2 - ladder_level_1) / day_ele_num
                            if (ladder_level_2 - remaining) > 0
                            else 0
                        )
                        ratio_3 = 1 - ratio_1 - ratio_2

                        # 各阶梯部分费用
                        cost_1 = (
                            day_tpq * price_tip_1 * ratio_1
                            + day_ppq * price_peak_1 * ratio_1
                            + day_npq * price_flat_1 * ratio_1
                            + day_vpq * price_valley_1 * ratio_1
                        )

                        cost_2 = (
                            day_tpq * price_tip_2 * ratio_2
                            + day_ppq * price_peak_2 * ratio_2
                            + day_npq * price_flat_2 * ratio_2
                            + day_vpq * price_valley_2 * ratio_2
                        )

                        cost_3 = (
                            day_tpq * price_tip_3 * ratio_3
                            + day_ppq * price_peak_3 * ratio_3
                            + day_npq * price_flat_3 * ratio_3
                            + day_vpq * price_valley_3 * ratio_3
                        )

                        return cost_1 + cost_2 + cost_3
                    elif year_accumulated - day_ele_num <= ladder_level_2:
                        # 跨越第二、第三阶梯
                        ratio_2 = (
                            ladder_level_2 - (year_accumulated - day_ele_num)
                        ) / day_ele_num
                        ratio_3 = 1 - ratio_2

                        # 第二阶梯部分
                        cost_2 = (
                            day_tpq * price_tip_2 * ratio_2
                            + day_ppq * price_peak_2 * ratio_2
                            + day_npq * price_flat_2 * ratio_2
                            + day_vpq * price_valley_2 * ratio_2
                        )

                        # 第三阶梯部分
                        cost_3 = (
                            day_tpq * price_tip_3 * ratio_3
                            + day_ppq * price_peak_3 * ratio_3
                            + day_npq * price_flat_3 * ratio_3
                            + day_vpq * price_valley_3 * ratio_3
                        )

                        return cost_2 + cost_3
                    else:
                        # 完全在第三阶梯
                        return (
                            day_tpq * price_tip_3
                            + day_ppq * price_peak_3
                            + day_npq * price_flat_3
                            + day_vpq * price_valley_3
                        )

            elif standard == BILLING_STANDARD_MONTH_阶梯:
                # 月阶梯计费
                ladder_level_1 = self.config.get(CONF_LADDER_LEVEL_1, 180)
                ladder_level_2 = self.config.get(CONF_LADDER_LEVEL_2, 280)
                price_1 = self.config.get(CONF_LADDER_PRICE_1, 0.5224)
                price_2 = self.config.get(CONF_LADDER_PRICE_2, 0.6224)
                price_3 = self.config.get(CONF_LADDER_PRICE_3, 0.8334)

                # 获取当前年月
                current_year_month = day_data["day"][:7]  # 格式：YYYY-MM

                # 计算当月累计用电量（截至当前日期）
                month_accumulated = 0
                current_day = day_data["day"]
                if self.data is not None:
                    for data in self.data.get("dayList", []):
                        if (
                            data["day"].startswith(current_year_month)
                            and data["day"] <= current_day
                        ):
                            month_accumulated += data["dayEleNum"]

                # 根据累计用电量计算阶梯电价
                if month_accumulated <= ladder_level_1:
                    # 第一阶梯
                    return day_ele_num * price_1
                elif month_accumulated <= ladder_level_2:
                    # 第二阶梯
                    # 检查是否跨阶梯
                    if month_accumulated - day_ele_num <= ladder_level_1:
                        # 跨阶梯，部分第一阶梯，部分第二阶梯
                        first_part = ladder_level_1 - (month_accumulated - day_ele_num)
                        second_part = day_ele_num - first_part
                        return first_part * price_1 + second_part * price_2
                    else:
                        # 完全在第二阶梯
                        return day_ele_num * price_2
                else:
                    # 第三阶梯
                    # 检查是否跨阶梯
                    if month_accumulated - day_ele_num <= ladder_level_1:
                        # 跨越第一、第二、第三阶梯
                        first_part = ladder_level_1 - (month_accumulated - day_ele_num)
                        remaining = day_ele_num - first_part
                        if (
                            month_accumulated - day_ele_num + first_part + remaining
                            <= ladder_level_2
                        ):
                            # 部分在第二阶梯
                            second_part = ladder_level_2 - (
                                month_accumulated - day_ele_num + first_part
                            )
                            third_part = remaining - second_part
                            return (
                                first_part * price_1
                                + second_part * price_2
                                + third_part * price_3
                            )
                        else:
                            # 完全在第三阶梯
                            return day_ele_num * price_3
                    elif month_accumulated - day_ele_num <= ladder_level_2:
                        # 跨越第二、第三阶梯
                        second_part = ladder_level_2 - (month_accumulated - day_ele_num)
                        third_part = day_ele_num - second_part
                        return second_part * price_2 + third_part * price_3
                    else:
                        # 完全在第三阶梯
                        return day_ele_num * price_3

            elif standard == BILLING_STANDARD_MONTH_阶梯_峰平谷:
                # 月阶梯+峰平谷计费
                ladder_level_1 = self.config.get(CONF_LADDER_LEVEL_1, 180)
                ladder_level_2 = self.config.get(CONF_LADDER_LEVEL_2, 280)

                # 各阶梯的峰平谷价
                price_tip_1 = self.config.get(
                    f"{CONF_LADDER_PRICE_1}_{CONF_PRICE_TIP}", 0.5283
                )
                price_peak_1 = self.config.get(
                    f"{CONF_LADDER_PRICE_1}_{CONF_PRICE_PEAK}", 0.5283
                )
                price_flat_1 = self.config.get(
                    f"{CONF_LADDER_PRICE_1}_{CONF_PRICE_FLAT}", 0.5283
                )
                price_valley_1 = self.config.get(
                    f"{CONF_LADDER_PRICE_1}_{CONF_PRICE_VALLEY}", 0.5283
                )

                price_tip_2 = self.config.get(
                    f"{CONF_LADDER_PRICE_2}_{CONF_PRICE_TIP}", 0.5783
                )
                price_peak_2 = self.config.get(
                    f"{CONF_LADDER_PRICE_2}_{CONF_PRICE_PEAK}", 0.5783
                )
                price_flat_2 = self.config.get(
                    f"{CONF_LADDER_PRICE_2}_{CONF_PRICE_FLAT}", 0.5783
                )
                price_valley_2 = self.config.get(
                    f"{CONF_LADDER_PRICE_2}_{CONF_PRICE_VALLEY}", 0.5783
                )

                price_tip_3 = self.config.get(
                    f"{CONF_LADDER_PRICE_3}_{CONF_PRICE_TIP}", 0.8283
                )
                price_peak_3 = self.config.get(
                    f"{CONF_LADDER_PRICE_3}_{CONF_PRICE_PEAK}", 0.8283
                )
                price_flat_3 = self.config.get(
                    f"{CONF_LADDER_PRICE_3}_{CONF_PRICE_FLAT}", 0.8283
                )
                price_valley_3 = self.config.get(
                    f"{CONF_LADDER_PRICE_3}_{CONF_PRICE_VALLEY}", 0.8283
                )

                # 获取当前年月
                current_year_month = day_data["day"][:7]  # 格式：YYYY-MM

                # 计算当月累计用电量（截至当前日期）
                month_accumulated = 0
                current_day = day_data["day"]
                if self.data is not None:
                    for data in self.data.get("dayList", []):
                        if (
                            data["day"].startswith(current_year_month)
                            and data["day"] <= current_day
                        ):
                            month_accumulated += data["dayEleNum"]

                # 根据累计用电量确定当前阶梯
                if month_accumulated <= ladder_level_1:
                    # 第一阶梯
                    return (
                        day_tpq * price_tip_1
                        + day_ppq * price_peak_1
                        + day_npq * price_flat_1
                        + day_vpq * price_valley_1
                    )
                elif month_accumulated <= ladder_level_2:
                    # 第二阶梯
                    # 检查是否跨阶梯
                    if month_accumulated - day_ele_num <= ladder_level_1:
                        # 跨阶梯，需要按比例计算
                        ratio_1 = (
                            ladder_level_1 - (month_accumulated - day_ele_num)
                        ) / day_ele_num
                        ratio_2 = 1 - ratio_1

                        # 第一阶梯部分
                        cost_1 = (
                            day_tpq * price_tip_1 * ratio_1
                            + day_ppq * price_peak_1 * ratio_1
                            + day_npq * price_flat_1 * ratio_1
                            + day_vpq * price_valley_1 * ratio_1
                        )

                        # 第二阶梯部分
                        cost_2 = (
                            day_tpq * price_tip_2 * ratio_2
                            + day_ppq * price_peak_2 * ratio_2
                            + day_npq * price_flat_2 * ratio_2
                            + day_vpq * price_valley_2 * ratio_2
                        )

                        return cost_1 + cost_2
                    else:
                        # 完全在第二阶梯
                        return (
                            day_tpq * price_tip_2
                            + day_ppq * price_peak_2
                            + day_npq * price_flat_2
                            + day_vpq * price_valley_2
                        )
                else:
                    # 第三阶梯或跨阶梯
                    if month_accumulated - day_ele_num <= ladder_level_1:
                        # 跨越三个阶梯，需要按比例计算
                        remaining = month_accumulated - day_ele_num
                        ratio_1 = (ladder_level_1 - remaining) / day_ele_num
                        ratio_2 = (
                            (ladder_level_2 - ladder_level_1) / day_ele_num
                            if (ladder_level_2 - remaining) > 0
                            else 0
                        )
                        ratio_3 = 1 - ratio_1 - ratio_2

                        # 各阶梯部分费用
                        cost_1 = (
                            day_tpq * price_tip_1 * ratio_1
                            + day_ppq * price_peak_1 * ratio_1
                            + day_npq * price_flat_1 * ratio_1
                            + day_vpq * price_valley_1 * ratio_1
                        )

                        cost_2 = (
                            day_tpq * price_tip_2 * ratio_2
                            + day_ppq * price_peak_2 * ratio_2
                            + day_npq * price_flat_2 * ratio_2
                            + day_vpq * price_valley_2 * ratio_2
                        )

                        cost_3 = (
                            day_tpq * price_tip_3 * ratio_3
                            + day_ppq * price_peak_3 * ratio_3
                            + day_npq * price_flat_3 * ratio_3
                            + day_vpq * price_valley_3 * ratio_3
                        )

                        return cost_1 + cost_2 + cost_3
                    elif month_accumulated - day_ele_num <= ladder_level_2:
                        # 跨越第二、第三阶梯
                        ratio_2 = (
                            ladder_level_2 - (month_accumulated - day_ele_num)
                        ) / day_ele_num
                        ratio_3 = 1 - ratio_2

                        # 第二阶梯部分
                        cost_2 = (
                            day_tpq * price_tip_2 * ratio_2
                            + day_ppq * price_peak_2 * ratio_2
                            + day_npq * price_flat_2 * ratio_2
                            + day_vpq * price_valley_2 * ratio_2
                        )

                        # 第三阶梯部分
                        cost_3 = (
                            day_tpq * price_tip_3 * ratio_3
                            + day_ppq * price_peak_3 * ratio_3
                            + day_npq * price_flat_3 * ratio_3
                            + day_vpq * price_valley_3 * ratio_3
                        )

                        return cost_2 + cost_3
                    else:
                        # 完全在第三阶梯
                        return (
                            day_tpq * price_tip_3
                            + day_ppq * price_peak_3
                            + day_npq * price_flat_3
                            + day_vpq * price_valley_3
                        )

            elif standard == BILLING_STANDARD_MONTH_阶梯_峰平谷_变动价格:
                # 月阶梯+峰平谷+变动价格
                ladder_level_1 = self.config.get(CONF_LADDER_LEVEL_1, 180)
                ladder_level_2 = self.config.get(CONF_LADDER_LEVEL_2, 280)

                # 各阶梯的尖平谷价
                # 第一阶梯
                price_tip_1 = self.config.get(
                    f"{CONF_LADDER_PRICE_1}_{CONF_PRICE_TIP}", 0.5224
                )
                price_peak_1 = self.config.get(
                    f"{CONF_LADDER_PRICE_1}_{CONF_PRICE_PEAK}", 0.5224
                )
                price_flat_1 = self.config.get(
                    f"{CONF_LADDER_PRICE_1}_{CONF_PRICE_FLAT}", 0.5224
                )

                # 第二阶梯
                price_tip_2 = self.config.get(
                    f"{CONF_LADDER_PRICE_2}_{CONF_PRICE_TIP}", 0.6224
                )
                price_peak_2 = self.config.get(
                    f"{CONF_LADDER_PRICE_2}_{CONF_PRICE_PEAK}", 0.6224
                )
                price_flat_2 = self.config.get(
                    f"{CONF_LADDER_PRICE_2}_{CONF_PRICE_FLAT}", 0.6224
                )

                # 第三阶梯
                price_tip_3 = self.config.get(
                    f"{CONF_LADDER_PRICE_3}_{CONF_PRICE_TIP}", 0.8224
                )
                price_peak_3 = self.config.get(
                    f"{CONF_LADDER_PRICE_3}_{CONF_PRICE_PEAK}", 0.8224
                )
                price_flat_3 = self.config.get(
                    f"{CONF_LADDER_PRICE_3}_{CONF_PRICE_FLAT}", 0.8224
                )

                # 获取当前日期信息
                current_date = day_data["day"]
                current_year_month = current_date[:7]  # 格式：YYYY-MM
                month = int(current_date[5:7])  # 当前月份，1-12

                # 获取当月各阶梯的谷价
                valley_price_1 = self.config.get(
                    f"month_{month:02d}_ladder_1_valley", 0.2535
                )
                valley_price_2 = self.config.get(
                    f"month_{month:02d}_ladder_2_valley", 0.3535
                )
                valley_price_3 = self.config.get(
                    f"month_{month:02d}_ladder_3_valley", 0.5535
                )

                # 计算当月累计用电量（截至当前日期）
                month_accumulated = 0
                current_day = day_data["day"]
                if self.data is not None:
                    for data in self.data.get("dayList", []):
                        if (
                            data["day"].startswith(current_year_month)
                            and data["day"] <= current_day
                        ):
                            month_accumulated += data["dayEleNum"]

                # 根据累计用电量确定当前阶梯
                if month_accumulated <= ladder_level_1:
                    # 第一阶梯
                    return (
                        day_tpq * price_tip_1
                        + day_ppq * price_peak_1
                        + day_npq * price_flat_1
                        + day_vpq * valley_price_1
                    )
                elif month_accumulated <= ladder_level_2:
                    # 第二阶梯
                    # 检查是否跨阶梯
                    if month_accumulated - day_ele_num <= ladder_level_1:
                        # 跨阶梯，需要按比例计算
                        ratio_1 = (
                            ladder_level_1 - (month_accumulated - day_ele_num)
                        ) / day_ele_num
                        ratio_2 = 1 - ratio_1

                        # 第一阶梯部分
                        cost_1 = (
                            day_tpq * price_tip_1 * ratio_1
                            + day_ppq * price_peak_1 * ratio_1
                            + day_npq * price_flat_1 * ratio_1
                            + day_vpq * valley_price_1 * ratio_1
                        )

                        # 第二阶梯部分
                        cost_2 = (
                            day_tpq * price_tip_2 * ratio_2
                            + day_ppq * price_peak_2 * ratio_2
                            + day_npq * price_flat_2 * ratio_2
                            + day_vpq * valley_price_2 * ratio_2
                        )

                        return cost_1 + cost_2
                    else:
                        # 完全在第二阶梯
                        return (
                            day_tpq * price_tip_2
                            + day_ppq * price_peak_2
                            + day_npq * price_flat_2
                            + day_vpq * valley_price_2
                        )
                else:
                    # 第三阶梯或跨阶梯
                    if month_accumulated - day_ele_num <= ladder_level_1:
                        # 跨越三个阶梯，需要按比例计算
                        remaining = month_accumulated - day_ele_num
                        ratio_1 = (ladder_level_1 - remaining) / day_ele_num
                        ratio_2 = (
                            (ladder_level_2 - ladder_level_1) / day_ele_num
                            if (ladder_level_2 - remaining) > 0
                            else 0
                        )
                        ratio_3 = 1 - ratio_1 - ratio_2

                        # 各阶梯部分费用
                        cost_1 = (
                            day_tpq * price_tip_1 * ratio_1
                            + day_ppq * price_peak_1 * ratio_1
                            + day_npq * price_flat_1 * ratio_1
                            + day_vpq * valley_price_1 * ratio_1
                        )

                        cost_2 = (
                            day_tpq * price_tip_2 * ratio_2
                            + day_ppq * price_peak_2 * ratio_2
                            + day_npq * price_flat_2 * ratio_2
                            + day_vpq * valley_price_2 * ratio_2
                        )

                        cost_3 = (
                            day_tpq * price_tip_3 * ratio_3
                            + day_ppq * price_peak_3 * ratio_3
                            + day_npq * price_flat_3 * ratio_3
                            + day_vpq * valley_price_3 * ratio_3
                        )

                        return cost_1 + cost_2 + cost_3
                    elif month_accumulated - day_ele_num <= ladder_level_2:
                        # 跨越第二、第三阶梯
                        ratio_2 = (
                            ladder_level_2 - (month_accumulated - day_ele_num)
                        ) / day_ele_num
                        ratio_3 = 1 - ratio_2

                        # 第二阶梯部分
                        cost_2 = (
                            day_tpq * price_tip_2 * ratio_2
                            + day_ppq * price_peak_2 * ratio_2
                            + day_npq * price_flat_2 * ratio_2
                            + day_vpq * valley_price_2 * ratio_2
                        )

                        # 第三阶梯部分
                        cost_3 = (
                            day_tpq * price_tip_3 * ratio_3
                            + day_ppq * price_peak_3 * ratio_3
                            + day_npq * price_flat_3 * ratio_3
                            + day_vpq * valley_price_3 * ratio_3
                        )

                        return cost_2 + cost_3
                    else:
                        # 完全在第三阶梯
                        return (
                            day_tpq * price_tip_3
                            + day_ppq * price_peak_3
                            + day_npq * price_flat_3
                            + day_vpq * valley_price_3
                        )

            elif standard == BILLING_STANDARD_OTHER_平均单价:
                # 其他-平均单价
                avg_price = self.config.get(CONF_AVERAGE_PRICE, 0.6)
                return day_ele_num * avg_price

            return 0
        except Exception as ex:
            _LOGGER.error("Error calculating cost by standard: %s", ex)
            return 0

    def _process_month_data(self, day_list, month_list):
        """Process and update monthly data."""
        try:
            # 确保月列表按时间降序排序（最新的月份在前）
            month_list = sorted(month_list, key=lambda x: x["month"], reverse=True)

            # 增加当月数据（如果不存在）
            now = datetime.now()
            current_month_str = now.strftime("%Y-%m")

            if not any(item["month"] == current_month_str for item in month_list):
                # 计算当月数据
                current_month_data = self._calculate_month_data(
                    day_list, current_month_str
                )
                if current_month_data:
                    month_list.insert(0, current_month_data)

            # 增加上月数据（如果不存在）
            if now.month == 1:
                # 如果当前是1月，上月是上一年12月
                prev_month = datetime(now.year - 1, 12, 1)
            else:
                # 其他情况，上月是当前年的上个月
                prev_month = datetime(now.year, now.month - 1, 1)
            prev_month_str = prev_month.strftime("%Y-%m")

            if not any(item["month"] == prev_month_str for item in month_list):
                # 计算上月数据
                prev_month_data = self._calculate_month_data(day_list, prev_month_str)
                if prev_month_data:
                    # 由于已经是降序排列，上月应该插入到当月之后
                    if month_list and month_list[0]["month"] == current_month_str:
                        month_list.insert(1, prev_month_data)
                    else:
                        month_list.insert(0, prev_month_data)

            # 处理每个月的数据，只保留需要的字段并转换为数字格式
            processed_month_list = []
            for month_item in month_list:
                month_str = month_item["month"]
                year_month = month_str.replace("-", "")

                # 从日数据中计算月度分时电量
                days_in_month = [
                    day
                    for day in day_list
                    if day["day"].replace("-", "")[:6] == year_month
                ]
                month_tpq = sum(float(day.get("dayTPq", 0)) for day in days_in_month)
                month_ppq = sum(float(day.get("dayPPq", 0)) for day in days_in_month)
                month_npq = sum(float(day.get("dayNPq", 0)) for day in days_in_month)
                month_vpq = sum(float(day.get("dayVPq", 0)) for day in days_in_month)

                # 创建新的月数据对象，只包含需要的字段
                new_month_item = {
                    "month": month_str,
                    "monthEleNum": float(month_item.get("monthEleNum", 0)),
                    "monthEleCost": float(month_item.get("monthEleCost", 0)),
                    "monthTPq": round(month_tpq, 2),
                    "monthPPq": round(month_ppq, 2),
                    "monthNPq": round(month_npq, 2),
                    "monthVPq": round(month_vpq, 2),
                }
                processed_month_list.append(new_month_item)

            return processed_month_list
        except Exception as ex:
            _LOGGER.error("Error processing month data: %s", ex)
            return month_list

    def _calculate_month_data(self, day_list, month_str):
        """Calculate month data from daily data."""
        try:
            year_month = month_str.replace("-", "")
            days_in_month = [
                day for day in day_list if day["day"].replace("-", "")[:6] == year_month
            ]

            if not days_in_month:
                return None

            month_ele_num = sum(float(day.get("dayEleNum", 0)) for day in days_in_month)
            month_tpq = sum(float(day.get("dayTPq", 0)) for day in days_in_month)
            month_ppq = sum(float(day.get("dayPPq", 0)) for day in days_in_month)
            month_npq = sum(float(day.get("dayNPq", 0)) for day in days_in_month)
            month_vpq = sum(float(day.get("dayVPq", 0)) for day in days_in_month)
            month_ele_cost = sum(
                float(day.get("dayEleCost", 0)) for day in days_in_month
            )

            return {
                "month": month_str,
                "monthEleNum": float(round(month_ele_num, 2)),
                "monthEleCost": float(round(month_ele_cost, 2)),
                "monthTPq": round(month_tpq, 2),
                "monthPPq": round(month_ppq, 2),
                "monthNPq": round(month_npq, 2),
                "monthVPq": round(month_vpq, 2),
            }
        except Exception as ex:
            _LOGGER.error("Error calculating month data: %s", ex)
            return None

    def _process_year_data(self, month_list):
        """Process and calculate yearly data from monthly data."""
        try:
            year_map = {}  # 用于按年份暂存数据

            for month_data in month_list:
                year = month_data["month"].split("-")[0]
                if year not in year_map:
                    year_map[year] = {
                        "year": year,
                        "yearEleNum": 0,
                        "yearEleCost": 0,
                        "yearTPq": 0,
                        "yearPPq": 0,
                        "yearNPq": 0,
                        "yearVPq": 0,
                    }

                # 由于月数据已经是数字格式，不需要再转换
                year_map[year]["yearEleNum"] += month_data.get("monthEleNum", 0)
                year_map[year]["yearEleCost"] += month_data.get("monthEleCost", 0)
                year_map[year]["yearTPq"] += month_data.get("monthTPq", 0)
                year_map[year]["yearPPq"] += month_data.get("monthPPq", 0)
                year_map[year]["yearNPq"] += month_data.get("monthNPq", 0)
                year_map[year]["yearVPq"] += month_data.get("monthVPq", 0)

            # 四舍五入到两位小数
            year_list = []
            for year_data in year_map.values():
                year_list.append(
                    {
                        "year": year_data["year"],
                        "yearEleNum": round(year_data["yearEleNum"], 2),
                        "yearEleCost": round(year_data["yearEleCost"], 2),
                        "yearTPq": round(year_data["yearTPq"], 2),
                        "yearPPq": round(year_data["yearPPq"], 2),
                        "yearNPq": round(year_data["yearNPq"], 2),
                        "yearVPq": round(year_data["yearVPq"], 2),
                    }
                )

            # 按年份倒序排列
            return sorted(year_list, key=lambda x: x["year"], reverse=True)
        except Exception as ex:
            _LOGGER.error("Error processing year data: %s", ex)
            return []


class StateGridInfoSensor(SensorEntity):
    """Representation of a State Grid Info sensor."""

    def __init__(self, coordinator, config):
        """Initialize the sensor."""
        self.coordinator = coordinator
        self.config = config
        consumer_number = config.get(CONF_CONSUMER_NUMBER, "")
        self.entity_id = f"sensor.state_grid_{consumer_number}"
        self._attr_unique_id = f"state_grid_{consumer_number}"
        self._attr_icon = "mdi:flash"

        self._attr_name = f"国家电网 {consumer_number}"
        self._attr_native_unit_of_measurement = "元"
        self._consumer_number = consumer_number

    @property
    def device_info(self):
        """Return device info."""
        # 动态获取户名：优先从数据中获取，如果没有则从配置中获取
        consumer_name = ""
        if self.coordinator.data and "consumer_name" in self.coordinator.data:
            consumer_name = self.coordinator.data.get("consumer_name", "")
        elif CONF_CONSUMER_NAME in self.config:
            consumer_name = self.config.get(CONF_CONSUMER_NAME, "")

        return {
            "identifiers": {(DOMAIN, f"state_grid_{self._consumer_number}")},
            "name": f"国家电网 {self._consumer_number}",
            "manufacturer": "国家电网",
            "model": f"户名:{consumer_name}",
        }

    @property
    def available(self):
        """Return if entity is available."""
        # 检查数据是否有效
        if not self.coordinator.data:
            return False

        # 检查数据是否过期（超过1小时）
        time_diff = datetime.now() - self.coordinator.last_update_time
        if time_diff.total_seconds() > 3600:  # 1小时
            return False

        return True

    @property
    def native_value(self):
        """Return the state of the sensor."""
        if self.coordinator.data:
            return self.coordinator.data.get("balance", 0)
        return 0

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        attrs = {}

        # 添加基本属性
        if self.coordinator.data:
            # 计算日均消费（最近7天的平均值）和剩余天数
            day_list = self.coordinator.data.get("dayList", [])
            if day_list:
                # 确保按日期降序排列（最新的在前面）
                sorted_days = sorted(day_list, key=lambda x: x["day"], reverse=True)

                # 获取最近7天的数据
                recent_days = sorted_days[:7]

                if recent_days:
                    # 计算日均消费
                    daily_costs = [day.get("dayEleCost", 0) for day in recent_days]
                    avg_daily_cost = sum(daily_costs) / len(daily_costs)

                    # 计算剩余天数
                    balance = self.coordinator.data.get("balance", 0)
                    if avg_daily_cost > 0:
                        estimated_days = balance / avg_daily_cost

                        # 计算最新用电日期与今天的差值
                        try:
                            latest_day = sorted_days[0]["day"]
                            latest_date = datetime.strptime(latest_day, "%Y-%m-%d")
                            today = datetime.now().replace(
                                hour=0, minute=0, second=0, microsecond=0
                            )
                            days_since_latest = (today - latest_date).days

                            # 剩余天数 = 预计使用天数 - (今天日期-最新用电日期)
                            remaining_days = max(0, estimated_days - days_since_latest)
                            # 向上取整
                            import math

                            # 将日均消费和剩余天数放在最前面
                            attrs["日均消费"] = round(avg_daily_cost, 2)
                            attrs["剩余天数"] = math.ceil(remaining_days)
                            attrs["预付费"] = (
                                "是"
                                if self.config.get(CONF_IS_PREPAID, False)
                                else "否"
                            )

                        except (ValueError, IndexError) as e:
                            _LOGGER.error("计算剩余天数时出错: %s", e)

            # 添加其他基本属性
            attrs.update(
                {
                    "date": self.coordinator.data.get("date", ""),
                    "daylist": self.coordinator.data.get("dayList", []),
                    "monthlist": self.coordinator.data.get("monthList", []),
                    "yearlist": self.coordinator.data.get("yearList", []),
                }
            )

        # 添加计费标准配置
        billing_standard_attrs = {}
        billing_standard = self.config.get(CONF_BILLING_STANDARD, "")

        # 获取阶梯档和累计用电量信息
        ladder_info = self._get_ladder_info(billing_standard)

        # 将计费标准转换为中文显示
        billing_standard_map = {
            BILLING_STANDARD_YEAR_阶梯: "年阶梯",
            BILLING_STANDARD_YEAR_阶梯_峰平谷: "年阶梯峰平谷",
            BILLING_STANDARD_MONTH_阶梯: "月阶梯",
            BILLING_STANDARD_MONTH_阶梯_峰平谷: "月阶梯峰平谷",
            BILLING_STANDARD_MONTH_阶梯_峰平谷_变动价格: "月阶梯峰平谷变动价格",
            BILLING_STANDARD_OTHER_平均单价: "平均单价",
        }
        billing_standard_chinese = billing_standard_map.get(
            billing_standard, billing_standard
        )
        billing_standard_attrs["计费标准"] = billing_standard_chinese

        # 将阶梯信息添加到计费标准属性中
        billing_standard_attrs.update(ladder_info)

        # 根据不同计费标准添加相应配置
        if billing_standard in [
            BILLING_STANDARD_YEAR_阶梯,
            BILLING_STANDARD_YEAR_阶梯_峰平谷,
        ]:
            billing_standard_attrs["年阶梯第2档起始电量"] = self.config.get(
                CONF_LADDER_LEVEL_1, 2160
            )
            billing_standard_attrs["年阶梯第3档起始电量"] = self.config.get(
                CONF_LADDER_LEVEL_2, 4200
            )

            # 获取年阶梯起始日期并计算当前年份的起始和结束日期
            year_ladder_start = self.config.get(CONF_YEAR_LADDER_START, "0101")
            current_date = datetime.now()
            current_year = current_date.year
            current_month = current_date.month
            current_day_int = current_date.day

            # 解析年阶梯起始日期
            start_month = int(year_ladder_start[:2])
            start_day = int(year_ladder_start[2:])

            # 推算年阶梯起始日期的年份
            if (current_month < start_month) or (
                current_month == start_month and current_day_int < start_day
            ):
                ladder_year = current_year - 1
            else:
                ladder_year = current_year

            # 格式化年阶梯起始日期和结束日期
            year_ladder_start_date_formatted = (
                f"{ladder_year}.{year_ladder_start[:2]}.{year_ladder_start[2:]}"
            )

            # 计算结束日期：次年起始日期减1天
            start_date_next_year = datetime(ladder_year + 1, start_month, start_day)
            end_date = start_date_next_year - timedelta(days=1)
            year_ladder_end_date_formatted = (
                f"{end_date.year}.{end_date.month:02d}.{end_date.day:02d}"
            )

            billing_standard_attrs["当前年阶梯起始日期"] = (
                year_ladder_start_date_formatted
            )
            billing_standard_attrs["当前年阶梯结束日期"] = (
                year_ladder_end_date_formatted
            )

            if billing_standard == BILLING_STANDARD_YEAR_阶梯:
                billing_standard_attrs["年阶梯第1档电价"] = self.config.get(
                    CONF_LADDER_PRICE_1, 0.4983
                )
                billing_standard_attrs["年阶梯第2档电价"] = self.config.get(
                    CONF_LADDER_PRICE_2, 0.5483
                )
                billing_standard_attrs["年阶梯第3档电价"] = self.config.get(
                    CONF_LADDER_PRICE_3, 0.7983
                )
            elif billing_standard == BILLING_STANDARD_YEAR_阶梯_峰平谷:
                billing_standard_attrs["年阶梯第1档尖电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_1}_{CONF_PRICE_TIP}", 0.5483
                )
                billing_standard_attrs["年阶梯第1档峰电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_1}_{CONF_PRICE_PEAK}", 0.5483
                )
                billing_standard_attrs["年阶梯第1档平电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_1}_{CONF_PRICE_FLAT}", 0.5483
                )
                billing_standard_attrs["年阶梯第1档谷电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_1}_{CONF_PRICE_VALLEY}", 0.2983
                )

                billing_standard_attrs["年阶梯第2档尖电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_2}_{CONF_PRICE_TIP}", 0.5983
                )
                billing_standard_attrs["年阶梯第2档峰电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_2}_{CONF_PRICE_PEAK}", 0.5983
                )
                billing_standard_attrs["年阶梯第2档平电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_2}_{CONF_PRICE_FLAT}", 0.5983
                )
                billing_standard_attrs["年阶梯第2档谷电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_2}_{CONF_PRICE_VALLEY}", 0.3483
                )

                billing_standard_attrs["年阶梯第3档尖电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_3}_{CONF_PRICE_TIP}", 0.8483
                )
                billing_standard_attrs["年阶梯第3档峰电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_3}_{CONF_PRICE_PEAK}", 0.8483
                )
                billing_standard_attrs["年阶梯第3档平电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_3}_{CONF_PRICE_FLAT}", 0.8483
                )
                billing_standard_attrs["年阶梯第3档谷电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_3}_{CONF_PRICE_VALLEY}", 0.5983
                )

        elif billing_standard in [
            BILLING_STANDARD_MONTH_阶梯,
            BILLING_STANDARD_MONTH_阶梯_峰平谷,
            BILLING_STANDARD_MONTH_阶梯_峰平谷_变动价格,
        ]:
            billing_standard_attrs["月阶梯第2档起始电量"] = self.config.get(
                CONF_LADDER_LEVEL_1, 180
            )
            billing_standard_attrs["月阶梯第3档起始电量"] = self.config.get(
                CONF_LADDER_LEVEL_2, 280
            )

            if billing_standard == BILLING_STANDARD_MONTH_阶梯:
                billing_standard_attrs["月阶梯第1档电价"] = self.config.get(
                    CONF_LADDER_PRICE_1, 0.5224
                )
                billing_standard_attrs["月阶梯第2档电价"] = self.config.get(
                    CONF_LADDER_PRICE_2, 0.6224
                )
                billing_standard_attrs["月阶梯第3档电价"] = self.config.get(
                    CONF_LADDER_PRICE_3, 0.8334
                )
            elif billing_standard == BILLING_STANDARD_MONTH_阶梯_峰平谷:
                billing_standard_attrs["月阶梯第1档尖电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_1}_{CONF_PRICE_TIP}", 0.5283
                )
                billing_standard_attrs["月阶梯第1档峰电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_1}_{CONF_PRICE_PEAK}", 0.5283
                )
                billing_standard_attrs["月阶梯第1档平电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_1}_{CONF_PRICE_FLAT}", 0.5283
                )
                billing_standard_attrs["月阶梯第1档谷电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_1}_{CONF_PRICE_VALLEY}", 0.5283
                )

                billing_standard_attrs["月阶梯第2档尖电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_2}_{CONF_PRICE_TIP}", 0.5783
                )
                billing_standard_attrs["月阶梯第2档峰电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_2}_{CONF_PRICE_PEAK}", 0.5783
                )
                billing_standard_attrs["月阶梯第2档平电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_2}_{CONF_PRICE_FLAT}", 0.5783
                )
                billing_standard_attrs["月阶梯第2档谷电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_2}_{CONF_PRICE_VALLEY}", 0.5783
                )

                billing_standard_attrs["月阶梯第3档尖电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_3}_{CONF_PRICE_TIP}", 0.8283
                )
                billing_standard_attrs["月阶梯第3档峰电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_3}_{CONF_PRICE_PEAK}", 0.8283
                )
                billing_standard_attrs["月阶梯第3档平电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_3}_{CONF_PRICE_FLAT}", 0.8283
                )
                billing_standard_attrs["月阶梯第3档谷电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_3}_{CONF_PRICE_VALLEY}", 0.8283
                )
            elif billing_standard == BILLING_STANDARD_MONTH_阶梯_峰平谷_变动价格:
                billing_standard_attrs["月阶梯第1档尖电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_1}_{CONF_PRICE_TIP}", 0.5224
                )
                billing_standard_attrs["月阶梯第1档峰电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_1}_{CONF_PRICE_PEAK}", 0.5224
                )
                billing_standard_attrs["月阶梯第1档平电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_1}_{CONF_PRICE_FLAT}", 0.5224
                )

                billing_standard_attrs["月阶梯第2档尖电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_2}_{CONF_PRICE_TIP}", 0.6224
                )
                billing_standard_attrs["月阶梯第2档峰电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_2}_{CONF_PRICE_PEAK}", 0.6224
                )
                billing_standard_attrs["月阶梯第2档平电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_2}_{CONF_PRICE_FLAT}", 0.6224
                )

                billing_standard_attrs["月阶梯第3档尖电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_3}_{CONF_PRICE_TIP}", 0.8224
                )
                billing_standard_attrs["月阶梯第3档峰电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_3}_{CONF_PRICE_PEAK}", 0.8224
                )
                billing_standard_attrs["月阶梯第3档平电价"] = self.config.get(
                    f"{CONF_LADDER_PRICE_3}_{CONF_PRICE_FLAT}", 0.8224
                )

                # 添加月份变动的谷价
                for month in range(1, 13):
                    valley_price_1 = self.config.get(
                        f"month_{month:02d}_ladder_1_valley"
                    )
                    valley_price_2 = self.config.get(
                        f"month_{month:02d}_ladder_2_valley"
                    )
                    valley_price_3 = self.config.get(
                        f"month_{month:02d}_ladder_3_valley"
                    )
                    if valley_price_1 is not None:
                        billing_standard_attrs[f"{month}月阶梯第1档谷电价"] = (
                            valley_price_1
                        )
                    if valley_price_2 is not None:
                        billing_standard_attrs[f"{month}月阶梯第2档谷电价"] = (
                            valley_price_2
                        )
                    if valley_price_3 is not None:
                        billing_standard_attrs[f"{month}月阶梯第3档谷电价"] = (
                            valley_price_3
                        )

        elif billing_standard == BILLING_STANDARD_OTHER_平均单价:
            billing_standard_attrs["平均单价"] = self.config.get(
                CONF_AVERAGE_PRICE, 0.6
            )

        # 添加计费标准属性到主属性
        attrs["计费标准"] = billing_standard_attrs

        # 添加状态信息
        attrs["数据源"] = self.config.get(CONF_DATA_SOURCE, "unknown")
        attrs["最后同步日期"] = self.coordinator.last_update_time.strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        return attrs

    def _get_ladder_info(self, billing_standard):
        """获取当前阶梯档和累计用电量信息"""
        try:
            if not self.coordinator.data or not self.coordinator.data.get("dayList"):
                return {}

            day_list = self.coordinator.data.get("dayList", [])
            if not day_list:
                return {}

            # 获取最新的日期（假设dayList已经按日期排序）
            latest_day_data = None
            if day_list:
                # 找到最新的一天数据
                sorted_days = sorted(day_list, key=lambda x: x["day"], reverse=True)
                latest_day_data = sorted_days[0]

            if not latest_day_data:
                return {}

            ladder_info = {}

            if billing_standard in [
                BILLING_STANDARD_YEAR_阶梯,
                BILLING_STANDARD_YEAR_阶梯_峰平谷,
            ]:
                # 年阶梯计算
                ladder_level_1 = self.config.get(CONF_LADDER_LEVEL_1, 2160)
                ladder_level_2 = self.config.get(CONF_LADDER_LEVEL_2, 4200)

                # 获取年阶梯起始日期
                year_ladder_start = self.config.get(CONF_YEAR_LADDER_START, "0101")
                current_day = latest_day_data["day"]
                current_year = int(current_day.split("-")[0])
                current_month = int(current_day.split("-")[1])
                current_day_int = int(current_day.split("-")[2])

                # 解析年阶梯起始日期
                start_month = int(year_ladder_start[:2])
                start_day = int(year_ladder_start[2:])

                # 推算年阶梯起始日期的年份
                if (current_month < start_month) or (
                    current_month == start_month and current_day_int < start_day
                ):
                    ladder_year = current_year - 1
                else:
                    ladder_year = current_year

                # 格式化年阶梯起始日期
                year_ladder_start_date = (
                    f"{ladder_year}-{year_ladder_start[:2]}-{year_ladder_start[2:]}"
                )

                # 计算年累计用电量
                year_accumulated = 0
                for data in day_list:
                    if (
                        data["day"] >= year_ladder_start_date
                        and data["day"] <= current_day
                    ):
                        year_accumulated += data["dayEleNum"]

                # 确定当前阶梯档
                if year_accumulated <= ladder_level_1:
                    current_ladder = "第1档"
                elif year_accumulated <= ladder_level_2:
                    current_ladder = "第2档"
                else:
                    current_ladder = "第3档"

                ladder_info["当前年阶梯档"] = current_ladder
                ladder_info["年阶梯累计用电量"] = round(year_accumulated, 2)

            elif billing_standard in [
                BILLING_STANDARD_MONTH_阶梯,
                BILLING_STANDARD_MONTH_阶梯_峰平谷,
                BILLING_STANDARD_MONTH_阶梯_峰平谷_变动价格,
            ]:
                # 月阶梯计算
                ladder_level_1 = self.config.get(CONF_LADDER_LEVEL_1, 180)
                ladder_level_2 = self.config.get(CONF_LADDER_LEVEL_2, 280)

                # 获取当前月份
                current_day = latest_day_data["day"]
                current_year_month = current_day[:7]  # YYYY-MM

                # 计算月累计用电量
                month_accumulated = 0
                for data in day_list:
                    if (
                        data["day"].startswith(current_year_month)
                        and data["day"] <= current_day
                    ):
                        month_accumulated += data["dayEleNum"]

                # 确定当前阶梯档
                if month_accumulated <= ladder_level_1:
                    current_ladder = "第1档"
                elif month_accumulated <= ladder_level_2:
                    current_ladder = "第2档"
                else:
                    current_ladder = "第3档"

                ladder_info["当前月阶梯档"] = current_ladder
                ladder_info["月阶梯累计用电量"] = round(month_accumulated, 2)

            return ladder_info

        except Exception as ex:
            _LOGGER.error("Error getting ladder info: %s", ex)
            return {}
