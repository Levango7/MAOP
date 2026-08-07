"""MAOP Hook Manager — Unified, pluggable lifecycle hook framework.

Provides:
  - HookManager: register/trigger hooks at key lifecycle points
  - HookType: callback (in-process) and webhook (async HTTP POST)
  - LifecycleEvent: canonical event names for agent/pipeline/loop phases
  - EventBus bridge: hooks ↔ EventBus bidirectional
  - YAML config: declare hooks in agents.yaml under `hooks:` section

Usage::

    from maop.core.agent.plugins_hooks.hook_manager import HookManager, LifecycleEvent

    mgr = HookManager()

    # Register a callback hook
    mgr.register(event=LifecycleEvent.AGENT_PRE_DISPATCH, callback=my_guard)

    # Register a webhook hook
    mgr.register(event=LifecycleEvent.LOOP_COMPLETE, url="https://example.com/hook")

    # Trigger hooks (returns list of HookResult)
    results = await mgr.trigger(LifecycleEvent.AGENT_PRE_DISPATCH, {"agent": "coder", "task": "fix"})

    # Bridge with EventBus
    mgr.bridge_event_bus(bus)
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


def _fail_open_default() -> bool:
    """Startup default: fail-open (do not break the chain on callback errors).

    Rationale: at startup, plugin callbacks may be misconfigured or import
    broken; blocking the agent pipeline on a single misbehaving hook would
    make the system unbootable. Operators may opt into strict mode by
    setting MAOP_HOOK_FAIL_MODE=closed.
    """
    return os.environ.get("MAOP_HOOK_FAIL_MODE", "open").lower() != "closed"


# ── Lifecycle Events ──────────────────────────────────────────────

class LifecycleEvent(str, Enum):
    """Canonical lifecycle events for MAOP hook points.

    Naming convention: <domain>.<phase>
      - agent.*   : agent dispatch lifecycle
      - loop.*    : MAOP loop phase transitions
      - step.*    : workflow step lifecycle
      - system.*  : system-level events
      - memory.*  : memory layer operations
      - model.*   : LLM model request/response
    """
    AGENT_PRE_DISPATCH = "agent.pre_dispatch"
    AGENT_POST_DISPATCH = "agent.post_dispatch"
    AGENT_ON_ERROR = "agent.on_error"
    AGENT_ON_TIMEOUT = "agent.on_timeout"
    LOOP_PRE_ANALYZE = "loop.pre_analyze"
    LOOP_POST_ANALYZE = "loop.post_analyze"
    LOOP_PRE_PLAN = "loop.pre_plan"
    LOOP_POST_PLAN = "loop.post_plan"
    LOOP_PRE_EXECUTE = "loop.pre_execute"
    LOOP_POST_EXECUTE = "loop.post_execute"
    LOOP_PRE_VERIFY = "loop.pre_verify"
    LOOP_POST_VERIFY = "loop.post_verify"
    LOOP_PRE_EVOLVE = "loop.pre_evolve"
    LOOP_POST_EVOLVE = "loop.post_evolve"
    LOOP_PRE_DREAM = "loop.pre_dream"
    LOOP_POST_DREAM = "loop.post_dream"
    LOOP_COMPLETE = "loop.complete"
    STEP_PRE_RUN = "step.pre_run"
    STEP_POST_RUN = "step.post_run"
    STEP_ON_FAIL = "step.on_fail"
    SYSTEM_START = "system.start"
    SYSTEM_STOP = "system.stop"
    SYSTEM_CONFIG_RELOAD = "system.config_reload"
    GUARDRAIL_PRE_CHECK = "guardrail.pre_check"
    GUARDRAIL_POST_CHECK = "guardrail.post_check"
    CIRCUIT_BREAKER_OPEN = "circuit_breaker.open"
    CIRCUIT_BREAKER_CLOSE = "circuit_breaker.close"
    MEMORY_WRITE = "memory.write"
    MEMORY_READ = "memory.read"
    MEMORY_CONSOLIDATE = "memory.consolidate"
    AGENT_SPAWN = "agent.spawn"
    AGENT_COMPLETE = "agent.complete"
    AGENT_EVOLVE = "agent.evolve"
    MODEL_REQUEST_BEFORE = "model.request_before"
    MODEL_RESPONSE_AFTER = "model.response_after"


class HookType(str, Enum):
    CALLBACK = "callback"
    WEBHOOK = "webhook"


class HookPhase(str, Enum):
    BEFORE = "before"
    AFTER = "after"
    ON_ERROR = "on_error"


# ── Models ────────────────────────────────────────────────────────

class HookDef(BaseModel):
    id: str = ""
    event: str
    hook_type: HookType = HookType.CALLBACK
    callback: str = ""  # human-readable name (e.g. "mod.func")
    callback_path: str = ""  # dotted import path for persistence reload
    url: str = ""
    enabled: bool = True
    priority: int = 0
    description: str = ""
    created_at: str = ""
    source: str = "api"


class HookResult(BaseModel):
    hook_id: str
    event: str
    success: bool = True
    error: str = ""
    duration_ms: int = 0
    response: str = ""
    decision: str = "allow"  # allow | deny | modify
    modified_data: dict[str, Any] = Field(default_factory=dict)


class HookTriggerStats(BaseModel):
    event: str
    hooks_invoked: int = 0
    hooks_succeeded: int = 0
    hooks_failed: int = 0
    total_duration_ms: int = 0


# ── Hook Manager ──────────────────────────────────────────────────

class HookManager:
    """Unified lifecycle hook manager with callback + webhook support.

    Features:
      - Register hooks via callback (Python callable) or webhook (HTTP URL)
      - Wildcard event matching (e.g. "agent.*" matches all agent events)
      - Priority-ordered execution
      - Async webhook delivery with timeout
      - SQLite persistence for hook definitions
      - EventBus bidirectional bridge
      - YAML config loading
    """

    def __init__(self, root_dir: str | Path = "data") -> None:
        self._root = Path(root_dir)
        self._db_path = get_db_path("hook_manager")
        self._callbacks: dict[str, list[tuple[str, Callable[..., Any]]]] = {}
        self._event_bus = None
        # Startup default: fail-open. Operators can flip to strict via env var.
        self._fail_open = _fail_open_default()
        self._init_db()
        # Rehydrate callbacks from persisted callback_path entries so that
        # hooks registered in a previous process can still fire after restart.
        self._reload_persisted_callbacks()

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS hooks (
                    id TEXT PRIMARY KEY,
                    event TEXT NOT NULL,
                    hook_type TEXT NOT NULL DEFAULT 'callback',
                    callback TEXT DEFAULT '',
                    callback_path TEXT DEFAULT '',
                    url TEXT DEFAULT '',
                    enabled INTEGER DEFAULT 1,
                    priority INTEGER DEFAULT 0,
                    description TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    source TEXT DEFAULT 'api'
                )
            """)
            # Schema migration: add callback_path column if missing (older DBs).
            try:
                cols = [r[1] for r in conn.execute("PRAGMA table_info(hooks)").fetchall()]
                if "callback_path" not in cols:
                    conn.execute("ALTER TABLE hooks ADD COLUMN callback_path TEXT DEFAULT ''")
            except Exception:
                logger.debug("Silent exception in core/hook_manager.py:206", exc_info=True)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS hook_logs (
                    id TEXT PRIMARY KEY,
                    hook_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    success INTEGER DEFAULT 1,
                    error TEXT DEFAULT '',
                    duration_ms INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_hooks_event
                ON hooks(event, enabled)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_hook_logs_event
                ON hook_logs(event, created_at)
            """)

    def _reload_persisted_callbacks(self) -> None:
        """Reload callbacks from persisted ``callback_path`` entries.

        Hooks registered in a previous process (or via YAML) carry a dotted
        import path in ``callback_path``. On HookManager construction we
        resolve these paths back to callables so the hooks can fire again.

        Non-reloadable hooks (closures, lambdas) have empty ``callback_path``
        and are silently skipped — they will be re-registered by the owning
        component when it starts up.
        """
        try:
            with sqlite_connect(self._db_path) as conn:
                cols = [r[1] for r in conn.execute("PRAGMA table_info(hooks)").fetchall()]
                if "callback_path" not in cols:
                    return  # nothing to reload on legacy schema
                rows = conn.execute(
                    "SELECT id, event, callback_path FROM hooks WHERE callback_path != ''"
                ).fetchall()
        except Exception as exc:
            logger.warning("[hook] Reload: failed to read persisted callbacks: %s", exc)
            return

        reloaded = 0
        for r in rows:
            hid = r["id"]
            event = r["event"]
            cb_path = r["callback_path"]
            # Skip if already in-memory (re-register on same process).
            if any(h == hid for h, _ in self._callbacks.get(event, [])):
                continue
            cb = self._resolve_callback(cb_path)
            if cb is None:
                logger.warning(
                    "[hook] Reload: cannot resolve '%s' for hook '%s'; "
                    "will fail-open at trigger time", cb_path, hid,
                )
                continue
            self._callbacks.setdefault(event, []).append((hid, cb))
            reloaded += 1
        if reloaded:
            logger.info("[hook] Reloaded %d persisted callbacks", reloaded)

    def set_fail_open(self, fail_open: bool) -> None:
        """Override the fail-open/closed policy at runtime (e.g. from config)."""
        self._fail_open = fail_open

    # ── Register ───────────────────────────────────────────────

    def register(
        self,
        event: str,
        *,
        callback: Callable[..., Any] | None = None,
        url: str = "",
        priority: int = 0,
        description: str = "",
        source: str = "api",
        hook_id: str = "",
    ) -> HookDef:
        """Register a hook for a lifecycle event.

        Parameters
        ----------
        event : str
            Lifecycle event name (e.g. "agent.pre_dispatch") or wildcard ("agent.*").
        callback : callable | None
            Python callable for in-process hooks. Receives (event, data) args.
        url : str
            HTTP URL for async webhook delivery.
        priority : int
            Higher priority hooks run first. Default 0.
        description : str
            Human-readable description.
        source : str
            Origin of the hook registration ("api", "yaml", "code").
        hook_id : str
            Custom ID; auto-generated if empty.
        """
        if callback is None and not url:
            raise ValueError("Must provide either callback or url")

        htype = HookType.WEBHOOK if url else HookType.CALLBACK
        hid = hook_id or f"hk-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()

        cb_name = ""
        cb_path = ""
        if callback:
            cb_name = (
                f"{callback.__module__}.{callback.__qualname__}"
                if hasattr(callback, "__module__")
                else str(callback)
            )
            # Persist a dotted import path so we can rehydrate on restart.
            # For closures/lambdas, callback_path stays "" (non-reloadable).
            qualname = getattr(callback, "__qualname__", "")
            module = getattr(callback, "__module__", "")
            if qualname and "<locals>" not in qualname and "<lambda>" not in qualname and module:
                cb_path = f"{module}.{qualname}"
            event_key = event
            if event_key not in self._callbacks:
                self._callbacks[event_key] = []
            self._callbacks[event_key].append((hid, callback))

        hdef = HookDef(
            id=hid, event=event, hook_type=htype,
            callback=cb_name, callback_path=cb_path, url=url, enabled=True,
            priority=priority, description=description,
            created_at=now, source=source,
        )

        with sqlite_connect(self._db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO hooks (id, event, hook_type, callback, callback_path, url, enabled, priority, description, created_at, source) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (hdef.id, hdef.event, hdef.hook_type.value,
                 hdef.callback, hdef.callback_path, hdef.url, 1 if hdef.enabled else 0,
                 hdef.priority, hdef.description, hdef.created_at, hdef.source),
            )

        logger.info("[hook] Registered %s hook '%s' for event '%s'", htype.value, hid, event)
        return hdef

    def unregister(self, hook_id: str) -> bool:
        """Remove a hook registration."""
        with sqlite_connect(self._db_path) as conn:
            row = conn.execute("SELECT event, callback FROM hooks WHERE id=?", (hook_id,)).fetchone()
            if row is None:
                return False
            conn.execute("DELETE FROM hooks WHERE id=?", (hook_id,))
        if row["event"] in self._callbacks:
            self._callbacks[row["event"]] = [
                (hid, cb) for hid, cb in self._callbacks[row["event"]] if hid != hook_id
            ]
        logger.info("[hook] Unregistered hook '%s'", hook_id)
        return True

    def enable(self, hook_id: str) -> bool:
        with sqlite_connect(self._db_path) as conn:
            cursor = conn.execute("UPDATE hooks SET enabled=1 WHERE id=?", (hook_id,))
        return cursor.rowcount > 0

    def disable(self, hook_id: str) -> bool:
        with sqlite_connect(self._db_path) as conn:
            cursor = conn.execute("UPDATE hooks SET enabled=0 WHERE id=?", (hook_id,))
        return cursor.rowcount > 0

    # ── Trigger ────────────────────────────────────────────────

    async def trigger(self, event: str, data: dict[str, Any] | None = None) -> list[HookResult]:
        """Trigger all hooks matching an event with chained data propagation.

        Hooks execute in priority order (highest first). Each hook's
        ``modified_data`` is merged into the payload for the next hook.
        If a hook returns ``decision="deny"``, the chain stops immediately.

        Supports wildcard matching: a hook registered for "agent.*" matches
        "agent.pre_dispatch", "agent.post_dispatch", etc.
        """
        payload = data or {}
        hooks = self._get_matching_hooks(event)
        if not hooks:
            return []

        results = []
        for hdef in hooks:
            if not hdef.enabled:
                continue
            start = time.monotonic()
            try:
                if hdef.hook_type == HookType.CALLBACK:
                    result = await self._invoke_callback(hdef, event, payload)
                else:
                    result = await self._invoke_webhook(hdef, event, payload)
                duration_ms = int((time.monotonic() - start) * 1000)
                result.duration_ms = duration_ms
            except Exception as exc:
                duration_ms = int((time.monotonic() - start) * 1000)
                result = HookResult(
                    hook_id=hdef.id, event=event,
                    success=False, error=str(exc), duration_ms=duration_ms,
                )
                if not self._fail_open:
                    # Fail-closed: log, persist, and re-raise immediately.
                    self._log_result(result)
                    logger.error(
                        "[hook] Fail-closed: hook '%s' raised %s; aborting chain", hdef.id, exc
                    )
                    raise
            results.append(result)
            self._log_result(result)

            # Chain propagation: merge modified_data into payload for next hook
            if result.success and result.modified_data:
                payload = {**payload, **result.modified_data}

            # Chain break: deny decision stops the chain
            if result.decision == "deny":
                logger.info("[hook] Chain broken by %s (deny)", hdef.id)
                break

            # Fail-closed: a failed hook also breaks the chain (deny-style).
            if not self._fail_open and not result.success:
                logger.info("[hook] Fail-closed: chain stopped on hook '%s' failure", hdef.id)
                break

        if self._event_bus:
            try:
                from maop.core.reliability.event_bus import Event
                await self._event_bus.publish(Event(
                    topic=f"hook.{event}",
                    data={"event": event, "results": [r.model_dump() for r in results], "final_payload": payload},
                    source="hook_manager",
                ))
            except Exception:
                logger.debug("Silent exception in core/hook_manager.py:443", exc_info=True)

        return results

    async def _invoke_callback(self, hdef: HookDef, event: str, data: dict[str, Any]) -> HookResult:
        cbs = self._callbacks.get(hdef.event, []) + self._callbacks.get(self._find_wildcard_key(hdef.event), [])
        target = None
        for hid, cb in cbs:
            if hid == hdef.id:
                target = cb
                break
        if target is None:
            return HookResult(hook_id=hdef.id, event=event, success=False, error="Callback not found in memory")

        import inspect
        result_data = None
        if inspect.iscoroutinefunction(target):
            result_data = await target(event, data)
        else:
            result_data = target(event, data)

        if result_data is None:
            return HookResult(hook_id=hdef.id, event=event, success=True)

        if isinstance(result_data, HookResult):
            return result_data
        if isinstance(result_data, dict):
            decision = result_data.get("decision", "allow")
            modified = result_data.get("modified_data", {})
            return HookResult(
                hook_id=hdef.id, event=event, success=True,
                decision=decision, modified_data=modified,
            )
        return HookResult(hook_id=hdef.id, event=event, success=True)

    async def _invoke_webhook(self, hdef: HookDef, event: str, data: dict[str, Any]) -> HookResult:
        try:
            import httpx
        except ImportError:
            return HookResult(hook_id=hdef.id, event=event, success=False, error="httpx not installed")

        payload = {"event": event, "data": data, "hook_id": hdef.id, "timestamp": datetime.now(timezone.utc).isoformat()}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(hdef.url, json=payload)
            return HookResult(
                hook_id=hdef.id, event=event,
                success=200 <= resp.status_code < 300,
                response=f"HTTP {resp.status_code}",
            )
        except Exception as exc:
            return HookResult(hook_id=hdef.id, event=event, success=False, error=str(exc))

    # ── Query ──────────────────────────────────────────────────

    def list_hooks(self, event: str = "") -> list[HookDef]:
        with sqlite_connect(self._db_path) as conn:
            if event:
                rows = conn.execute(
                    "SELECT * FROM hooks WHERE event=? ORDER BY priority DESC", (event,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM hooks ORDER BY event, priority DESC").fetchall()
        return [HookDef(
            id=r["id"], event=r["event"], hook_type=HookType(r["hook_type"]),
            callback=r["callback"], url=r["url"], enabled=bool(r["enabled"]),
            priority=r["priority"], description=r["description"],
            created_at=r["created_at"], source=r["source"],
        ) for r in rows]

    def get_hook(self, hook_id: str) -> HookDef | None:
        with sqlite_connect(self._db_path) as conn:
            row = conn.execute("SELECT * FROM hooks WHERE id=?", (hook_id,)).fetchone()
        if row is None:
            return None
        return HookDef(
            id=row["id"], event=row["event"], hook_type=HookType(row["hook_type"]),
            callback=row["callback"], url=row["url"], enabled=bool(row["enabled"]),
            priority=row["priority"], description=row["description"],
            created_at=row["created_at"], source=row["source"],
        )

    def get_logs(self, event: str = "", limit: int = 100) -> list[dict]:
        with sqlite_connect(self._db_path) as conn:
            if event:
                rows = conn.execute(
                    "SELECT * FROM hook_logs WHERE event=? ORDER BY created_at DESC LIMIT ?",
                    (event, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM hook_logs ORDER BY created_at DESC LIMIT ?", (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    # ── EventBus Bridge ────────────────────────────────────────

    def bridge_event_bus(self, bus: Any) -> None:
        """Bridge HookManager with EventBus.

        - Hook trigger events are published to EventBus as "hook.<event>"
        - EventBus events matching lifecycle patterns trigger hooks
        """
        self._event_bus = bus
        for le in LifecycleEvent:
            with contextlib.suppress(Exception):
                bus.subscribe(
                    le.value,
                    self._make_bus_subscriber(le.value),
                    max_retries=0,
                )
        logger.info("[hook] Bridged with EventBus")

    def _make_bus_subscriber(self, event: str) -> Callable[..., Any]:
        async def _on_bus_event(evt: Any) -> None:
            try:
                await self.trigger(event, evt.data if hasattr(evt, "data") else {})
            except Exception as exc:
                logger.warning("[hook] EventBus→Hook bridge error for %s: %s", event, exc)
        return _on_bus_event

    # ── YAML Config Loading ────────────────────────────────────

    def load_from_yaml(self, yaml_path: str | Path) -> int:
        """Load hook definitions from a YAML file's `hooks:` section.

        YAML format::

            hooks:
              - event: agent.pre_dispatch
                url: https://example.com/guard
                priority: 10
              - event: loop.complete
                callback: my_module.my_function
                description: "Post-loop audit"
        """
        import yaml as _yaml
        path = Path(yaml_path)
        if not path.exists():
            return 0
        try:
            raw = path.read_text(encoding="utf-8")
            data = _yaml.safe_load(raw) or {}
        except Exception as exc:
            logger.error("[hook] Failed to load YAML: %s", exc)
            return 0

        hook_list = data.get("hooks", [])
        if not isinstance(hook_list, list):
            return 0

        loaded = 0
        for entry in hook_list:
            if not isinstance(entry, dict) or "event" not in entry:
                continue
            event = entry["event"]
            url = entry.get("url", "")
            callback_path = entry.get("callback", "")
            priority = entry.get("priority", 0)
            description = entry.get("description", "")

            if url:
                self.register(
                    event=event, url=url, priority=priority,
                    description=description, source="yaml",
                )
                loaded += 1
            elif callback_path:
                cb = self._resolve_callback(callback_path)
                if cb:
                    self.register(
                        event=event, callback=cb, priority=priority,
                        description=description, source="yaml",
                    )
                    loaded += 1
                else:
                    logger.warning("[hook] Cannot resolve callback: %s", callback_path)

        logger.info("[hook] Loaded %d hooks from %s", loaded, path)
        return loaded

    def _resolve_callback(self, dotted_path: str) -> Callable[..., Any] | None:
        """Resolve a dotted path like 'my_module.my_func' to a callable."""
        try:
            parts = dotted_path.rsplit(".", 1)
            if len(parts) != 2:
                return None
            import importlib
            mod = importlib.import_module(parts[0])
            return cast(Callable[..., Any] | None, getattr(mod, parts[1]))
        except Exception:
            logger.debug("Silent exception in core/hook_manager.py:634", exc_info=True)
            return None

    # ── Internal helpers ───────────────────────────────────────

    def _get_matching_hooks(self, event: str) -> list[HookDef]:
        with sqlite_connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM hooks WHERE enabled=1 ORDER BY priority DESC",
            ).fetchall()
        results = []
        for r in rows:
            hook_event = r["event"]
            if hook_event == event or self._wildcard_match(hook_event, event):
                results.append(HookDef(
                    id=r["id"], event=r["event"], hook_type=HookType(r["hook_type"]),
                    callback=r["callback"], url=r["url"], enabled=True,
                    priority=r["priority"], description=r["description"],
                    created_at=r["created_at"], source=r["source"],
                ))
        return results

    @staticmethod
    def _wildcard_match(pattern: str, event: str) -> bool:
        if not pattern.endswith(".*"):
            return False
        prefix = pattern[:-2]
        return event.startswith(prefix + ".")

    def _find_wildcard_key(self, event: str) -> str:
        parts = event.rsplit(".", 1)
        if len(parts) == 2:
            return parts[0] + ".*"
        return ""

    def _log_result(self, result: HookResult) -> None:
        now = datetime.now(timezone.utc).isoformat()
        log_id = f"hl-{uuid.uuid4().hex[:8]}"
        try:
            with sqlite_connect(self._db_path) as conn:
                conn.execute(
                    "INSERT INTO hook_logs (id, hook_id, event, success, error, duration_ms, created_at) VALUES (?,?,?,?,?,?,?)",
                    (log_id, result.hook_id, result.event,
                     1 if result.success else 0, result.error,
                     result.duration_ms, now),
                )
        except Exception:
            logger.debug("Silent exception in core/hook_manager.py:680", exc_info=True)


# ── Global singleton ──────────────────────────────────────────────

_hook_manager: HookManager | None = None


def get_hook_manager(root_dir: str | Path | None = None) -> HookManager:
    global _hook_manager
    if _hook_manager is None:
        _hook_manager = HookManager(root_dir=root_dir or "data")
    return _hook_manager
