#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阶段三 F3-02 Visual Workflow Builder E2E 测试用例
覆盖DAG↔YAML双向编译、环检测、合法性校验功能验证
"""
import pytest
import yaml
from maop.core.visual_builder import VisualDAG, compile_visual_dag, decompile_yaml

class TestVisualBuilderE2E:
    def setup_method(self):
        """初始化合法的DAG测试数据"""
        self.valid_dag = VisualDAG(
            nodes=[
                {"id": "start", "type": "trigger", "config": {"event": "alert"}},
                {"id": "filter", "type": "filter", "config": {"condition": "severity > 3"}},
                {"id": "llm_analyze", "type": "llm_node", "config": {"task": "分析告警根因"}},
                {"id": "end", "type": "response", "config": {"action": "auto_remediate"}}
            ],
            edges=[
                {"from": "start", "to": "filter"},
                {"from": "filter", "to": "llm_analyze"},
                {"from": "llm_analyze", "to": "end"}
            ],
            name="告警自动处理工作流"
        )

    def test_dag_to_yaml_compile_success(self):
        """正常DAG编译为YAML成功，且字段完整"""
        yaml_content = compile_visual_dag(self.valid_dag)
        parsed = yaml.safe_load(yaml_content)
        assert parsed["name"] == "告警自动处理工作流"
        assert len(parsed["nodes"]) == 4
        assert len(parsed["edges"]) == 3
        # 验证节点字段传递正确
        llm_node = next(n for n in parsed["nodes"] if n["id"] == "llm_analyze")
        assert llm_node["type"] == "llm_node"
        assert llm_node["config"]["task"] == "分析告警根因"

    def test_yaml_to_dag_decompile_success(self):
        """YAML反编译为DAG成功，双向一致"""
        yaml_content = compile_visual_dag(self.valid_dag)
        decompiled_dag = decompile_yaml(yaml_content)
        assert decompiled_dag.name == self.valid_dag.name
        assert len(decompiled_dag.nodes) == len(self.valid_dag.nodes)
        assert len(decompiled_dag.edges) == len(self.valid_dag.edges)
        # 验证边映射正确
        original_edges = {(e["from"], e["to"]) for e in self.valid_dag.edges}
        decompiled_edges = {(e["from"], e["to"]) for e in decompiled_dag.edges}
        assert original_edges == decompiled_edges

    def test_cycle_detection_raise_error(self):
        """有环DAG编译时抛出异常"""
        cycle_dag = VisualDag(
            nodes=[
                {"id": "A", "type": "llm_node", "config": {}},
                {"id": "B", "type": "llm_node", "config": {}}
            ],
            edges=[
                {"from": "A", "to": "B"},
                {"from": "B", "to": "A"}
            ],
            name="有环测试DAG"
        )
        with pytest.raises(ValueError) as exc_info:
            compile_visual_dag(cycle_dag)
        assert "DAG存在环，无法编译" in str(exc_info.value)

    def test_missing_node_edge_raise_error(self):
        """边引用不存在的节点时抛出异常"""
        invalid_dag = VisualDag(
            nodes=[
                {"id": "A", "type": "llm_node", "config": {}},
                {"id": "B", "type": "llm_node", "config": {}}
            ],
            edges=[
                {"from": "A", "to": "C"} # C节点不存在
            ],
            name="节点缺失测试DAG"
        )
        with pytest.raises(ValueError) as exc_info:
            compile_visual_dag(invalid_dag)
        assert "边引用了不存在的节点" in str(exc_info.value)

    def test_empty_dag_compile(self):
        """空DAG编译抛出异常"""
        empty_dag = VisualDag(nodes=[], edges=[], name="空DAG")
        with pytest.raises(ValueError) as exc_info:
            compile_visual_dag(empty_dag)
        assert "DAG不能为空" in str(exc_info.value)
