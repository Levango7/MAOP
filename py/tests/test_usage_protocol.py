"""GAP-2 方案 A：``[USAGE]`` 协议行解析单元测试。

锁死契约：schema 拆分字段（``prompt=/completion=``）为可选项且追加在
``source=`` 之后；解析器必须同时吃旧格式（turns/tokens/source 三段）与新
格式；多行取最后一条；无协议行 ``None``（调用方回填语义不变）。
"""
from __future__ import annotations

from maop.core.agent.usage_protocol import parse_usage_line


def test_full_split_schema_wins() -> None:
    """拆分在位：total = prompt + completion（细契约优先于 tokens=）。"""
    u = parse_usage_line("[USAGE] turns=2 tokens=100 source=measured prompt=70 completion=30")
    assert u == {
        "prompt_tokens": 70,
        "completion_tokens": 30,
        "total_tokens": 100,
    }


def test_split_mismatch_prefers_split_sum() -> None:
    """tokens= 与拆分不一致时以拆分为准（拆分是更细的计费合同）。"""
    u = parse_usage_line("[USAGE] turns=1 tokens=5 source=measured prompt=100 completion=50")
    assert u is not None
    assert u["total_tokens"] == 150
    assert u["prompt_tokens"] == 100


def test_legacy_three_field_schema_only_total() -> None:
    """旧格式（老 agent）：只申报 total，in/out 拆分未知——成本侧走启发式。"""
    u = parse_usage_line("[USAGE] turns=1 tokens=520 source=estimated")
    assert u == {"total_tokens": 520}


def test_prompt_only_partial_split() -> None:
    """只打 prompt：completion 记 0（agent 未申报，弥散优于臆造）。"""
    u = parse_usage_line("[USAGE] turns=1 tokens=90 source=measured prompt=90")
    assert u == {
        "prompt_tokens": 90,
        "completion_tokens": 0,
        "total_tokens": 90,
    }


def test_completion_only_split() -> None:
    u = parse_usage_line("[USAGE] turns=1 source=measured completion=42")
    assert u == {
        "prompt_tokens": 0,
        "completion_tokens": 42,
        "total_tokens": 42,
    }


def test_bare_line_without_tokens_returns_none() -> None:
    assert parse_usage_line("[USAGE] turns=1 source=estimated") is None


def test_unrelated_stdout_returns_none() -> None:
    assert parse_usage_line("all good\nno usage here\n") is None


def test_empty_and_none_stdout() -> None:
    assert parse_usage_line("") is None
    assert parse_usage_line(None) is None


def test_multiple_lines_take_last() -> None:
    """多轮打印：最后一条是终态权威（与 run_sim harness 口径一致）。"""
    stdout = (
        "[sim:worker] ack\n"
        "[USAGE] turns=1 tokens=10 source=estimated prompt=7 completion=3\n"
        "[sim:worker] retry\n"
        "[USAGE] turns=2 tokens=999 source=estimated prompt=600 completion=399\n"
    )
    u = parse_usage_line(stdout)
    assert u is not None
    assert u["total_tokens"] == 999


def test_loop_level_prefixed_line_still_matches() -> None:
    """maop.cli run 的 loop 层行与 stub 行会在同一 stdout 先后出现。"""
    stdout = (
        "[USAGE] turns=1 tokens=0 source=estimated\n"
        "canned reply text\n"
        "[USAGE] turns=1 tokens=123 source=estimated prompt=80 completion=43\n"
    )
    u = parse_usage_line(stdout)
    assert u is not None
    assert u["total_tokens"] == 123


def test_turns_field_is_required() -> None:
    """无 turns= 的行不匹配（协议行龙头字段缺失视为非协议行）。"""
    assert parse_usage_line("[USAGE] tokens=100 source=measured") is None
