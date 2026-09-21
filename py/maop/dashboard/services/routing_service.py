"""Routing & Protocol 域 Service 层.

框架无关（不导入 FastAPI），函数接收显式参数，返回普通 dict/object，
不抛 HTTPException。可独立单元测试，无需 HTTP 上下文。

本模块从以下三个 router 提取业务逻辑：

- ``routing.py``         — 路由决策追踪（recent / stats / by_trace）
- ``routing_preview.py`` — 路由评分预览（match / cooldowns / scores）
- ``protocol.py``        — 协议注册中心（register / unregister / get /
  list / versions / validate / send / messages）

单例（RoutingDecisionStore / ProtocolRegistry）使用双重检查锁定保护，
与原 router 风格一致；``_set_xxx`` 供测试注入。共享运行时状态
（``MAOP_ROOT``）通过 ``maop.dashboard.routers.state`` 访问。

routing_preview 原先通过 ``os.path.dirname`` 五层上推 ``__file__``
解析项目根；在 service 层改用 ``state.MAOP_ROOT``（二者等价：均指向
仓库根），消除对 router 文件路径的依赖。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, cast

from maop.config.loader import load_config
from maop.core.routing.route_scorer import get_route_scorer
from maop.dashboard.routers import state

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# §1  单例访问器 — RoutingDecisionStore / ProtocolRegistry
# ══════════════════════════════════════════════════════════════════

_decision_store: Any = None
_decision_store_lock = threading.Lock()


def _get_decision_store() -> Any:
    """惰性初始化全局 RoutingDecisionStore 单例。"""
    global _decision_store
    if _decision_store is None:
        with _decision_store_lock:
            if _decision_store is None:  # double-checked locking
                from maop.core.routing.routing_decision import RoutingDecisionStore
                _decision_store = RoutingDecisionStore()
    return _decision_store


def _set_decision_store(store: Any) -> None:
    """供测试注入自定义 decision store（隔离存储）。"""
    global _decision_store
    with _decision_store_lock:
        _decision_store = store


_protocol_reg: Any = None
_protocol_reg_lock = threading.Lock()


def _get_protocol_reg() -> Any:
    """惰性初始化全局 ProtocolRegistry 单例。"""
    global _protocol_reg
    if _protocol_reg is None:
        with _protocol_reg_lock:
            if _protocol_reg is None:  # double-checked locking
                from maop.core.agent.plugins_hooks.protocol import ProtocolRegistry
                _protocol_reg = ProtocolRegistry(root_dir=str(state.MAOP_ROOT))
    return _protocol_reg


def _set_protocol_reg(reg: Any) -> None:
    """供测试注入自定义 protocol registry（隔离注册表）。"""
    global _protocol_reg
    with _protocol_reg_lock:
        _protocol_reg = reg


# ══════════════════════════════════════════════════════════════════
# §2  Routing decision trace — recent / stats / by_trace
# ══════════════════════════════════════════════════════════════════

def query_recent_decisions(limit: int, stage: str) -> dict[str, Any]:
    """查询最近的 routing 决策（newest-first）。

    Parameters
    ----------
    limit : int
        最大返回数量（已 clamp 到 [1, 1000]）。
    stage : str
        stage 过滤器；空字符串表示不过滤。

    Returns
    -------
    dict
        ``{"decisions": [...], "count": int, "total": int,
        "limit": int, "stage": str}``。
    """
    store = _get_decision_store()
    capped_limit = max(1, min(int(limit), 1000))
    stage_filter = stage.strip() or None
    decisions = store.query_recent(limit=capped_limit, stage=stage_filter)
    total = store.count(stage=stage_filter)
    return {
        "decisions": [d.to_dict() for d in decisions],
        "count": len(decisions),
        "total": total,
        "limit": capped_limit,
        "stage": stage_filter or "",
    }


def get_decision_stats() -> dict[str, Any]:
    """返回 routing 决策的聚合统计。

    Returns
    -------
    dict
        ``{"total": int, "by_stage": dict, "last_24h": int}``。
    """
    store = _get_decision_store()
    stats = store.stats()
    return {
        "total": stats.get("total", 0),
        "by_stage": stats.get("by_stage", {}),
        "last_24h": stats.get("last_24h", 0),
    }


def query_decisions_by_trace(trace_id: str) -> dict[str, Any]:
    """返回指定 trace 的完整决策链（oldest-first）。

    Returns
    -------
    dict
        ``{"trace_id": str, "decisions": [...], "count": int,
        "stages": [str, ...]}``。
    """
    store = _get_decision_store()
    decisions = store.query_by_trace(trace_id)
    stages = [d.stage for d in decisions]
    return {
        "trace_id": trace_id,
        "decisions": [d.to_dict() for d in decisions],
        "count": len(decisions),
        "stages": stages,
    }


# ══════════════════════════════════════════════════════════════════
# §3  Route scoring preview — match / cooldowns / scores
# ══════════════════════════════════════════════════════════════════

def _resolve_root() -> str:
    """解析项目根目录（兼容 MAOP_ROOT_DIR / MAOP_ROOT 环境变量）。

    等价于原 router 中 ``get_root_dir(default=<5 层 dirname>)`` ——
    五层 dirname 从 ``routers/routing_preview.py`` 上推到仓库根，
    与 ``state.MAOP_ROOT`` 指向同一目录。
    """
    from maop.config.env import get_root_dir

    return str(get_root_dir(default=str(state.MAOP_ROOT)))


def preview_route_match(task: str) -> dict[str, Any]:
    """预览任务描述的路由匹配结果。

    Parameters
    ----------
    task : str
        任务描述（非空，由 router 校验）。

    Returns
    -------
    dict
        匹配成功：``{"task": str, "matched": True, "routing_key": str,
        "agent": str, "score": float, "confidence": float,
        "matched_by": str, "all_candidates": [...]}``；
        未匹配：``{"task": str, "matched": False, "message": str}``。
    """
    root = _resolve_root()
    config = load_config(root)
    scorer = get_route_scorer(config)
    match = scorer.match(task, adaptive=True)

    if match is None:
        return {
            "task": task,
            "matched": False,
            "message": "No route matched — would fall through to legacy routing",
        }

    # 计算所有候选路由的分数以提供透明度
    task_lower = task.lower()
    # 优先使用公开方法 score_route，回退到内部方法 _score_route
    _score_fn = getattr(scorer, "score_route", None) or scorer._score_route
    all_scores: list[dict[str, Any]] = []
    for rk, route in config.routing.items():
        score, matched_by = _score_fn(task_lower, rk, route)
        if score > 0:
            all_scores.append({
                "routing_key": rk,
                "score": round(score, 4),
                "matched_by": matched_by,
                "primary": route.primary,
                "fallback": route.fallback or None,
                "tertiary": route.tertiary or None,
            })
    all_scores.sort(key=lambda x: cast(float, x["score"]), reverse=True)

    return {
        "task": task,
        "matched": True,
        "routing_key": match.routing_key,
        "agent": match.agent,
        "score": match.score,
        "confidence": match.confidence,
        "matched_by": match.matched_by,
        "all_candidates": all_scores,
    }


def get_route_cooldowns() -> dict[str, Any]:
    """获取所有处于冷却期（最近失败）的 agent。

    Returns
    -------
    dict
        ``{"count": int, "cooldowns": [...]}``。
    """
    scorer = get_route_scorer()
    cooldowns = scorer.get_cooldown_status()
    return {"count": len(cooldowns), "cooldowns": cooldowns}


def get_route_scores(task: str) -> dict[str, Any]:
    """获取所有路由对指定任务的评分。

    Parameters
    ----------
    task : str
        任务描述（非空，由 router 校验）。

    Returns
    -------
    dict
        ``{"task": str, "scores": [...]}``。
    """
    root = _resolve_root()
    config = load_config(root)
    scorer = get_route_scorer(config)
    task_lower = task.lower()
    # 优先使用公开方法 score_route，回退到内部方法 _score_route
    _score_fn = getattr(scorer, "score_route", None) or scorer._score_route
    scores: list[dict[str, Any]] = []
    for rk, route in config.routing.items():
        score, matched_by = _score_fn(task_lower, rk, route)
        scores.append({
            "routing_key": rk,
            "score": round(score, 4),
            "matched_by": matched_by or "none",
            "primary": route.primary,
        })
    scores.sort(key=lambda x: cast(float, x["score"]), reverse=True)
    return {"task": task, "scores": scores}


# ══════════════════════════════════════════════════════════════════
# §4  Protocol registry — register / unregister / get / list / versions
# ══════════════════════════════════════════════════════════════════

def register_protocol(
    name: str, version: str, schema_def: dict[str, Any],
    participants: list[str], description: str,
) -> dict[str, Any]:
    """注册协议。

    Returns
    -------
    dict
        ``{"protocol": dict}`` — 注册后的协议 model_dump()。
    """
    reg = _get_protocol_reg()
    proto = reg.register(
        name=name, version=version, schema_def=schema_def,
        participants=participants, description=description,
    )
    return {"protocol": proto.model_dump()}


def unregister_protocol(name: str, version: str) -> dict[str, Any]:
    """注销协议。

    Returns
    -------
    dict
        ``{"removed": bool}``。

    Raises
    ------
    KeyError
        协议不存在（registry 返回 False）。
    """
    reg = _get_protocol_reg()
    removed = reg.unregister(name, version)
    if not removed:
        raise KeyError(f"Protocol {name} v{version} not found")
    return {"removed": removed}


def get_protocol(name: str, version: str) -> dict[str, Any]:
    """获取单个协议定义。

    Returns
    -------
    dict
        ``{"protocol": dict}``。

    Raises
    ------
    KeyError
        协议不存在（registry 返回 None）。
    """
    reg = _get_protocol_reg()
    proto = reg.get(name, version)
    if proto is None:
        raise KeyError(f"Protocol {name} v{version} not found")
    return {"protocol": proto.model_dump()}


def list_protocols() -> dict[str, Any]:
    """列出所有协议。

    Returns
    -------
    dict
        ``{"protocols": [...], "count": int}``。
    """
    reg = _get_protocol_reg()
    protocols = reg.list_protocols()
    return {"protocols": [p.model_dump() for p in protocols], "count": len(protocols)}


def list_protocol_versions(name: str) -> dict[str, Any]:
    """列出指定协议的所有版本。

    Returns
    -------
    dict
        ``{"name": str, "versions": [...]}``。
    """
    reg = _get_protocol_reg()
    versions = reg.list_versions(name)
    return {"name": name, "versions": versions}


# ══════════════════════════════════════════════════════════════════
# §5  Protocol registry — validate / send / messages
# ══════════════════════════════════════════════════════════════════

def validate_protocol(protocol_name: str, payload: dict[str, Any], version: str) -> dict[str, Any]:
    """验证协议消息是否符合 schema。

    Returns
    -------
    dict
        ``{"valid": bool, "protocol": str, "version": str}``。
    """
    reg = _get_protocol_reg()
    valid = reg.validate(protocol_name, payload, version)
    return {"valid": valid, "protocol": protocol_name, "version": version}


def send_protocol_message(
    protocol: str, sender: str, recipient: str,
    payload: dict[str, Any], version: str,
) -> dict[str, Any]:
    """发送协议消息。

    Returns
    -------
    dict
        ``{"message": dict}`` — 发送后的消息 model_dump()。
    """
    reg = _get_protocol_reg()
    msg = reg.send_message(
        protocol=protocol, sender=sender, recipient=recipient,
        payload=payload, version=version,
    )
    return {"message": msg.model_dump()}


def get_protocol_messages(
    recipient: str, protocol: str, limit: int,
) -> dict[str, Any]:
    """获取指定 recipient 的协议消息。

    Parameters
    ----------
    recipient : str
        接收者标识（非空，由 router 校验）。
    protocol : str
        协议名过滤器；空字符串表示不过滤。
    limit : int
        最大返回数量。

    Returns
    -------
    dict
        ``{"messages": [...], "count": int}``。
    """
    reg = _get_protocol_reg()
    messages = reg.get_messages(recipient, protocol=protocol or None, limit=limit)
    return {"messages": [m.model_dump() for m in messages], "count": len(messages)}