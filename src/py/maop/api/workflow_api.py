#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
工作流管理API
提供DAG编译、反编译、保存、加载功能
"""

import time
from flask import Flask, request, jsonify
from py.maop.core.visual_builder import compile_visual_dag, decompile_yaml, VisualDAG
from py.maop.api.auth_middleware import require_auth
from py.maop.core.rbac import Permission

app = Flask(__name__)

# 替换为SQLite持久化存储
from py.maop.core.storage import SQLiteStorage
storage = SQLiteStorage()


@app.route("/api/v1/workflow/compile", methods=["POST"])
@require_auth(Permission.WORKFLOW_CREATE)
def compile_workflow():
    """前端DAG编译为YAML"""
    data = request.json
    try:
        dag = VisualDAG(
            name=data.get("name", "未命名工作流"),
            nodes=data.get("nodes", []),
            edges=data.get("edges", [])
        )
        yaml_content = compile_visual_dag(dag)
        return yaml_content, 200, {'Content-Type': 'text/yaml'}
    except Exception as e:
        return jsonify({"code": 400, "message": str(e)}), 400


@app.route("/api/v1/workflow/decompile", methods=["POST"])
@require_auth(Permission.WORKFLOW_VIEW)
def decompile_workflow():
    """YAML反编译为DAG供前端编辑"""
    yaml_data = request.data.decode('utf-8')
    try:
        dag = decompile_yaml(yaml_data)
        return jsonify({
            "code": 200,
            "data": {
                "name": dag.name,
                "nodes": dag.nodes,
                "edges": dag.edges
            }
        })
    except Exception as e:
        return jsonify({"code": 400, "message": str(e)}), 400


@app.route("/api/v1/workflow/save", methods=["POST"])
def save_workflow():
    """保存工作流"""
    data = request.json
    try:
        dag = VisualDAG(
            name=data.get("name", "未命名工作流"),
            nodes=data.get("nodes", []),
            edges=data.get("edges", [])
        )
        yaml_content = compile_visual_dag(dag)
        workflow_id = data.get("id", f"wf_{int(time.time())}")
        storage.save_workflow(workflow_id, dag.name, yaml_content)
        return jsonify({"code": 200, "data": {"id": workflow_id}})
    except Exception as e:
        return jsonify({"code": 400, "message": str(e)}), 400


@app.route("/api/v1/workflow/list", methods=["GET"])
@require_auth(Permission.WORKFLOW_VIEW)
def list_workflows():
    """列出所有工作流"""
    workflows = storage.list_workflows()
    return jsonify({
        "code": 200,
        "data": workflows
    })


@app.route("/api/v1/workflow/<workflow_id>", methods=["GET"])
@require_auth(Permission.WORKFLOW_VIEW)
def get_workflow(workflow_id):
    """获取工作流详情"""
    workflow = storage.get_workflow(workflow_id)
    if not workflow:
        return jsonify({"code": 404, "message": "工作流不存在"}), 404
    return jsonify({"code": 200, "data": workflow})
