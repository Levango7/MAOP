"""Blackboard architecture business logic for MAOP Dashboard.

Encapsulates blackboard read/write/clear operations and stats collection.
Extracted from the blackboard router so the router layer only does
parameter parsing + service call + response.

The service is framework-agnostic: it does not import FastAPI and can be
unit-tested or invoked from CLI/CI without an HTTP context. The
blackboard singleton is obtained via ``get_blackboard()`` from the core
reliability module.
"""

from __future__ import annotations

import logging
from typing import Any

from maop.core.reliability.blackboard import (
    BlackboardDomain,
    get_blackboard,
)

logger = logging.getLogger(__name__)


# ── Allowed domains (whitelist) ────────────────────────────────────


def get_allowed_domains() -> list[str]:
    """返回所有允许的域（白名单）的字符串值列表。"""
    return [d.value for d in BlackboardDomain]


# ── Snapshot & domains ─────────────────────────────────────────────


def get_snapshot() -> dict[str, Any]:
    """获取黑板完整快照，返回 ``{domain: [entry_dict...]}``。"""
    bb = get_blackboard()
    return bb.get_snapshot()


def list_domains() -> dict[str, list[str]]:
    """列出允许的域（白名单）与当前非空域。

    返回 ``{"allowed": [...], "active": [...]}``。
    """
    bb = get_blackboard()
    return {
        "allowed": get_allowed_domains(),
        "active": bb.get_domains(),
    }


# ── Domain read / write / clear ────────────────────────────────────


def read_domain(domain: str) -> list[dict[str, Any]]:
    """读取指定域内所有条目，返回 dict 列表。

    可能抛出 ``InvalidDomainError``，由 router 层捕获并转为 HTTP 400。
    """
    bb = get_blackboard()
    entries = bb.read(domain)
    return [e.to_dict() for e in entries]


async def write_entry(
    domain: str,
    content: Any,
    contributor: str,
    confidence: float = 1.0,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """写入黑板条目，返回 entry dict。

    可能抛出 ``InvalidDomainError``，由 router 层捕获并转为 HTTP 400。
    """
    bb = get_blackboard()
    entry = await bb.write(
        domain,
        content,
        contributor,
        confidence=confidence,
        metadata=metadata or {},
    )
    return entry.to_dict()


async def clear_domain(domain: str) -> int:
    """清除指定域，返回被清除的条目数。

    可能抛出 ``InvalidDomainError``，由 router 层捕获并转为 HTTP 400。
    """
    bb = get_blackboard()
    return await bb.clear(domain)


# ── History & stats ────────────────────────────────────────────────


def get_history(limit: int = 100) -> list[dict[str, Any]]:
    """获取操作历史（最近 ``limit`` 条）。"""
    bb = get_blackboard()
    return bb.get_history(limit=limit)


def get_stats() -> dict[str, Any]:
    """黑板统计信息。"""
    bb = get_blackboard()
    return {
        "total_entries": bb.total_entries(),
        "active_domains": bb.get_domains(),
        "event_bus_enabled": bb.event_bus_enabled,
        "allowed_domains": get_allowed_domains(),
    }