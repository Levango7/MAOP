#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
操作审计日志模块
记录、查询、导出用户操作行为日志
"""

import time
import json
import logging
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Dict, List, Optional


class OperationType(Enum):
    """操作类型枚举"""
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    EXECUTE = "execute"
    LOGIN = "login"
    LOGOUT = "logout"
    ROUTE_MANAGE = "route_manage"
    WORKFLOW_DEPLOY = "workflow_deploy"


@dataclass
class AuditLog:
    """审计日志条目"""
    user_id: str
    operation: OperationType
    resource_type: str
    resource_id: str
    detail: Dict
    result: bool = True
    error_message: Optional[str] = None
    timestamp: float = 0.0
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()


class AuditLogger:
    """审计日志记录器"""
    def __init__(self, storage: Optional['SQLiteStorage'] = None):
        self.storage = storage  # SQLite存储实例
        self.logger = logging.getLogger("audit_log")

    def log(self, log_entry: AuditLog):
        """记录审计日志"""
        log_data = asdict(log_entry)
        # 保存到持久化存储
        if self.storage:
            self.storage.save_audit_log(log_data)
        # 同步写入系统日志
        self.logger.info(json.dumps(log_data, default=str))

    def query_logs(
        self,
        user_id: Optional[str] = None,
        operation: Optional[OperationType] = None,
        resource_type: Optional[str] = None,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict]:
        """查询审计日志"""
        op_value = operation.value if operation else None
        if self.storage:
            return self.storage.query_audit_logs(
                user_id=user_id,
                operation=op_value,
                resource_type=resource_type,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
                offset=offset
            )
        # 无存储时返回空
        return []

    def export_logs(self, format: str = "json") -> str:
        """导出审计日志"""
        logs = self.query_logs(limit=10000)
        if format == "json":
            return json.dumps(logs, default=str, ensure_ascii=False, indent=2)
        elif format == "csv":
            if not logs:
                return ""
            headers = list(logs[0].keys())
            csv_content = ",".join(headers) + "\n"
            for log in logs:
                csv_content += ",".join(str(log.get(h, "")) for h in headers) + "\n"
            return csv_content
        else:
            raise ValueError(f"不支持的导出格式: {format}")
