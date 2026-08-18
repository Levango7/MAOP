"""Routing subpackage.

路由评分、决策、负载均衡、provider 健康。

Modules:
    route_scorer, routing_decision, load_balancer, provider_health
"""
from __future__ import annotations

import importlib

__all__ = [
    "AgentMetrics",
    "HealthResult",
    "LBAlgorithm",
    "LBStats",
    "LoadBalancer",
    "ProviderHealthChecker",
    "RouteMatch",
    "RouteScorer",
    "RoutingDecisionRecord",
    "RoutingDecisionStore",
    "get_active_span_context",
    "get_load_balancer",
    "get_route_scorer",
    "get_routing_decision_store",
    "logger",
    "record_decision_safe",
    "reset_routing_decision_store",
]

# 符号 → 子模块名映射（惰性加载用，含私有符号）
_SYMBOL_TO_MODULE: dict[str, str] = {
    # 注: 多个子模块均导出同名符号（如 logger），
    # 按字典构造语义仅最后一个映射生效，与重构前运行时行为一致。
    "LBAlgorithm": "load_balancer",
    "_EWMA_ALPHA": "load_balancer",
    "AgentMetrics": "load_balancer",
    "LBStats": "load_balancer",
    "LoadBalancer": "load_balancer",
    "_global_lb": "load_balancer",
    "get_load_balancer": "load_balancer",
    "_set_lb_span_attrs": "load_balancer",
    "_record_lb_decision": "load_balancer",
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
