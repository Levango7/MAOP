"""Overview, system resources & diagnostics endpoints.

Endpoints:
    GET /api/overview           — aggregated KPI dashboard data
    GET /api/system/resources   — memory / SQLite / vector / log usage
    GET /api/system/diagnostics — health checks for core subsystems
"""

from __future__ import annotations

import asyncio
import logging
import platform
import sys
import time
from typing import Any

from fastapi import APIRouter, Request

from maop import __version__ as MAOP_VERSION
from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

from . import _deps

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Overview (v4 compat) ──────────────────────────────────────────
_overview_cache: dict[str, Any] = {}
_OVERVIEW_CACHE_TTL = 60.0
# P2-9 fix: cache file counts separately (rarely change, expensive to compute)
_file_counts_cache: dict[str, Any] = {}
_FILE_COUNTS_CACHE_TTL = 600.0  # 10 minutes


@router.get("/api/overview")
@handle_api_errors
async def api_overview(request: Request) -> dict[str, Any]:
    now = time.monotonic()
    cached = _overview_cache.get("data")
    cached_at = _overview_cache.get("ts", 0)
    if cached and now - cached_at < _OVERVIEW_CACHE_TTL:
        return cached
    try:
        b = _deps.get_bridge()
        rpt = await b.report(hours=48)
        agents = await b.agent_stats()
        ts = await b.timeseries(hours=168)
        live = await b.live()
        fails = await b.failures()
        # Real delegation trend (MoM/YoY) sourced from logs/delegations.json,
        # which holds the genuine delegation history (the SQL delegations
        # table is not populated by the current pipeline).
        period = await b.delegation_period_stats()
        agent_count = (
            len(agents.get("agents", []))
            if isinstance(agents, dict)
            else (len(agents) if isinstance(agents, list) else 0)
        )
        # Use real delegation history (logs/delegations.json) for the KPI so the
        # overview shows actual numbers instead of 0 from the empty SQL table.
        deleg_total = period.get("total", 0)
        deleg_sr = period.get("success_rate", 0.0)
        # P2-9 fix: cache file counts (10min TTL) to avoid per-request file traversal
        _fc_now = time.monotonic()
        _fc_cached = _file_counts_cache.get("data")
        _fc_ts = _file_counts_cache.get("ts", 0)
        if _fc_cached and _fc_now - _fc_ts < _FILE_COUNTS_CACHE_TTL:
            source_files = _fc_cached["source_files"]
            code_lines = _fc_cached["code_lines"]
            test_files = _fc_cached["test_files"]
            tests_total = _fc_cached["tests_total"]
        else:
            py_dir = _deps.MAOP_ROOT / "py" / "maop"
            source_files = sum(
                1 for p in py_dir.rglob("*.py") if "__pycache__" not in str(p)
            )
            code_lines = 0
            for p in py_dir.rglob("*.py"):
                if "__pycache__" in str(p):
                    continue
                try:
                    code_lines += await asyncio.to_thread(_deps._count_file_lines, p)
                except Exception as exc:
                    logger.warning('Failed to count code lines: %s', exc)
            test_dir = _deps.MAOP_ROOT / "py" / "tests"
            test_files = sum(1 for p in test_dir.glob("test_*.py")) if test_dir.exists() else 0
            tests_total = 0
            if test_dir.exists():
                for tf in test_dir.glob("test_*.py"):
                    try:
                        tests_total += tf.read_text(encoding="utf-8", errors="replace").count("def test_")
                    except Exception as exc:
                        logger.warning('Failed to count test functions: %s', exc)
            _file_counts_cache["data"] = {
                "source_files": source_files, "code_lines": code_lines,
                "test_files": test_files, "tests_total": tests_total,
            }
            _file_counts_cache["ts"] = _fc_now
        # Count actual API endpoints from FastAPI app routes
        api_endpoints = sum(
            1 for r in request.app.routes if getattr(r, 'path', '').startswith('/api/')
        )
        result = {
            "agents_total": agent_count, "modules_total": source_files,
            "tests_total": tests_total,
            "success_rate": deleg_sr,
            "delegations_total": deleg_total,
            "delegations_mom": period.get("delegations_mom"),
            "delegations_yoy": period.get("delegations_yoy"),
            "success_rate_mom": period.get("success_rate_mom"),
            "success_rate_yoy": period.get("success_rate_yoy"),
            "avg_latency_ms": rpt.get("avg_latency_ms", 0) if isinstance(rpt, dict) else 0,
            "recent_delegations": (
                live.get("recent_delegations", [])
                if isinstance(live, dict)
                else (live if isinstance(live, list) else [])
            ),
            "fail_ranking": fails if isinstance(fails, list) else [],
            "source_files": source_files, "code_lines": code_lines,
            "test_files": test_files,
            "api_endpoints": api_endpoints,
            "python_ver": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "platform": f"{platform.system()} {platform.machine()}",
            "version": MAOP_VERSION,
            "uptime": f"{round(time.time() - _deps.start_time)}s",
            "timeseries": ts if isinstance(ts, list) else None,
        }
        _overview_cache["data"] = result
        _overview_cache["ts"] = now
        return result
    except Exception as exc:
        logger.error('Overview failed: %s', exc)
        return {"error": "Overview failed", "agents_total": 0, "modules_total": 0, "tests_total": 0}


# ── System Resources & Diagnostics (C-7 修复) ─────────────────────
@router.get("/api/system/resources")
@handle_api_errors
async def api_system_resources(request: Request) -> dict[str, Any]:
    """返回系统资源使用情况：内存存储 / SQLite DB / 向量索引 / 日志文件。

    每项返回 {pct, used_mb, total_mb}；失败时该项追加 {error} 字段，pct 降级为 0。
    psutil 未安装时 memory_store 降级为 0，并在该项标记 error。
    """
    require_admin(request)

    # ── Memory Store: 进程 RSS，总内存上限默认 100MB ──
    mem_total_mb = 100.0
    mem_used_mb = 0.0
    mem_error: str | None = None
    try:
        import psutil
        proc = psutil.Process()
        mem_used_mb = proc.memory_info().rss / 1024 / 1024
    except ImportError:
        mem_error = "psutil not available"
    except Exception as exc:
        mem_error = str(exc)[:200]
    memory_store: dict[str, Any] = {
        "pct": _deps._pct(mem_used_mb, mem_total_mb),
        "used_mb": round(mem_used_mb, 2),
        "total_mb": mem_total_mb,
    }
    if mem_error:
        memory_store["error"] = mem_error

    # ── SQLite DB: data 目录下所有 .db 文件大小总和，上限默认 50MB ──
    sqlite_total_mb = 50.0
    sqlite_used_mb = 0.0
    sqlite_error: str | None = None
    try:
        data_dir = _deps.MAOP_ROOT / "data"
        if data_dir.exists():
            for f in data_dir.glob("*.db"):
                if f.is_file():
                    try:
                        sqlite_used_mb += f.stat().st_size / 1024 / 1024
                    except OSError as exc:
                        logger.warning('Failed to stat %s: %s', f, exc)
    except Exception as exc:
        sqlite_error = str(exc)[:200]
    sqlite_db: dict[str, Any] = {
        "pct": _deps._pct(sqlite_used_mb, sqlite_total_mb),
        "used_mb": round(sqlite_used_mb, 2),
        "total_mb": sqlite_total_mb,
    }
    if sqlite_error:
        sqlite_db["error"] = sqlite_error

    # ── Vector Index: data 目录下向量相关文件大小，上限默认 100MB ──
    vector_total_mb = 100.0
    vector_used_mb = 0.0
    vector_error: str | None = None
    try:
        data_dir = _deps.MAOP_ROOT / "data"
        if data_dir.exists():
            for pattern in ("*.vec", "*.index", "vector*.db", "vectors*.db", "*.faiss"):
                for f in data_dir.glob(pattern):
                    if f.is_file():
                        try:
                            vector_used_mb += f.stat().st_size / 1024 / 1024
                        except OSError as exc:
                            logger.warning('Failed to stat %s: %s', f, exc)
    except Exception as exc:
        vector_error = str(exc)[:200]
    vector_index: dict[str, Any] = {
        "pct": _deps._pct(vector_used_mb, vector_total_mb),
        "used_mb": round(vector_used_mb, 2),
        "total_mb": vector_total_mb,
    }
    if vector_error:
        vector_index["error"] = vector_error

    # ── Log Files: logs 目录总大小，上限默认 50MB ──
    log_total_mb = 50.0
    log_used_mb = 0.0
    log_error: str | None = None
    try:
        log_dir = _deps.MAOP_ROOT / "logs"
        log_used_mb = _deps._dir_size_mb(log_dir)
    except Exception as exc:
        log_error = str(exc)[:200]
    log_files: dict[str, Any] = {
        "pct": _deps._pct(log_used_mb, log_total_mb),
        "used_mb": round(log_used_mb, 2),
        "total_mb": log_total_mb,
    }
    if log_error:
        log_files["error"] = log_error

    return {
        "memory_store": memory_store,
        "sqlite_db": sqlite_db,
        "vector_index": vector_index,
        "log_files": log_files,
    }


@router.get("/api/system/diagnostics")
@handle_api_errors
async def api_system_diagnostics(request: Request) -> dict[str, Any]:
    """运行系统诊断：database / agent_registry / memory_store / vector_index / config_loader / audit_log。

    每项返回 {ok, result}；检查失败返回 {ok: false, result: str(exc)[:200]}。
    Personal 版 audit_log 降级为 {ok: true, result: "N/A (personal edition)"}。
    """
    require_admin(request)

    result: dict[str, dict[str, Any]] = {}

    # ── Database: 尝试 SELECT 1 ──
    try:
        import sqlite3
        db_path = _deps.get_db_path()
        if not db_path.exists():
            data_dir = _deps.MAOP_ROOT / "data"
            db_files = list(data_dir.glob("*.db")) if data_dir.exists() else []
            if db_files:
                db_path = db_files[0]
        if db_path.exists():
            conn = sqlite3.connect(str(db_path), timeout=2)
            try:
                cur = conn.execute("SELECT 1")
                cur.fetchone()
                result["database"] = {"ok": True, "result": f"OK ({db_path.name})"}
            finally:
                conn.close()
        else:
            result["database"] = {"ok": True, "result": "OK (no db file yet)"}
    except Exception as exc:
        result["database"] = {"ok": False, "result": str(exc)[:200]}

    # ── Agent Registry: 调用 list_agents() 返回数量 ──
    try:
        from maop.core.agent.lifecycle.agent_registry import AgentRegistry
        reg = AgentRegistry()
        agents = reg.list_agents() if hasattr(reg, "list_agents") else []
        count = len(agents) if hasattr(agents, "__len__") else 0
        result["agent_registry"] = {"ok": True, "result": f"{count} agents"}
    except Exception as exc:
        result["agent_registry"] = {"ok": False, "result": str(exc)[:200]}

    # ── Memory Store: try import 检查 ──
    try:
        from maop.core.reliability.cache import get_cache  # noqa: F401
        result["memory_store"] = {"ok": True, "result": "OK"}
    except Exception as exc:
        result["memory_store"] = {"ok": False, "result": str(exc)[:200]}

    # ── Vector Index: try import ──
    try:
        from maop.core.memory.vector import VectorStore  # noqa: F401
        result["vector_index"] = {"ok": True, "result": "OK"}
    except Exception as exc:
        result["vector_index"] = {"ok": False, "result": str(exc)[:200]}

    # ── Config Loader: try 加载 agents.yaml ──
    try:
        from maop.config.loader import ConfigLoader
        cfg = ConfigLoader(project_root=str(_deps.MAOP_ROOT)).load()
        agent_count = len(cfg.agents) if hasattr(cfg, "agents") else 0
        result["config_loader"] = {"ok": True, "result": f"OK ({agent_count} agents configured)"}
    except Exception as exc:
        result["config_loader"] = {"ok": False, "result": str(exc)[:200]}

    # ── Audit Log: try import maop.enterprise.audit；Personal 版降级 ──
    try:
        from maop.enterprise.audit import EnterpriseAuditLogger as _EntAudit  # noqa: F401
        result["audit_log"] = {"ok": True, "result": "OK (enterprise)"}
    except ImportError:
        result["audit_log"] = {"ok": True, "result": "N/A (personal edition)"}
    except Exception as exc:
        result["audit_log"] = {"ok": False, "result": str(exc)[:200]}

    return result