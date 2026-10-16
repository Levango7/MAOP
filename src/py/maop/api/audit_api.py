#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
审计日志API接口
提供日志查询、导出功能
"""

from flask import Flask, request, jsonify
from py.maop.core.audit_logger import AuditLogger, OperationType
from py.maop.core.storage import SQLiteStorage

app = Flask(__name__)
# 注入持久化存储实例
audit_logger = AuditLogger(storage=SQLiteStorage())


@app.route("/api/v1/audit/logs", methods=["GET"])
def query_audit_logs():
    user_id = request.args.get("user_id")
    operation = request.args.get("operation")
    resource_type = request.args.get("resource_type")
    start_time = request.args.get("start_time", type=float)
    end_time = request.args.get("end_time", type=float)
    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)

    try:
        op_type = OperationType(operation) if operation else None
    except ValueError:
        return jsonify({"code": 400, "message": "无效的操作类型"}), 400

    logs = audit_logger.query_logs(
        user_id=user_id,
        operation=op_type,
        resource_type=resource_type,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        offset=offset
    )
    return jsonify({"code": 200, "data": logs, "total": len(audit_logger._logs)})


@app.route("/api/v1/audit/logs/export", methods=["GET"])
def export_audit_logs():
    format = request.args.get("format", "json")
    try:
        content = audit_logger.export_logs(format=format)
        return content, 200, {'Content-Type': 'application/octet-stream'}
    except Exception as e:
        return jsonify({"code": 400, "message": str(e)}), 400
