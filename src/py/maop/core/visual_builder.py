#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
可视化工作流DAG编译器
实现Visual DAG ↔ MAOP YAML的双向编译、拓扑合法性校验功能
"""

import yaml
from dataclasses import dataclass
from typing import Dict, List, Set
from collections import deque


@dataclass
class VisualDAG:
    """可视化工作流DAG数据结构"""
    name: str
    nodes: List[Dict]
    edges: List[Dict]


def _detect_cycle(nodes: List[Dict], edges: List[Dict]) -> bool:
    """BFS拓扑排序检测有环，返回True表示存在环"""
    node_ids = {n["id"] for n in nodes}
    in_degree = {nid: 0 for nid in node_ids}
    adj = {nid: [] for nid in node_ids}
    # 构建邻接表和入度
    for e in edges:
        from_id = e["from"]
        to_id = e["to"]
        if from_id in node_ids and to_id in node_ids:
            adj[from_id].append(to_id)
            in_degree[to_id] += 1
    # BFS拓扑排序
    queue = deque([nid for nid, deg in in_degree.items() if deg == 0])
    visited = 0
    while queue:
        curr = queue.popleft()
        visited += 1
        for neighbor in adj[curr]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)
    # 访问节点数不等于总节点数则有环
    return visited != len(node_ids)


def compile_visual_dag(dag: VisualDAG) -> str:
    """将Visual DAG编译为MAOP YAML格式"""
    if not dag.nodes or not dag.edges:
        raise ValueError("DAG不能为空，至少需要包含一个节点和一条边")
    # 校验节点/边合法性
    node_ids = {n["id"] for n in dag.nodes}
    for e in dag.edges:
        if e["from"] not in node_ids or e["to"] not in node_ids:
            raise ValueError(f"边引用了不存在的节点: {e}")
    # 环检测
    if _detect_cycle(dag.nodes, dag.edges):
        raise ValueError("DAG存在环，无法编译")
    # 转换为YAML格式
    yaml_data = {
        "name": dag.name,
        "version": "1.0",
        "nodes": dag.nodes,
        "edges": dag.edges
    }
    return yaml.dump(yaml_data, allow_unicode=True, sort_keys=False)


def decompile_yaml(yaml_content: str) -> VisualDAG:
    """将MAOP YAML反编译为Visual DAG数据结构"""
    data = yaml.safe_load(yaml_content)
    if not data or "nodes" not in data or "edges" not in data:
        raise ValueError("YAML格式错误，缺少nodes或edges字段")
    return VisualDAG(
        name=data.get("name", "未命名工作流"),
        nodes=data["nodes"],
        edges=data["edges"]
    )
