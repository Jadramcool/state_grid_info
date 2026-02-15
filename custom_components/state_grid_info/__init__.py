"""国家电网辅助信息集成."""

import logging
import os
import homeassistant.helpers.config_validation as cv
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.frontend import add_extra_js_url

from .const import DOMAIN
from .database import StateGridDatabase

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]

CARD_FILENAME = "state-grid-card.js"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the State Grid Info component."""
    await setup_state_grid_card(hass)
    return True


async def setup_state_grid_card(hass: HomeAssistant) -> bool:
    """设置国家电网卡片前端资源."""
    state_grid_card_path = "/state_grid_info-local"
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                state_grid_card_path,
                hass.config.path("custom_components/state_grid_info/www"),
                False,
            )
        ]
    )
    _LOGGER.debug(
        f"register_static_path: {state_grid_card_path + ':custom_components/state_grid_info/www'}"
    )
    add_extra_js_url(hass, state_grid_card_path + f"/state-grid-card.js")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up State Grid Info from a config entry."""
    import asyncio
    from datetime import timedelta

    hass.data.setdefault(DOMAIN, {})

    db = await hass.async_add_executor_job(StateGridDatabase, hass)

    hass.data[DOMAIN][entry.entry_id] = {
        "config": entry.data,
        "entities": [],
        "database": db,
    }

    await setup_state_grid_card(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def refresh_data_periodically():
        """定期刷新数据，确保数据更新"""
        try:
            while True:
                await asyncio.sleep(300)

                coordinator = None
                for entity in (
                    hass.data.get(DOMAIN, {})
                    .get(entry.entry_id, {})
                    .get("entities", [])
                ):
                    if hasattr(entity, "coordinator"):
                        coordinator = entity.coordinator
                        break

                if coordinator:
                    _LOGGER.info("执行定期数据刷新")
                    await coordinator.async_refresh()
                else:
                    _LOGGER.warning("未找到协调器，无法执行定期刷新")
        except asyncio.CancelledError:
            pass
        except Exception as ex:
            _LOGGER.error("定期刷新任务出错: %s", ex)

    refresh_task = hass.loop.create_task(refresh_data_periodically())

    hass.data[DOMAIN][entry.entry_id]["refresh_task"] = refresh_task

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator = None
    for entity in hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("entities", []):
        if hasattr(entity, "coordinator"):
            coordinator = entity.coordinator
            break

    refresh_task = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("refresh_task")
    if refresh_task:
        _LOGGER.info("取消定期刷新任务")
        refresh_task.cancel()
        try:
            await refresh_task
        except Exception:
            pass

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if coordinator and hasattr(coordinator, "async_unload"):
        _LOGGER.info("清理协调器资源")
        await coordinator.async_unload()

    db = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("database")
    if db:
        db.close()

    if unload_ok:
        if entry.entry_id in hass.data.get(DOMAIN, {}):
            hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
