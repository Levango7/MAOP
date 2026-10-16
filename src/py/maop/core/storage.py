#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据持久化存储层
基于SQLite实现，替换原有内存存储，支持服务重启数据不丢失
"""

import sqlite3
import json
import time
from typing import Optional, Dict, List
from contextlib import contextmanager


class SQLiteStorage:
    """SQLite存储实现"""
    def __init__(self, db_path: str = "maop.db"):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _get_conn(self):
        """获取数据库连接上下文"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        """初始化数据库表结构"""
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS workflows (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    yaml_content TEXT,
                    created_at REAL,
                    updated_at REAL
                );
                
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    detail TEXT,
                    result BOOLEAN,
                    error_message TEXT,
                    timestamp REAL,
                    ip_address TEXT
                );
                
                CREATE TABLE IF NOT EXISTS route_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query TEXT,
                    selected_model TEXT,
                    task_class TEXT,
                    remaining_budget_rate REAL,
                    cost REAL,
                    timestamp REAL
                );
            """)

    # 工作流相关操作
    def save_workflow(self, workflow_id: str, name: str, yaml_content: str):
        """保存工作流"""
        now = time.time()
        with self._get_conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO workflows (id, name, yaml_content, created_at, updated_at)
                VALUES (?, ?, ?, COALESCE((SELECT created_at FROM workflows WHERE id=?), ?), ?)""",
                (workflow_id, name, yaml_content, workflow_id, now, now)
            )

    def get_workflow(self, workflow_id: str) -> Optional[Dict]:
        """获取工作流详情"""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM workflows WHERE id = ?", (workflow_id,)).fetchone()
            if not row:
                return None
            return dict(row)

    def list_workflows(self) -> List[Dict]:
        """列出所有工作流"""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT id, name, created_at, updated_at FROM workflows ORDER BY updated_at DESC").fetchall()
            return [dict(row) for row in rows]

    def delete_workflow(self, workflow_id: str) -> bool:
        """删除工作流"""
        with self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM workflows WHERE id = ?", (workflow_id,))
            return cursor.rowcount > 0

    # 审计日志相关操作
    def save_audit_log(self, log_data: Dict):
        """保存审计日志"""
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO audit_logs 
                (user_id, operation, resource_type, resource_id, detail, result, error_message, timestamp, ip_address)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    log_data["user_id"],
                    log_data["operation"],
                    log_data["resource_type"],
                    log_data["resource_id"],
                    json.dumps(log_data.get("detail", {})),
                    log_data.get("result", True),
                    log_data.get("error_message"),
                    log_data.get("timestamp", time.time()),
                    log_data.get("ip_address")
                )
            )

    def query_audit_logs(
        self,
        user_id: Optional[str] = None,
        operation: Optional[str] = None,
        resource_type: Optional[str] = None,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict]:
        """查询审计日志"""
        sql = "SELECT * FROM audit_logs WHERE 1=1"
        params = []
        if user_id:
            sql += " AND user_id = ?"
            params.append(user_id)
        if operation:
            sql += " AND operation = ?"
            params.append(operation)
        if resource_type:
            sql += " AND resource_type = ?"
            params.append(resource_type)
        if start_time:
            sql += " AND timestamp >= ?"
            params.append(start_time)
        if end_time:
            sql += " AND timestamp <= ?"
            params.append(end_time)
        sql += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]

    # 路由记录相关操作
    def save_route_record(self, record_data: Dict):
        """保存路由记录"""
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO route_records 
                (query, selected_model, task_class, remaining_budget_rate, cost, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    record_data.get("query"),
                    record_data.get("selected_model"),
                    record_data.get("task_class"),
                    record_data.get("remaining_budget_rate", 0),
                    record_data.get("cost", 0),
                    record_data.get("timestamp", time.time())
                )
            )
