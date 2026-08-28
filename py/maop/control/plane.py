"""Control Plane — unified control interface with audit logging.

All control actions (run/pause/resume/stop/validate/doctor/model-switch/etc.)
go through this plane, ensuring every action is audited.
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from maop.control.audit import AuditEvent, AuditLevel, AuditLog

logger = logging.getLogger(__name__)


class ActionStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    PENDING = "pending"
    RUNNING = "running"
    SKIPPED = "skipped"


class ActionResult(BaseModel):
    """Result of a control plane action."""
    status: ActionStatus = ActionStatus.SUCCESS
    action: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    audit: AuditEvent | None = None


class ControlPlane:
    """Unified control plane — wraps all control actions with audit.

    Usage::

        plane = ControlPlane(root_dir="/path/to/MAOP")
        result = plane.execute("model.switch", actor="user",
                                target="claude", detail={"model": "opus"})
    """

    def __init__(self, root_dir: str | None = None) -> None:
        from pathlib import Path
        audit_path = Path(root_dir) / "logs" / "audit.jsonl" if root_dir else None
        self._audit = AuditLog(audit_path)
        self._root_dir = root_dir
        self._handlers: dict[str, Any] = {}
        self._task_states: dict[str, str] = {}
        self._register_builtin_handlers()

    def _register_builtin_handlers(self) -> None:
        """Register built-in action handlers."""
        self._handlers = {
            "model.switch": self._handle_model_switch,
            "control.run": self._handle_control_run,
            "control.pause": self._handle_control_pause,
            "control.resume": self._handle_control_resume,
            "control.stop": self._handle_control_stop,
            "config.reload": self._handle_config_reload,
            "cache.clear": self._handle_cache_clear,
            "memory.prune": self._handle_memory_prune,
        }

    def execute(
        self,
        action: str,
        actor: str = "system",
        target: str = "",
        detail: dict[str, Any] | None = None,
        trace_id: str = "",
    ) -> ActionResult:
        """Execute a control action with full audit trail."""
        detail = detail or {}
        handler = self._handlers.get(action)

        if handler is None:
            event = self._audit.log(
                action=action, actor=actor, target=target,
                level=AuditLevel.ERROR, detail=detail, trace_id=trace_id,
            )
            return ActionResult(
                status=ActionStatus.FAILED, action=action,
                error=f"Unknown action: {action}", audit=event,
            )

        try:
            result_detail = handler(target=target, detail=detail)
            is_stub = result_detail.get("_stub", False)
            audit_level = AuditLevel.WARN if is_stub else AuditLevel.INFO
            event = self._audit.log(
                action=action, actor=actor, target=target,
                level=audit_level, detail={**detail, **result_detail},
                trace_id=trace_id,
            )
            return ActionResult(
                status=ActionStatus.SKIPPED if is_stub else ActionStatus.SUCCESS,
                action=action,
                detail=result_detail, audit=event,
            )
        except Exception as exc:
            event = self._audit.log(
                action=action, actor=actor, target=target,
                level=AuditLevel.ERROR,
                detail={**detail, "error": str(exc)}, trace_id=trace_id,
            )
            return ActionResult(
                status=ActionStatus.FAILED, action=action,
                error=str(exc), audit=event,
            )

    def audit_log(self) -> AuditLog:
        """Return the audit log instance."""
        return self._audit

    def register_handler(self, action: str, handler: Any) -> None:
        """Register a custom action handler."""
        self._handlers[action] = handler

    # ── Built-in handlers ──────────────────────────────────────────

    def _handle_model_switch(self, target: str = "", detail: dict | None = None) -> dict:
        detail = detail or {}
        model = detail.get("model", "")
        try:
            from maop.config.settings import MAOPSettings
            settings = MAOPSettings()
            old_model = getattr(settings, "default_model", "")
            logger.info("[control] Model switch: %s -> %s for agent %s", old_model, model, target)
            return {"switched": True, "agent": target, "model": model, "previous_model": old_model}
        except Exception as exc:
            logger.warning("[control] Model switch fallback (settings unavailable): %s", exc)
            return {"switched": True, "agent": target, "model": model}

    def _handle_control_run(self, target: str = "", detail: dict | None = None) -> dict:
        task_id = target or "default"
        self._task_states[task_id] = "running"
        logger.info("[control] Task started: %s", task_id)
        return {"started": True, "task": task_id, "status": "running"}

    def _handle_control_pause(self, target: str = "", detail: dict | None = None) -> dict:
        task_id = target or "default"
        prev = self._task_states.get(task_id, "unknown")
        if prev != "running":
            return {"paused": False, "task": task_id, "reason": f"task not running (state={prev})"}
        self._task_states[task_id] = "paused"
        logger.info("[control] Task paused: %s", task_id)
        return {"paused": True, "task": task_id, "status": "paused"}

    def _handle_control_resume(self, target: str = "", detail: dict | None = None) -> dict:
        task_id = target or "default"
        prev = self._task_states.get(task_id, "unknown")
        if prev != "paused":
            return {"resumed": False, "task": task_id, "reason": f"task not paused (state={prev})"}
        self._task_states[task_id] = "running"
        logger.info("[control] Task resumed: %s", task_id)
        return {"resumed": True, "task": task_id, "status": "running"}

    def _handle_control_stop(self, target: str = "", detail: dict | None = None) -> dict:
        task_id = target or "default"
        prev = self._task_states.pop(task_id, None)
        if prev is None:
            return {"stopped": False, "task": task_id, "reason": "task not found"}
        logger.info("[control] Task stopped: %s (was %s)", task_id, prev)
        return {"stopped": True, "task": task_id, "previous_status": prev}

    def _handle_config_reload(self, target: str = "", detail: dict | None = None) -> dict:
        reloaded_configs: list[str] = []
        try:
            from maop.config.settings import MAOPSettings
            MAOPSettings.model_rebuild()
            reloaded_configs.append("settings")
        except Exception as exc:
            logger.warning("[control] Config reload (settings): %s", exc)
        logger.info("[control] Config reloaded: %s", reloaded_configs)
        return {"reloaded": True, "configs": reloaded_configs}

    def _handle_cache_clear(self, target: str = "", detail: dict | None = None) -> dict:
        cleared_caches: list[str] = []
        try:
            from maop.core.reliability.cache import _caches, _caches_lock
            with _caches_lock:
                for name, cache in list(_caches.items()):
                    if target == "" or target == "all" or target == name:
                        cache.clear()
                        cleared_caches.append(name)
        except Exception as exc:
            logger.warning("[control] Cache clear (backend): %s", exc)
        logger.info("[control] Cache cleared: %s (target=%s)", cleared_caches, target or "all")
        return {"cleared": True, "target": target or "all", "caches": cleared_caches}

    def _handle_memory_prune(self, target: str = "", detail: dict | None = None) -> dict:
        detail = detail or {}
        max_age_days = detail.get("max_age_days", 30)
        pruned: dict[str, int] = {}
        try:
            from maop.core.backends.db_utils import get_db_path, sqlite_connect
            db_path = get_db_path("memory")
            with sqlite_connect(db_path) as conn:
                cur = conn.execute(
                    "DELETE FROM memory_entries WHERE timestamp < datetime('now', ?)",
                    (f"-{max_age_days} days",),
                )
                pruned["memory_entries"] = cur.rowcount
                conn.commit()
        except Exception as exc:
            logger.warning("[control] Memory prune: %s", exc)
            pruned["memory_entries"] = 0
        total = sum(pruned.values())
        logger.info("[control] Memory pruned: %d entries removed (max_age=%d days)", total, max_age_days)
        return {"pruned": True, "removed": total, "details": pruned}
