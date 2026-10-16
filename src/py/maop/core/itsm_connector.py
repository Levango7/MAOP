#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第三方ITSM工具对接模块
支持ServiceNow/Jira的工单创建、查询、更新操作
"""

import requests
from typing import Dict, Optional
from enum import Enum
from .ha import retry


class ITSMType(Enum):
    """ITSM工具类型"""
    SERVICENOW = "servicenow"
    JIRA = "jira"


class ITSMConnector:
    """ITSM工具连接器"""
    def __init__(self, itsm_type: ITSMType, base_url: str, username: str, password: str):
        self.itsm_type = itsm_type
        self.base_url = base_url.rstrip("/")
        self.auth = (username, password)
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    @retry(max_retries=2, delay=0.5)
    def create_ticket(self, subject: str, description: str, priority: int = 3, **kwargs) -> Dict:
        """创建工单"""
        if self.itsm_type == ITSMType.SERVICENOW:
            url = f"{self.base_url}/api/now/table/incident"
            data = {
                "short_description": subject,
                "description": description,
                "priority": priority
            }
        elif self.itsm_type == ITSMType.JIRA:
            url = f"{self.base_url}/rest/api/2/issue/"
            data = {
                "fields": {
                    "project": {"key": kwargs.get("project_key", "MAOP")},
                    "summary": subject,
                    "description": description,
                    "issuetype": {"name": "Incident"},
                    "priority": {"name": self._map_jira_priority(priority)}
                }
            }
        else:
            raise ValueError(f"不支持的ITSM类型: {itsm_type}")
        response = requests.post(url, json=data, auth=self.auth, headers=self.headers, timeout=10)
        response.raise_for_status()
        return response.json()

    @retry(max_retries=2, delay=0.5)
    def get_ticket(self, ticket_id: str) -> Optional[Dict]:
        """查询工单详情"""
        if self.itsm_type == ITSMType.SERVICENOW:
            url = f"{self.base_url}/api/now/table/incident/{ticket_id}"
        elif self.itsm_type == ITSMType.JIRA:
            url = f"{self.base_url}/rest/api/2/issue/{ticket_id}"
        else:
            return None
        response = requests.get(url, auth=self.auth, headers=self.headers, timeout=10)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    @retry(max_retries=2, delay=0.5)
    def update_ticket(self, ticket_id: str, status: str, comment: Optional[str] = None) -> Dict:
        """更新工单状态/添加评论"""
        if self.itsm_type == ITSMType.SERVICENOW:
            url = f"{self.base_url}/api/now/table/incident/{ticket_id}"
            data = {"state": self._map_snow_status(status)}
            if comment:
                data["work_notes"] = comment
        elif self.itsm_type == ITSMType.JIRA:
            url = f"{self.base_url}/rest/api/2/issue/{ticket_id}"
            data = {
                "update": {
                    "comment": [{"add": {"body": comment}}] if comment else []
                },
                "transition": {"name": status}
            }
        else:
            raise ValueError(f"不支持的ITSM类型: {itsm_type}")
        response = requests.patch(url, json=data, auth=self.auth, headers=self.headers, timeout=10)
        response.raise_for_status()
        return response.json()

    def _map_jira_priority(self, priority: int) -> str:
        """映射优先级到Jira优先级名称"""
        map_dict = {
            1: "Highest",
            2: "High",
            3: "Medium",
            4: "Low",
            5: "Lowest"
        }
        return map_dict.get(priority, "Medium")

    def _map_snow_status(self, status: str) -> str:
        """映射通用状态到ServiceNow状态码"""
        map_dict = {
            "open": "1",
            "in_progress": "2",
            "resolved": "6",
            "closed": "7"
        }
        return map_dict.get(status, "1")
