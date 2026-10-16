#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
审计日志模块单元测试
"""
import pytest
from py.maop.core.audit_logger import AuditLogger, AuditLog, OperationType


class TestAuditLogger:
    def setup_method(self):
        self.logger = AuditLogger()
        # 插入测试数据
        self.logger.log(AuditLog(
            user_id="user_1",
            operation=OperationType.CREATE,
            resource_type="workflow",
            resource_id="wf_001",
            detail={"name": "测试工作流"}
        ))
        self.logger.log(AuditLog(
            user_id="user_2",
            operation=OperationType.WORKFLOW_DEPLOY,
            resource_type="workflow",
            resource_id="wf_002",
            detail={"name": "告警处理工作流"}
        ))

    def test_log_count(self):
        assert len(self.logger._logs) == 2

    def test_query_by_user_id(self):
        logs = self.logger.query_logs(user_id="user_1")
        assert len(logs) == 1
        assert logs[0]["operation"] == OperationType.CREATE.value

    def test_query_by_resource_type(self):
        logs = self.logger.query_logs(resource_type="workflow")
        assert len(logs) == 2

    def test_query_with_pagination(self):
        logs = self.logger.query_logs(limit=1, offset=0)
        assert len(logs) == 1

    def test_export_json(self):
        json_content = self.logger.export_logs(format="json")
        assert "user_1" in json_content
        assert "wf_002" in json_content

    def test_export_csv(self):
        csv_content = self.logger.export_logs(format="csv")
        assert "user_id,operation,resource_type" in csv_content
        assert "user_1" in csv_content

    def test_export_invalid_format_raises(self):
        with pytest.raises(ValueError):
            self.logger.export_logs(format="xml")
