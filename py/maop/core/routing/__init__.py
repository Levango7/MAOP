"""Routing subpackage.

路由评分、决策、动态配置、负载均衡、provider 健康、多目标评分。

Modules:
    route_scorer, routing_decision, dynamic_router, multi_objective_scorer,
    load_balancer, provider_health
"""
from __future__ import annotations

import importlib

__all__ = [
    "logger",
    "AgentScore",
    "DynamicRouter",
    "logger",
    "LBAlgorithm",
    "AgentMetrics",
    "LBStats",
    "LoadBalancer",
    "get_load_balancer",
    "logger",
    "ObjectiveWeights",
    "AgentObjectiveVector",
    "ParetoFrontierResult",
    "MultiObjectiveScorer",
    "logger",
    "HealthResult",
    "ProviderHealthChecker",
    "logger",
    "RouteMatch",
    "RouteScorer",
    "get_route_scorer",
    "logger",
    "RoutingDecisionRecord",
    "RoutingDecisionStore",
    "get_active_span_context",
    "get_routing_decision_store",
    "reset_routing_decision_store",
    "record_decision_safe",
]

# 符号 → 子模块名映射（惰性加载用，含私有符号）
_SYMBOL_TO_MODULE: dict[str, str] = {
    # 注: 多个子模块均导出同名符号（如 logger），
    # 按字典构造语义仅最后一个映射生效，与重构前运行时行为一致。
    "_CACHE_TTL_SEC": "dynamic_router",
    "_SPEED_NORMALIZATION_MS": "dynamic_router",
    "_DEFAULT_SUCCESS_RATE": "dynamic_router",
    "_DEFAULT_SPEED_SCORE": "dynamic_router",
    "_DEAD_AGENT_SCORE": "dynamic_router",
    "_RECENT_DELEGATION_LIMIT": "dynamic_router",
    "AgentScore": "dynamic_router",
    "DynamicRouter": "dynamic_router",
    "LBAlgorithm": "load_balancer",
    "_EWMA_ALPHA": "load_balancer",
    "AgentMetrics": "load_balancer",
    "LBStats": "load_balancer",
    "LoadBalancer": "load_balancer",
    "_global_lb": "load_balancer",
    "get_load_balancer": "load_balancer",
    "_set_lb_span_attrs": "load_balancer",
    "_record_lb_decision": "load_balancer",
    "ObjectiveWeights": "multi_objective_scorer",
    "AgentObjectiveVector": "multi_objective_scorer",
    "ParetoFrontierResult": "multi_objective_scorer",
    "MultiObjectiveScorer": "multi_objective_scorer",
    "HealthResult": "provider_health",
    "_safe_enum_value": "provider_health",
    "ProviderHealthChecker": "provider_health",
    "_REGEX_WEIGHT": "route_scorer",
    "_KEYWORD_BASE": "route_scorer",
    "_KEYWORD_BONUS": "route_scorer",
    "_CAPABILITY_BONUS": "route_scorer",
    "_CONFIDENCE_HIGH": "route_scorer",
    "_CONFIDENCE_MEDIUM": "route_scorer",
    "_COOLDOWN_SEC": "route_scorer",
    "_MAX_COOLDOWN_ENTRIES": "route_scorer",
    "_score_cache": "route_scorer",
    "_SCORE_CACHE_MAX": "route_scorer",
    "RouteMatch": "route_scorer",
    "_AgentCooldown": "route_scorer",
    "RouteScorer": "route_scorer",
    "_singleton_lock": "route_scorer",
    "_instance": "route_scorer",
    "get_route_scorer": "route_scorer",
    "_set_span_attr": "route_scorer",
    "_record_route_scorer_decision": "route_scorer",
    "logger": "routing_decision",
    "RoutingDecisionRecord": "routing_decision",
    "_ROUTING_DECISION_DDL": "routing_decision",
    "RoutingDecisionStore": "routing_decision",
    "get_active_span_context": "routing_decision",
    "_store_instance": "routing_decision",
    "get_routing_decision_store": "routing_decision",
    "reset_routing_decision_store": "routing_decision",
    "record_decision_safe": "routing_decision",
}


def __getattr__(name: str):
    """惰性加载子模块符号，避免循环导入。"""
    if name in _SYMBOL_TO_MODULE:
        mod_name = _SYMBOL_TO_MODULE[name]
        mod = importlib.import_module(f".{mod_name}", __name__)
        value = getattr(mod, name)
        globals()[name] = value  # 缓存，下次直接访问
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
