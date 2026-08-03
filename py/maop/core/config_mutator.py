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
from collections.abc import Callable
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

        mutation_type = suggestion.get("mutation_type", "") or suggestion.get("type", "")
        handler = {
            "change_routing": self._mutate_routing,
            "routing_mismatch": self._mutate_routing,  # 向后兼容
            "adjust_timeout": self._mutate_timeout,
            "slow_agent": self._mutate_timeout,  # 向后兼容
            "disable_agent": self._mutate_disable_agent,
            "agent_low_success": self._mutate_disable_agent,  # 向后兼容
            "change_routing_empty": self._mutate_empty_routing,
            "empty_routing_key": self._mutate_empty_routing,  # 向后兼容
            "add_capability": self._mutate_add_capability,
            "adjust_retries": self._mutate_adjust_retries,
            "adjust_cache": self._mutate_adjust_cache,
            "switch_model": self._mutate_switch_model,
            "record_lesson": self._mutate_record_lesson,
            "record_preference": self._mutate_record_preference,
            "error_pattern_rule": self._mutate_record_lesson,
            "recurring_failure": self._mutate_record_lesson,  # 向后兼容 (旧 EvolveEngine 类型名)
        }.get(mutation_type)

        if not handler:
            return MutationResult(
                suggestion_id=suggestion_id,
                mutation_type=mutation_type,
                error=f"Unknown mutation type: {mutation_type}",
            )

        backup = self._backup_yaml()
        try:
            # C4/C5 fix: hold FileLock for the entire read-modify-write cycle
            # to prevent concurrent lost-update on agents.yaml.
            _lock_path = str(self._agents_yaml) + ".lock"
            with FileLock(_lock_path, timeout_seconds=10):
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
        safe_write_text(self._agents_yaml, content, encoding="utf-8")
        with open(self._agents_yaml, encoding="utf-8") as f:
            yaml.safe_load(f)  # readback validate (caller holds FileLock)

    def _atomic_yaml_update(
        self,
        apply_fn: Callable[[dict[str, Any]], list[str]],
    ) -> list[str]:
        """C4/C5 fix: atomically read-modify-write agents.yaml under one FileLock.

        Previous _load_yaml() + _save_yaml() only locked the write phase.
        Two concurrent callers could both read the same old data, modify
        different fields, and write sequentially — the second write overwrites
        the first (lost update). This method does read-modify-write under
        a single FileLock.
        """
        import yaml
        lock_path = str(self._agents_yaml) + ".lock"
        with FileLock(lock_path, timeout_seconds=10):
            if self._agents_yaml.exists():
                with open(self._agents_yaml, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
            else:
                data = {}
            changes = apply_fn(data)
            if not changes:
                return changes
            content = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
            safe_write_text(self._agents_yaml, content, encoding="utf-8")
            with open(self._agents_yaml, encoding="utf-8") as f:
                yaml.safe_load(f)
            return changes

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
            reloader.force_reload()
            return True
        except Exception as exc:
            logger.debug("[mutator] Hot reload failed: %s", exc)
            return False

    def _mutate_routing(self, suggestion: dict[str, Any]) -> list[str]:
        """调整路由配置: 将表现不佳的 agent 降级为 fallback，而非禁用。

        如果提供了 suggested_agent 则切换 primary；
        否则仅将当前 agent 从 primary 降为 fallback。
        """
        data = self._load_yaml()
        changes: list[str] = []

        routing = data.get("routing", {})
        params = suggestion.get("mutation_params", {})
        key = params.get("routing_key", "") or suggestion.get("routing_key", "")
        new_agent = params.get("suggested_agent", "") or suggestion.get("suggested_agent", "")
        current_agent = params.get("agent", "") or suggestion.get("agent", "")

        if not key:
            return changes

        if key in routing:
            old_primary = routing[key].get("primary", "")
            if new_agent and old_primary != new_agent:
                routing[key]["fallback"] = old_primary
                routing[key]["primary"] = new_agent
                changes.append(f"routing.{key}.primary: {old_primary} → {new_agent}")
                changes.append(f"routing.{key}.fallback: → {old_primary}")
            elif current_agent and old_primary == current_agent:
                # 将表现不佳的 agent 降为 fallback，提升原 fallback
                old_fallback = routing[key].get("fallback", "")
                if old_fallback:
                    routing[key]["primary"] = old_fallback
                    routing[key]["fallback"] = current_agent
                    changes.append(f"routing.{key}.primary: {old_primary} → {old_fallback} (demoted {current_agent})")
        else:
            if new_agent:
                routing[key] = {"primary": new_agent}
                changes.append(f"routing.{key}.primary: (new) → {new_agent}")

        data["routing"] = routing
        self._save_yaml(data)
        return changes

    def _mutate_timeout(self, suggestion: dict[str, Any]) -> list[str]:
        """增加慢 agent 的 timeout (而非减半)。"""
        data = self._load_yaml()
        changes: list[str] = []

        params = suggestion.get("mutation_params", {})
        agent_name = params.get("agent", "") or suggestion.get("agent", "")
        new_timeout = params.get("suggested_timeout", 0) or suggestion.get("suggested_timeout", 0)

        if not agent_name:
            return changes

        agents = data.get("agents", {})
        if agent_name in agents:
            old_timeout = agents[agent_name].get("timeout_s", 60)
            if new_timeout <= 0:
                # 如果未提供 suggested_timeout，增加 50%
                new_timeout = min(600, int(old_timeout * 1.5))
            if new_timeout > old_timeout:
                agents[agent_name]["timeout_s"] = new_timeout
                changes.append(f"agents.{agent_name}.timeout_s: {old_timeout} → {new_timeout}")
                data["agents"] = agents
                self._save_yaml(data)

        return changes

    def _mutate_disable_agent(self, suggestion: dict[str, Any]) -> list[str]:
        """禁用成功率极低的 agent。"""
        data = self._load_yaml()
        changes: list[str] = []

        params = suggestion.get("mutation_params", {})
        agent_name = params.get("agent", "") or suggestion.get("agent", "")
        agents = data.get("agents", {})

        if agent_name in agents and agents[agent_name].get("enabled", True):
            agents[agent_name]["enabled"] = False
            changes.append(f"agents.{agent_name}.enabled: True → False")
            data["agents"] = agents
            self._save_yaml(data)

        return changes

    def _mutate_empty_routing(self, suggestion: dict[str, Any]) -> list[str]:
        """为空路由键分配默认 agent。"""
        data = self._load_yaml()
        changes: list[str] = []

        params = suggestion.get("mutation_params", {})
        key = params.get("routing_key", "") or suggestion.get("routing_key", "")
        agent = params.get("suggested_agent", "") or suggestion.get("suggested_agent", "")

        if key and agent:
            routing = data.get("routing", {})
            routing[key] = {"primary": agent}
            changes.append(f"routing.{key}.primary: (empty) → {agent}")
            data["routing"] = routing
            self._save_yaml(data)

        return changes

    def _mutate_add_capability(self, suggestion: dict[str, Any]) -> list[str]:
        """为 agent 添加新能力标签 (只增不删)。"""
        data = self._load_yaml()
        changes: list[str] = []

        params = suggestion.get("mutation_params", {})
        agent_name = params.get("agent", "") or suggestion.get("agent", "")
        new_cap = params.get("suggested_capability", "")

        if not agent_name or not new_cap:
            return changes

        agents = data.get("agents", {})
        if agent_name in agents:
            caps = agents[agent_name].get("capabilities", [])
            if new_cap not in caps:
                caps.append(new_cap)
                agents[agent_name]["capabilities"] = caps
                changes.append(f"agents.{agent_name}.capabilities: +{new_cap}")
                data["agents"] = agents
                self._save_yaml(data)

        # 同时记录到 agent 记忆
        try:
            from maop.core.agent_memory import AgentMemory
            mem = AgentMemory(root_dir=str(self._root))
            mem.store(agent_name=agent_name, memory_type="lesson",
                      content={"type": "capability_added", "capability": new_cap},
                      importance=0.7)
        except Exception:
            pass

        return changes

    def _mutate_adjust_retries(self, suggestion: dict[str, Any]) -> list[str]:
        """调整 agent 的 max_retries。"""
        data = self._load_yaml()
        changes: list[str] = []

        params = suggestion.get("mutation_params", {})
        agent_name = params.get("agent", "") or suggestion.get("agent", "")
        new_retries = params.get("suggested_max_retries", 5)

        if not agent_name:
            return changes

        agents = data.get("agents", {})
        if agent_name in agents:
            old_retries = agents[agent_name].get("max_retries", 3)
            agents[agent_name]["max_retries"] = new_retries
            changes.append(f"agents.{agent_name}.max_retries: {old_retries} → {new_retries}")
            data["agents"] = agents
            self._save_yaml(data)

        return changes

    def _mutate_adjust_cache(self, suggestion: dict[str, Any]) -> list[str]:
        """调整缓存参数 (TTL/max_size/similarity_threshold)。"""
        changes: list[str] = []
        params = suggestion.get("mutation_params", {})
        cache_name = params.get("cache_name", "")
        parameter = params.get("parameter", "")
        new_value = params.get("new_value", 0)

        if not cache_name or not parameter:
            return changes

        try:
            from maop.core.cache import get_cache
            cache = get_cache(cache_name)
            if cache and hasattr(cache, parameter):
                old_value = getattr(cache, parameter)
                setattr(cache, parameter, new_value)
                changes.append(f"cache.{cache_name}.{parameter}: {old_value} → {new_value}")
        except Exception as exc:
            logger.debug("[mutator] Cache adjustment failed: %s", exc)

        return changes

    def _mutate_switch_model(self, suggestion: dict[str, Any]) -> list[str]:
        """切换 agent 使用的模型 (需要手动确认)。"""
        changes: list[str] = []
        # 模型切换是高风险操作，只记录建议不自动执行
        params = suggestion.get("mutation_params", {})
        model = params.get("model", "")
        total_cost = params.get("total_cost", 0)
        logger.info("[mutator] Model switch suggestion: %s (cost $%.2f) — manual review required", model, total_cost)
        return changes

    def _mutate_record_lesson(self, suggestion: dict[str, Any]) -> list[str]:
        """记录教训到 agent 记忆 (不修改配置文件)。"""
        changes: list[str] = []
        params = suggestion.get("mutation_params", {})
        agent_name = params.get("agent", "")
        pattern = params.get("pattern", "")
        error = params.get("error", "")
        root_cause = params.get("root_cause", "")

        try:
            from maop.core.agent_memory import AgentMemory
            mem = AgentMemory(root_dir=str(self._root))
            if agent_name:
                mem.store(agent_name=agent_name, memory_type="lesson",
                          content={"type": "error_lesson", "pattern": pattern, "error": error,
                                   "root_cause": root_cause, "description": suggestion.get("description", "")},
                          importance=0.8)
            changes.append(f"Recorded lesson for agent '{agent_name}': {pattern or error}")
        except Exception as exc:
            logger.debug("[mutator] Record lesson failed: %s", exc)

        return changes

    def _mutate_record_preference(self, suggestion: dict[str, Any]) -> list[str]:
        """记录用户偏好到 agent 记忆 (不直接修改配置)。"""
        changes: list[str] = []
        params = suggestion.get("mutation_params", {})
        agent_name = params.get("agent", "")
        parameter = params.get("parameter", "")
        suggested_value = params.get("suggested_default", "")

        try:
            from maop.core.agent_memory import AgentMemory
            mem = AgentMemory(root_dir=str(self._root))
            if agent_name:
                mem.store(agent_name=agent_name, memory_type="preference",
                          content={"type": "suggested_default", "parameter": parameter,
                                   "value": suggested_value, "auto_generated": True},
                          importance=0.6)
            changes.append(f"Recorded preference for agent '{agent_name}': {parameter}={suggested_value}")
        except Exception as exc:
            logger.debug("[mutator] Record preference failed: %s", exc)

        return changes
