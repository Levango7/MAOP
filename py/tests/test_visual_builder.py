"""`visual_builder`（F3-02 可视化 DAG ↔ MAOP YAML）的行为测试。

价值集中在两处，也是前端一接线就会踩的地方：

1. **compile/decompile 必须可逆** —— 否则"在画布上编辑已有工作流"会静默丢字段；
2. **validate_dag 的错误码是前端唯一能对齐的契约**，码名一改前端就显示不出来。

本文件实测出的三条实现现实都写在对应用例里：`NO_SOURCE_NODE` 与 `CYCLIC_DAG` 在有环时
**同时**报（同一事实的两个视角）；condition 节点的表达式住在 `config["expression"]`；
节点坐标按 docstring 明示会归零。
"""

from __future__ import annotations

import pytest

yaml = pytest.importorskip("yaml")

# 产品码必须等 importorskip 之后再导（PyYAML 缺失时整个文件要干净跳过）
from maop.core.visual_builder import (
    NODE_TYPE_AGENT,
    NODE_TYPE_CONDITION,
    NODE_TYPE_GATEWAY,
    VALID_NODE_TYPES,
    VisualDAG,
    VisualEdge,
    VisualNode,
    compile_visual_dag,
    decompile_yaml,
    optimization_hints,
    validate_dag,
)


def _healthy_dag() -> VisualDAG:
    return VisualDAG(
        name="release",
        version=3,
        nodes=[
            VisualNode(id="start", node_type=NODE_TYPE_AGENT, label="intake"),
            VisualNode(id="gw", node_type=NODE_TYPE_GATEWAY),
            VisualNode(
                id="cond",
                node_type=NODE_TYPE_CONDITION,
                config={"expression": "score > 0.8"},
            ),
            VisualNode(id="pub", node_type=NODE_TYPE_AGENT, config={"agent": "publisher"}),
        ],
        edges=[
            VisualEdge(source="start", target="gw"),
            VisualEdge(source="gw", target="cond", label="ok", condition="score > 0.8"),
            VisualEdge(source="cond", target="pub"),
        ],
    )


# ── compile ────────────────────────────────────────────────────────


def test_compile_emits_a_valid_workflow_document() -> None:
    doc = yaml.safe_load(compile_visual_dag(_healthy_dag()))
    assert doc["name"] == "release"
    assert doc["version"] == 3
    assert {n["id"] for n in doc["nodes"]} == {"start", "gw", "cond", "pub"}
    assert [e["target"] for e in doc["edges"]] == ["gw", "cond", "pub"]


def test_compile_is_deterministic() -> None:
    """同一棵树两次编译必须逐字节相同 —— 否则画布保存会产出无意义的 diff。"""
    a, b = _healthy_dag(), _healthy_dag()
    assert compile_visual_dag(a) == compile_visual_dag(b)


def test_compile_keeps_position_out_of_the_artifact() -> None:
    """坐标是画布态，不进 YAML（docstring 明示 round-trip 后归零）。"""
    dag = _healthy_dag()
    dag.nodes[0].position_x = 120.5
    dag.nodes[0].position_y = -8.0
    doc = yaml.safe_load(compile_visual_dag(dag))
    assert not any("position" in n for n in doc["nodes"])
    assert decompile_yaml(compile_visual_dag(dag)).nodes[0].position_x == 0.0


def test_condition_node_expression_is_written_from_config() -> None:
    doc = yaml.safe_load(compile_visual_dag(_healthy_dag()))
    cond = next(n for n in doc["nodes"] if n["type"] == NODE_TYPE_CONDITION)
    assert cond["condition"] == "score > 0.8"


def test_condition_node_without_expression_defaults_to_true_string() -> None:
    dag = VisualDAG(
        name="bare",
        nodes=[VisualNode(id="c", node_type=NODE_TYPE_CONDITION)],
        edges=[],
    )
    doc = yaml.safe_load(compile_visual_dag(dag))
    assert doc["nodes"][0]["condition"] == "true", "空 config 也要给出可用表达式"


def test_nodes_without_config_do_not_emit_an_empty_config_key() -> None:
    doc = yaml.safe_load(compile_visual_dag(_healthy_dag()))
    gw = next(n for n in doc["nodes"] if n["id"] == "gw")
    assert "config" not in gw


# ── round-trip ─────────────────────────────────────────────────────


def test_round_trip_preserves_structure() -> None:
    back = decompile_yaml(compile_visual_dag(_healthy_dag()))
    assert back.name == "release"
    assert back.version == 3
    assert [n.id for n in back.nodes] == ["start", "gw", "cond", "pub"]
    assert [(e.source, e.target) for e in back.edges] == [
        ("start", "gw"), ("gw", "cond"), ("cond", "pub"),
    ]
    assert back.nodes[0].label == "intake"


def test_round_trip_preserves_node_config_and_edge_condition() -> None:
    """docstring 承诺"lossless for all structural data"，这里就是那条承诺的判据。

    表达式走的是 `config`（compile 会把整个 config 原样写进节点），
    而节点级 `condition` 键只是给编排器的派生副本 —— 所以可逆性靠 config 成立，不是靠那个键。
    """
    original = _healthy_dag()
    back = decompile_yaml(compile_visual_dag(original))
    assert back.nodes[3].config == {"agent": "publisher"}
    assert back.edges[1].condition == "score > 0.8"
    cond = next(n for n in back.nodes if n.node_type == NODE_TYPE_CONDITION)
    assert cond.config.get("expression") == "score > 0.8"


def test_node_level_condition_key_is_not_read_back_into_config() -> None:
    """派生副本是**单向**的：手写 YAML 只给 `condition` 而不给 `config.expression` 时不会还原。

    这条不是缺陷断言，是把边界写清楚 —— 画布重新导入外部 YAML 时，条件表达式必须以
    `config.expression` 为事实源，`condition` 键只出不进。
    """
    dag = decompile_yaml(
        "name: external\nversion: 1\n"
        "nodes:\n- id: c\n  type: condition\n  condition: 'x > 1'\n"
        "edges: []\n"
    )
    assert dag.nodes[0].config == {}, "decompile 不消费节点级 condition 键"


def test_decompile_of_broken_yaml_returns_a_named_fallback_instead_of_raising() -> None:
    """解析失败要落到可识别的占位 DAG（前端据此提示），不能把异常抛给用户操作。"""
    dag = decompile_yaml("\t this: is: not: valid: yaml: [")
    assert dag.name == "parse-error"
    assert dag.nodes == []


def test_decompile_of_empty_document_falls_back_to_defaults() -> None:
    dag = decompile_yaml("")
    assert dag.name == "untitled"
    assert dag.version == 1


# ── validate_dag ───────────────────────────────────────────────────


def test_valid_node_types_are_the_closed_set() -> None:
    assert VALID_NODE_TYPES == frozenset({"agent", "tool", "gateway", "condition"})


def test_healthy_dag_has_no_errors() -> None:
    assert validate_dag(_healthy_dag()) == []


def test_duplicate_node_id_is_reported() -> None:
    dag = VisualDAG(
        name="dup",
        nodes=[VisualNode(id="a", node_type=NODE_TYPE_AGENT),
               VisualNode(id="a", node_type=NODE_TYPE_AGENT)],
        edges=[],
    )
    codes = [e.code for e in validate_dag(dag)]
    assert "DUPLICATE_NODE_ID" in codes


def test_invalid_node_type_is_reported_per_node() -> None:
    dag = VisualDAG(name="t", nodes=[VisualNode(id="a", node_type="shiny")], edges=[])
    err = next(e for e in validate_dag(dag) if e.code == "INVALID_NODE_TYPE")
    assert err.node_id == "a"


@pytest.mark.parametrize(("missing", "code"), [("source", "MISSING_EDGE_SOURCE"),
                                               ("target", "MISSING_EDGE_TARGET")])
def test_edges_pointing_at_unknown_nodes_are_reported(missing: str, code: str) -> None:
    kwargs = {"source": "ghost", "target": "a"} if missing == "source" else {"source": "a", "target": "ghost"}
    dag = VisualDAG(
        name="dangling",
        nodes=[VisualNode(id="a", node_type=NODE_TYPE_AGENT)],
        edges=[VisualEdge(**kwargs)],
    )
    assert code in [e.code for e in validate_dag(dag)]


def test_empty_dag_is_reported_without_node_level_noise() -> None:
    codes = [e.code for e in validate_dag(VisualDAG(name="empty"))]
    assert codes == ["EMPTY_DAG"]


def test_cycle_reports_both_structural_codes() -> None:
    """有环时 CYCLIC_DAG 与 NO_SOURCE_NODE 同时出现 —— 后者文案自己写着"likely a cycle"。

    钉住这条是为了防止有人把 NO_SOURCE_NODE 当成独立缺陷去"修"：它是同一事实的第二视角。
    """
    dag = VisualDAG(
        name="cycle",
        nodes=[VisualNode(id="a", node_type=NODE_TYPE_AGENT),
               VisualNode(id="b", node_type=NODE_TYPE_AGENT)],
        edges=[VisualEdge(source="a", target="b"), VisualEdge(source="b", target="a")],
    )
    codes = {e.code for e in validate_dag(dag)}
    assert {"CYCLIC_DAG", "NO_SOURCE_NODE"} <= codes


def test_source_node_exists_when_one_node_has_no_incoming_edge() -> None:
    assert "NO_SOURCE_NODE" not in {e.code for e in validate_dag(_healthy_dag())}


# ── optimization_hints ─────────────────────────────────────────────


def test_hints_with_no_data_are_structurally_complete() -> None:
    out = optimization_hints()
    assert out["suggestions"] == []
    assert out["top_waste_agent"] == ""
    assert out["budget_remaining_pct"] == 100.0
    # 2026-10-28 契约更新：cache_hit_rate 已接 SemanticCache.stats()（089897a 消除
    # TODO）。空缓存是**真实值 0.0**（hits/(hits+misses) 分母为 0 时返回 0.0），
    # 不再是未接线期的 None；结构的完整性由其余三键继续把守。
    assert out["cache_hit_rate"] == 0.0, "必须是真实统计值，不能是 None 或编造值"


def test_top_spender_agent_gets_a_downgrade_suggestion_with_60pct_estimate() -> None:
    out = optimization_hints(by_agent={"alpha": {"cost": 100.0}, "beta": {"cost": 30.0}})
    assert out["top_waste_agent"] == "alpha"
    assert out["potential_savings_usd"] == 60.0
    assert any("alpha" in s for s in out["suggestions"])


def test_zero_cost_agents_produce_no_suggestion() -> None:
    out = optimization_hints(by_agent={"alpha": {"cost": 0.0}})
    assert out["top_waste_agent"] == ""
    assert out["suggestions"] == []


def test_budget_pressure_fires_below_twenty_percent_remaining() -> None:
    out = optimization_hints(monthly_spent_usd=90.0, monthly_budget_usd=100.0)
    assert out["budget_remaining_pct"] == 10.0
    assert any("AGGRESSIVE" in s for s in out["suggestions"])


def test_overspend_clamps_remaining_to_zero_and_is_never_negative() -> None:
    out = optimization_hints(monthly_spent_usd=150.0, monthly_budget_usd=100.0)
    assert out["budget_remaining_pct"] == 0.0


def test_zero_budget_does_not_claim_pressure() -> None:
    """budget 未设（0）时不能算"剩余 0%"来吓用户。"""
    out = optimization_hints(monthly_spent_usd=50.0, monthly_budget_usd=0.0)
    assert out["budget_remaining_pct"] == 100.0
    assert not any("AGGRESSIVE" in s for s in out["suggestions"])


def test_embedding_model_cost_is_flagged_case_insensitively() -> None:
    out = optimization_hints(by_model={"Text-Embedding-3": {"cost": 4.0}, "gpt-4o": {"cost": 9.0}})
    assert any("Embedding" in s and "4.00" in s for s in out["suggestions"])
