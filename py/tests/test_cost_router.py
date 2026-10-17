"""`cost_router`（F3-05 成本分层路由）的行为测试。

这个模块现在还没有生产调用方（登记在 dead-wiring 清单里），所以这些用例是它唯一的
被执行面 —— 也正是"接上线之前先把口径钉住"的地方。三条值得注意的实测结论写在用例里：

* `_pick_*` 三个策略分支里 **只有 balanced 会看 requested_model**，
  aggressive / quality_first 直接忽略调用方点名 —— 传了不报错，但也不生效。
* `get_cost_router()` 的 docstring 说"参数只在显式传入时覆盖已有配置"，
  **实际是"仅首次构造时生效"**：单例存在后任何参数都被忽略。本文件按真实行为钉住它，
  并把 docstring 改成同口径（本仓有过 get_route_scorer 单例污染导致生产路由退化的先例）。
* `monthly_budget_usd=0.0` 因为走 `or` 链，等价于"没传"，不是"预算为零"。
"""

from __future__ import annotations

import pytest

from maop.core.cost_router import (
    _DEFAULT_TIERS,
    CostTier,
    LLMCostRouter,
    RoutingStrategy,
    TaskClass,
    classify_task,
    get_cost_router,
    reset_cost_router,
)


@pytest.fixture(autouse=True)
def _no_router_singleton_leak():
    """单例是进程级的：前后各清一次，否则本文件的构造会漏给后续用例。"""
    reset_cost_router()
    yield
    reset_cost_router()


# ── classify_task ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("codegen", TaskClass.CODE_GENERATION),
        ("review", TaskClass.CHAT),
        ("fileops", TaskClass.CLASSIFICATION),
        ("embed", TaskClass.EMBEDDING),
        ("unknown-key", TaskClass.UNKNOWN),
    ],
)
def test_routing_key_fast_path(key: str, expected: TaskClass) -> None:
    assert classify_task(key) is expected


def test_task_text_heuristics_fire_only_when_key_is_unmapped() -> None:
    """routing key 命中就不再看文本 —— 文本是兜底不是并列判据。"""
    assert classify_task("", "please summarize this report") is TaskClass.SUMMARIZATION
    assert classify_task("codegen", "please summarize this") is TaskClass.CODE_GENERATION


def test_heuristic_precedence_is_documented_by_order() -> None:
    """关键词表是按序短路的：靠前的类别赢。这里钉住现实，不是我认为应该的样子。"""
    assert classify_task("", "search the code for bugs") is TaskClass.SEARCH
    assert classify_task("", "implement and chat about it") is TaskClass.CODE_GENERATION


def test_heuristics_are_case_insensitive() -> None:
    assert classify_task("", "SUMMARIZE PLEASE") is TaskClass.SUMMARIZATION
    assert classify_task("", "Fix the login bug") is TaskClass.CODE_GENERATION


# ── 分层表不变量 ────────────────────────────────────────────────────


def test_every_task_class_has_a_tier() -> None:
    assert set(_DEFAULT_TIERS) == set(TaskClass), "新增 TaskClass 必须同时补分层，否则走 UNKNOWN 兜底"


def test_tiers_never_list_preferred_model_as_its_own_fallback() -> None:
    for tc, tier in _DEFAULT_TIERS.items():
        assert tier.preferred_model, tc
        assert tier.preferred_model not in tier.fallback_models, f"{tc} 的兜底里混进了首选模型"
        assert tier.max_cost_per_1k_tokens > 0, f"{tc} 缺成本上限"


def test_cheap_task_classes_do_not_have_looser_ceilings_than_codegen() -> None:
    """分类/嵌入这种轻量任务的成本上限不该高于代码生成，否则分层就失去意义。"""
    ceiling = {tc: t.max_cost_per_1k_tokens for tc, t in _DEFAULT_TIERS.items()}
    assert ceiling[TaskClass.CLASSIFICATION] <= ceiling[TaskClass.CODE_GENERATION]
    assert ceiling[TaskClass.EMBEDDING] <= ceiling[TaskClass.CODE_GENERATION]


# ── route(): 三种策略 ─────────────────────────────────────────────


def test_balanced_respects_requested_model_but_still_marks_degraded() -> None:
    """实测：调用方点名的兜底模型会被选中，但 `degraded` 仍是 True。

    字段注释写的是"因预算被强制降级"，实现却是 line 256 的
    `selected != tier.preferred_model and strategy != QUALITY_FIRST` —— 只要没拿首选就算降级。
    本用例钉**真实行为**，同时把字段 docstring 改成同口径（见 cost_router.py）。
    要不要改成"按调用方意愿选就不算降级"是接线时的产品决定，现在没有消费方，我不擅自动语义。
    """
    r = LLMCostRouter(strategy=RoutingStrategy.BALANCED)
    d = r.route(routing_key="codegen", requested_model="gpt-4o-mini")
    assert d.selected_model == "gpt-4o-mini"
    assert "requested-model" in d.routing_reason
    assert d.degraded is True, "当前口径：只要没选 tier.preferred_model 就记 degraded"


def test_balanced_ignores_requested_model_outside_the_tier() -> None:
    r = LLMCostRouter(strategy=RoutingStrategy.BALANCED)
    d = r.route(routing_key="codegen", requested_model="some-random-model")
    assert d.selected_model == _DEFAULT_TIERS[TaskClass.CODE_GENERATION].preferred_model
    assert d.original_model == "some-random-model", "点名的模型必须留在审计记录里"


def test_balanced_budget_guard_downgrades_below_ten_percent() -> None:
    r = LLMCostRouter(strategy=RoutingStrategy.BALANCED)
    d = r.route(routing_key="codegen", budget_remaining_pct=9.9)
    assert d.selected_model == _DEFAULT_TIERS[TaskClass.CODE_GENERATION].fallback_models[0]
    assert "budget-guard" in d.routing_reason
    assert d.degraded is True


def test_aggressive_always_picks_cheapest_and_marks_degraded() -> None:
    r = LLMCostRouter(strategy=RoutingStrategy.AGGRESSIVE)
    d = r.route(routing_key="codegen", requested_model="gpt-4o")
    assert d.selected_model == _DEFAULT_TIERS[TaskClass.CODE_GENERATION].fallback_models[0]
    assert d.degraded is True
    assert "aggressive" in d.routing_reason


def test_quality_first_ignores_cost_and_is_never_marked_degraded() -> None:
    r = LLMCostRouter(strategy=RoutingStrategy.QUALITY_FIRST)
    d = r.route(routing_key="codegen", budget_remaining_pct=1.0, requested_model="gpt-4o-mini")
    assert d.selected_model == _DEFAULT_TIERS[TaskClass.CODE_GENERATION].preferred_model
    assert d.degraded is False, "quality_first 拿首选不是降级"


def test_tier_without_fallbacks_cannot_be_downgraded() -> None:
    """兜底表为空时 budget-guard / aggressive 必须退回首选，而不是抛或返回空串。"""
    tier = CostTier(task_class=TaskClass.CHAT, preferred_model="solo-model", fallback_models=())
    r = LLMCostRouter(strategy=RoutingStrategy.AGGRESSIVE, tiers={TaskClass.CHAT: tier})
    assert r.route(routing_key="chat").selected_model == "solo-model"
    r2 = LLMCostRouter(strategy=RoutingStrategy.BALANCED, tiers={TaskClass.CHAT: tier})
    assert r2.route(routing_key="chat", budget_remaining_pct=0.0).selected_model == "solo-model"


def test_missing_task_class_falls_back_to_the_module_unknown_tier() -> None:
    """实测：类别在自定义表里缺失时，兜底取的是**模块默认表**的 UNKNOWN，而不是实例自己的 UNKNOWN。

    即"只登记了 UNKNOWN 的自定义分层表"拦不住 codegen —— 它会拿到
    `_DEFAULT_TIERS[UNKNOWN]`，里面可能是调用方从没批准过的模型。
    这条口径对接线方是个隐患（现在无消费方），先由测试把它说破。
    """
    own_unknown = CostTier(task_class=TaskClass.UNKNOWN, preferred_model="fallback-pref",
                           fallback_models=("fb",))
    r = LLMCostRouter(tiers={TaskClass.UNKNOWN: own_unknown})
    d = r.route(routing_key="codegen")
    assert d.selected_model == _DEFAULT_TIERS[TaskClass.UNKNOWN].preferred_model
    assert d.selected_model != "fallback-pref", "兜底不读实例自己的 UNKNOWN 条目"


# ── 审计日志与隔离 ────────────────────────────────────────────────


def test_decision_log_appends_one_per_route_and_clears() -> None:
    r = LLMCostRouter()
    r.route(routing_key="codegen")
    r.route(routing_key="chat")
    assert len(r.get_decision_log()) == 2
    r.clear_decision_log()
    assert r.get_decision_log() == []


def test_decision_log_returned_copy_does_not_expose_internals() -> None:
    r = LLMCostRouter()
    r.route(routing_key="codegen")
    snapshot = r.get_decision_log()
    snapshot.clear()
    assert len(r.get_decision_log()) == 1, "拿到的必须是副本，否则外部能把审计记录清空"


def test_default_tier_table_is_not_shared_mutably() -> None:
    """实例改自己的分层表不许污染模块默认值。"""
    r = LLMCostRouter()
    original = _DEFAULT_TIERS[TaskClass.CHAT]
    r._tiers[TaskClass.CHAT] = CostTier(task_class=TaskClass.CHAT, preferred_model="mutated")
    assert _DEFAULT_TIERS[TaskClass.CHAT] is original


# ── estimate_cost ─────────────────────────────────────────────────


def test_estimate_cost_uses_per_million_token_math(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Tracker:
        def get_pricing(self) -> dict[str, dict[str, float]]:
            return {"m": {"prompt_per_1m": 2.0, "completion_per_1m": 4.0}}

    import maop.core.cost_tracker as ct

    monkeypatch.setattr(ct, "get_cost_tracker", lambda: _Tracker())
    r = LLMCostRouter()
    assert r.estimate_cost("m", prompt_tokens=1_000_000, completion_tokens=500_000) == 4.0
    assert r.estimate_cost("not-in-pricing", prompt_tokens=1000) == 0.0, "未知模型必须给 0，不是抛"


def test_estimate_cost_swallows_tracker_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import maop.core.cost_tracker as ct

    def _boom() -> object:
        raise RuntimeError("pricing table unavailable")

    monkeypatch.setattr(ct, "get_cost_tracker", _boom)
    assert LLMCostRouter().estimate_cost("m", prompt_tokens=10) == 0.0


# ── 单例访问器 ────────────────────────────────────────────────────


def test_get_cost_router_is_a_singleton_and_args_apply_only_at_construction() -> None:
    """docstring 原先写"显式传入时覆盖已有配置"，实测**不覆盖** —— 这里钉真实行为。

    本仓有过同类事故（get_route_scorer 单例污染导致生产路由退化），所以
    "传了参数却静默无效"必须由测试说清楚，而不是等人踩。
    """
    first = get_cost_router(strategy=RoutingStrategy.AGGRESSIVE)
    again = get_cost_router(strategy=RoutingStrategy.QUALITY_FIRST)
    assert again is first
    assert again._strategy is RoutingStrategy.AGGRESSIVE, "已存在的单例不会因新参数改策略"


def test_reset_cost_router_forces_rebuild_with_new_args() -> None:
    get_cost_router(strategy=RoutingStrategy.AGGRESSIVE)
    reset_cost_router()
    fresh = get_cost_router(strategy=RoutingStrategy.QUALITY_FIRST)
    assert fresh._strategy is RoutingStrategy.QUALITY_FIRST


def test_invalid_settings_policy_degrades_to_balanced_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cost_routing_policy 写了词表外的值 ⇒ RoutingStrategy() 抛 ValueError ⇒ 落到 except 分支。

    实测该分支**完全不读 settings**：SLO 回到硬编码 3000、预算回到 0.0 ——
    所以一个拼错的策略名会顺带吞掉 settings 里的延迟 SLO 与月度预算。
    这条也在用例里说破，接线时值得单独决定"要不要只降级策略、其余仍取 settings"。
    """
    import maop.config.settings as st

    class _BadSettings:
        cost_routing_policy = "yolo"
        budget_monthly_limit_usd = 12.5
        cost_latency_slo_ms = 900

    monkeypatch.setattr(st, "get_settings", lambda: _BadSettings())
    r = get_cost_router()
    assert r._strategy is RoutingStrategy.BALANCED
    assert r._latency_slo_ms == 3000, "异常分支不读 settings.cost_latency_slo_ms"
    assert r._monthly_budget == 0.0, "异常分支同样丢弃 settings.budget_monthly_limit_usd"


def test_valid_settings_values_are_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    """对照：策略名合法时 settings 才被采纳（与上一条合起来才证明差异在策略名）。"""
    import maop.config.settings as st

    class _OkSettings:
        cost_routing_policy = "aggressive"
        budget_monthly_limit_usd = 12.5
        cost_latency_slo_ms = 900

    monkeypatch.setattr(st, "get_settings", lambda: _OkSettings())
    r = get_cost_router()
    assert r._strategy is RoutingStrategy.AGGRESSIVE
    assert r._latency_slo_ms == 900
    assert r._monthly_budget == 12.5
