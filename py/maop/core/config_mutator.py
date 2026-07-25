"""MAOP Config Mutator — Apply evolution suggestions directly to agents.yaml.

Bridges the gap between EvolveEngine.apply() (which only marks suggestions)
and actual configuration changes. Reads suggestions and modifies agents.yaml
in-place, then triggers hot-reload.

Supported mutations:
  - routing_mismatch → Change primary/fallback/tertiary agent for a routing key
  - slow_agent → Adjust timeout_s for an agent
  - agent_low_success → Disable agent or adjust routing priority
  - empty_routing_key → Assign a default agent to an empty routing key

Usage::

    from maop.core.config_mutator import ConfigMutator

    mutator = ConfigMutator(root_dir="/path/to/MAOP")
    result = mutator.apply_suggestion("S001")
    print(result)  # {"applied": True, "changes": [...], "reloaded": True}
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel

from maop.core.filelock import FileLock
from maop.core.safe_writer import safe_write_text

logger = logging.getLogger(__name__)


class MutationResult(BaseModel):
    suggestion_id: str = ""
    applied: bool = False
    mutation_type: str = ""
    changes: list[str] = []
    backup_path: str = ""
    reloaded: bool = False
    error: str = ""


class ConfigMutator:
    """Apply evolution suggestions directly to agents.yaml.

    Reads the current agents.yaml, applies the specified mutation,
    writes back, and triggers ConfigHotReload if available.
    """

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._agents_yaml = self._root / "config" / "agents.yaml"
        self._suggestions_file = self._root / "data" / "evolve-suggestions.json"

    def apply_suggestion(self, suggestion_id: str) -> MutationResult:
        """Apply a single suggestion to agents.yaml."""
        suggestion = self._load_suggestion(suggestion_id)
        if not suggestion:
            return MutationResult(
                suggestion_id=suggestion_id,
                error=f"Suggestion {suggestion_id} not found",
            )

        if not suggestion.get("auto_applicable", False):
            return MutationResult(
                suggestion_id=suggestion_id,
                mutation_type=suggestion.get("type", ""),
                error="Suggestion is not auto-applicable",
            )

        if suggestion.get("applied", False):
            return MutationResult(
                suggestion_id=suggestion_id,
                mutation_type=suggestion.get("type", ""),
                error="Suggestion already applied",
            )

        mutation_type = suggestion.get("type", "")
        handler = {
            "routing_mismatch": self._mutate_routing,
            "slow_agent": self._mutate_timeout,
            "agent_low_success": self._mutate_disable_agent,
            "empty_routing_key": self._mutate_empty_routing,
        }.get(mutation_type)

        if not handler:
            return MutationResult(
                suggestion_id=suggestion_id,
                mutation_type=mutation_type,
                error=f"Unknown mutation type: {mutation_type}",
            )

        backup = self._backup_yaml()
        try:
            changes = handler(suggestion)
            self._mark_applied(suggestion_id)
            reloaded = self._trigger_reload()
            return MutationResult(
                suggestion_id=suggestion_id,
                applied=True,
                mutation_type=mutation_type,
                changes=changes,
                backup_path=str(backup),
                reloaded=reloaded,
            )
        except Exception as exc:
            if backup:
                shutil.copy2(backup, self._agents_yaml)
            return MutationResult(
                suggestion_id=suggestion_id,
                mutation_type=mutation_type,
                error=str(exc),
            )

    def _load_yaml(self) -> dict[str, Any]:
        """Load agents.yaml as a dict."""
        if not self._agents_yaml.exists():
            return {}
        import yaml
        with open(self._agents_yaml, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _save_yaml(self, data: dict[str, Any]) -> None:
        """原子写入 dict 到 agents.yaml，防止崩溃导致配置损坏。

        使用 safe_write_text 原子写入（先写临时文件 → fsync → os.replace），
        并通过 FileLock 文件锁防止并发写入冲突。写入后回读校验 YAML 合法性，
        若解析失败则抛出异常，由上层 apply_suggestion 的 backup 恢复机制处理。
        """
        import yaml
        content = yaml.safe_dump(
            data,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
        lock_path = self._agents_yaml.with_suffix(self._agents_yaml.suffix + ".lock")
        with FileLock(str(lock_path), timeout_seconds=10):
            safe_write_text(self._agents_yaml, content, encoding="utf-8")
            # 回读校验：确保写入的 YAML 可正常解析
            with open(self._agents_yaml, encoding="utf-8") as f:
                yaml.safe_load(f)  # 解析失败会抛异常，触发上层 backup 恢复

    def _backup_yaml(self) -> Path | None:
        """Create a timestamped backup of agents.yaml."""
        if not self._agents_yaml.exists():
            return None
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        backup = self._agents_yaml.with_name(f"agents.yaml.bak.{ts}")
        shutil.copy2(self._agents_yaml, backup)
        return backup

    def _load_suggestion(self, suggestion_id: str) -> dict[str, Any] | None:
        """Load a suggestion by ID from the evolve-suggestions.json file."""
        if not self._suggestions_file.exists():
            return None
        import json
        with open(self._suggestions_file, encoding="utf-8") as f:
            suggestions = json.load(f)
        for s in suggestions:
            if s.get("id") == suggestion_id:
                return cast(dict[str, Any] | None, s)
        return None

    def _mark_applied(self, suggestion_id: str) -> None:
        """Mark a suggestion as applied in the JSON file."""
        if not self._suggestions_file.exists():
            return
        import json
        with open(self._suggestions_file, encoding="utf-8") as f:
            suggestions = json.load(f)
        for s in suggestions:
            if s.get("id") == suggestion_id:
                s["applied"] = True
                break
        with open(self._suggestions_file, "w", encoding="utf-8") as f:
            json.dump(suggestions, f, indent=2, ensure_ascii=False)

    def _trigger_reload(self) -> bool:
        """Trigger ConfigHotReload if available."""
        try:
            from maop.config.hot_reload import ConfigHotReload
            reloader = ConfigHotReload(root_dir=str(self._root))
            reloader.reload()  # type: ignore[attr-defined]
            return True
        except Exception as exc:
            logger.debug("[mutator] Hot reload failed: %s", exc)
            return False

    def _mutate_routing(self, suggestion: dict[str, Any]) -> list[str]:
        """Change routing configuration for a routing key."""
        data = self._load_yaml()
        changes: list[str] = []

        routing = data.get("routing", {})
        key = suggestion.get("routing_key", "")
        new_agent = suggestion.get("suggested_agent", "")

        if not key or not new_agent:
            return changes

        if key in routing:
            old_primary = routing[key].get("primary", "")
            if old_primary != new_agent:
                routing[key]["fallback"] = old_primary
                routing[key]["primary"] = new_agent
                changes.append(f"routing.{key}.primary: {old_primary} → {new_agent}")
                changes.append(f"routing.{key}.fallback: → {old_primary}")
        else:
            routing[key] = {"primary": new_agent}
            changes.append(f"routing.{key}.primary: (new) → {new_agent}")

        data["routing"] = routing
        self._save_yaml(data)
        return changes

    def _mutate_timeout(self, suggestion: dict[str, Any]) -> list[str]:
        """Adjust timeout for a slow agent."""
        data = self._load_yaml()
        changes: list[str] = []

        agent_name = suggestion.get("agent", "")
        new_timeout = suggestion.get("suggested_timeout", 120)

        agents = data.get("agents", {})
        if agent_name in agents:
            old_timeout = agents[agent_name].get("timeout_s", 60)
            agents[agent_name]["timeout_s"] = new_timeout
            changes.append(f"agents.{agent_name}.timeout_s: {old_timeout} → {new_timeout}")
            data["agents"] = agents
            self._save_yaml(data)

        return changes

    def _mutate_disable_agent(self, suggestion: dict[str, Any]) -> list[str]:
        """Disable an agent with low success rate."""
        data = self._load_yaml()
        changes: list[str] = []

        agent_name = suggestion.get("agent", "")
        agents = data.get("agents", {})

        if agent_name in agents:
            agents[agent_name]["enabled"] = False
            changes.append(f"agents.{agent_name}.enabled: True → False")
            data["agents"] = agents
            self._save_yaml(data)

        return changes

    def _mutate_empty_routing(self, suggestion: dict[str, Any]) -> list[str]:
        """Assign a default agent to an empty routing key."""
        data = self._load_yaml()
        changes: list[str] = []

        key = suggestion.get("routing_key", "")
        agent = suggestion.get("suggested_agent", "")

        if key and agent:
            routing = data.get("routing", {})
            routing[key] = {"primary": agent}
            changes.append(f"routing.{key}.primary: (empty) → {agent}")
            data["routing"] = routing
            self._save_yaml(data)

        return changes
