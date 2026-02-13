"""SQLite database manager for State Grid Info integration."""
import logging
import os
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any

_LOGGER = logging.getLogger(__name__)


class StateGridDatabase:
    """SQLite database manager for electricity data storage."""

    def __init__(self, hass, db_path: Optional[str] = None):
        """Initialize the database manager."""
        self.hass = hass
        
        if db_path is None:
            self.db_path = os.path.join(hass.config.config_dir, "state_grid_data.db")
        else:
            self.db_path = db_path
        
        self._connection: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection with row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Initialize database tables."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_electricity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cons_no TEXT NOT NULL,
                    day TEXT NOT NULL,
                    day_ele_num REAL DEFAULT 0,
                    day_ele_cost REAL DEFAULT 0,
                    day_tpq REAL DEFAULT 0,
                    day_ppq REAL DEFAULT 0,
                    day_npq REAL DEFAULT 0,
                    day_vpq REAL DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(cons_no, day)
                )
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_daily_cons_no 
                ON daily_electricity(cons_no)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_daily_day 
                ON daily_electricity(day)
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS monthly_electricity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cons_no TEXT NOT NULL,
                    month TEXT NOT NULL,
                    month_ele_num REAL DEFAULT 0,
                    month_ele_cost REAL DEFAULT 0,
                    month_tpq REAL DEFAULT 0,
                    month_ppq REAL DEFAULT 0,
                    month_npq REAL DEFAULT 0,
                    month_vpq REAL DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(cons_no, month)
                )
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_monthly_cons_no 
                ON monthly_electricity(cons_no)
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS metadata (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cons_no TEXT NOT NULL,
                    balance REAL DEFAULT 0,
                    last_update TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(cons_no)
                )
            """)
            
            conn.commit()
            conn.close()
            
            _LOGGER.info("SQLite数据库初始化成功: %s", self.db_path)
        except Exception as ex:
            _LOGGER.error("SQLite数据库初始化失败: %s", ex)

    def save_daily_data(self, cons_no: str, day_list: List[Dict[str, Any]]) -> int:
        """Save daily electricity data to database."""
        if not day_list:
            return 0
        
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            saved_count = 0
            for item in day_list:
                day = item.get("day", "")
                if not day:
                    continue
                
                cursor.execute("""
                    INSERT OR REPLACE INTO daily_electricity 
                    (cons_no, day, day_ele_num, day_ele_cost, day_tpq, day_ppq, day_npq, day_vpq, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    cons_no,
                    day,
                    float(item.get("dayEleNum", 0)),
                    float(item.get("dayEleCost", 0)),
                    float(item.get("dayTPq", 0)),
                    float(item.get("dayPPq", 0)),
                    float(item.get("dayNPq", 0)),
                    float(item.get("dayVPq", 0)),
                ))
                saved_count += 1
            
            conn.commit()
            conn.close()
            
            _LOGGER.debug("保存了 %d 条日用电数据到数据库", saved_count)
            return saved_count
        except Exception as ex:
            _LOGGER.error("保存日用电数据失败: %s", ex)
            return 0

    def save_monthly_data(self, cons_no: str, month_list: List[Dict[str, Any]]) -> int:
        """Save monthly electricity data to database."""
        if not month_list:
            return 0
        
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            saved_count = 0
            for item in month_list:
                month = item.get("month", "")
                if not month:
                    continue
                
                cursor.execute("""
                    INSERT OR REPLACE INTO monthly_electricity 
                    (cons_no, month, month_ele_num, month_ele_cost, month_tpq, month_ppq, month_npq, month_vpq, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    cons_no,
                    month,
                    float(item.get("monthEleNum", 0)),
                    float(item.get("monthEleCost", 0)),
                    float(item.get("monthTPq", 0)),
                    float(item.get("monthPPq", 0)),
                    float(item.get("monthNPq", 0)),
                    float(item.get("monthVPq", 0)),
                ))
                saved_count += 1
            
            conn.commit()
            conn.close()
            
            _LOGGER.debug("保存了 %d 条月用电数据到数据库", saved_count)
            return saved_count
        except Exception as ex:
            _LOGGER.error("保存月用电数据失败: %s", ex)
            return 0

    def save_metadata(self, cons_no: str, balance: float, last_update: str) -> bool:
        """Save metadata (balance, last update time) to database."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO metadata 
                (cons_no, balance, last_update, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """, (cons_no, balance, last_update))
            
            conn.commit()
            conn.close()
            
            _LOGGER.debug("保存元数据: 户号=%s, 余额=%.2f", cons_no, balance)
            return True
        except Exception as ex:
            _LOGGER.error("保存元数据失败: %s", ex)
            return False

    def get_daily_data(self, cons_no: str, days: int = 365) -> List[Dict[str, Any]]:
        """Get daily electricity data from database."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            
            cursor.execute("""
                SELECT day, day_ele_num, day_ele_cost, day_tpq, day_ppq, day_npq, day_vpq
                FROM daily_electricity
                WHERE cons_no = ? AND day >= ?
                ORDER BY day ASC
            """, (cons_no, cutoff_date))
            
            rows = cursor.fetchall()
            conn.close()
            
            result = []
            for row in rows:
                result.append({
                    "day": row["day"],
                    "dayEleNum": row["day_ele_num"],
                    "dayEleCost": row["day_ele_cost"],
                    "dayTPq": row["day_tpq"],
                    "dayPPq": row["day_ppq"],
                    "dayNPq": row["day_npq"],
                    "dayVPq": row["day_vpq"],
                })
            
            _LOGGER.debug("从数据库获取了 %d 条日用电数据", len(result))
            return result
        except Exception as ex:
            _LOGGER.error("获取日用电数据失败: %s", ex)
            return []

    def get_monthly_data(self, cons_no: str) -> List[Dict[str, Any]]:
        """Get monthly electricity data from database."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT month, month_ele_num, month_ele_cost, month_tpq, month_ppq, month_npq, month_vpq
                FROM monthly_electricity
                WHERE cons_no = ?
                ORDER BY month ASC
            """, (cons_no,))
            
            rows = cursor.fetchall()
            conn.close()
            
            result = []
            for row in rows:
                result.append({
                    "month": row["month"],
                    "monthEleNum": row["month_ele_num"],
                    "monthEleCost": row["month_ele_cost"],
                    "monthTPq": row["month_tpq"],
                    "monthPPq": row["month_ppq"],
                    "monthNPq": row["month_npq"],
                    "monthVPq": row["month_vpq"],
                })
            
            return result
        except Exception as ex:
            _LOGGER.error("获取月用电数据失败: %s", ex)
            return []

    def get_metadata(self, cons_no: str) -> Optional[Dict[str, Any]]:
        """Get metadata from database."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT balance, last_update
                FROM metadata
                WHERE cons_no = ?
            """, (cons_no,))
            
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return {
                    "balance": row["balance"],
                    "last_update": row["last_update"],
                }
            return None
        except Exception as ex:
            _LOGGER.error("获取元数据失败: %s", ex)
            return None

    def get_all_data(self, cons_no: str, days: int = 365) -> Dict[str, Any]:
        """Get all electricity data for a consumer."""
        daily_data = self.get_daily_data(cons_no, days)
        monthly_data = self.get_monthly_data(cons_no)
        metadata = self.get_metadata(cons_no)
        
        return {
            "dayList": daily_data,
            "monthList": monthly_data,
            "balance": metadata.get("balance", 0) if metadata else 0,
            "last_update": metadata.get("last_update", "") if metadata else "",
        }

    def cleanup_old_data(self, cons_no: str, retention_days: int = 365):
        """Clean up old data beyond retention period."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cutoff_date = (datetime.now() - timedelta(days=retention_days)).strftime("%Y-%m-%d")
            
            cursor.execute("""
                DELETE FROM daily_electricity
                WHERE cons_no = ? AND day < ?
            """, (cons_no, cutoff_date))
            
            deleted_count = cursor.rowcount
            conn.commit()
            conn.close()
            
            if deleted_count > 0:
                _LOGGER.info("清理了 %d 条过期数据", deleted_count)
        except Exception as ex:
            _LOGGER.error("清理过期数据失败: %s", ex)

    def get_daily_count(self, cons_no: str) -> int:
        """Get count of daily records for a consumer."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT COUNT(*) as count
                FROM daily_electricity
                WHERE cons_no = ?
            """, (cons_no,))
            
            row = cursor.fetchone()
            conn.close()
            
            return row["count"] if row else 0
        except Exception as ex:
            _LOGGER.error("获取数据计数失败: %s", ex)
            return 0

    def close(self):
        """Close database connection."""
        if self._connection:
            self._connection.close()
            self._connection = None
            _LOGGER.info("数据库连接已关闭")
