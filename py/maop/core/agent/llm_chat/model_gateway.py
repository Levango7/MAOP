"""MAOP Model Gateway — 模型授权网关。

控制哪些模型可以被哪些 Agent/Session 使用，转发请求到
LLMProviderFactory，并提供计费 hook。

核心职责：
  - 基于通配符模式（fnmatch）的模型权限规则匹配
  - 单次调用 Token 上限 / 每日 Token 限额 / 全局每日 Token 限额检查
  - 权限规则与使用量持久化到 SQLite
  - 线程安全（RLock 保护共享状态）

Public symbols:
  - ModelPermission
  - ModelGatewayConfig
  - ModelAccessDecision
  - ModelGateway
"""

from __future__ import annotations

import fnmatch
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


# ── Pydantic Models ────────────────────────────────────────────────


class ModelPermission(BaseModel):
    """模型权限规则。

    通过 ``model_pattern``（支持通配符，如 "gpt-4*"、"claude-*"、"*"）
    匹配模型名，决定是否允许访问，并附加可选的 Token 限额。
    """

    model_pattern: str
    allowed: bool = True
    max_tokens_per_call: int = 0  # 0 = 不限
    daily_token_limit: int = 0  # 0 = 不限
    priority: int = 100  # 越小越优先


class ModelGatewayConfig(BaseModel):
    """模型网关配置。"""

    default_policy: str = "allow"  # "allow" | "deny"
    permissions: list[ModelPermission] = Field(default_factory=list)
    global_daily_token_limit: int = 0  # 0 = 不限
    enable_quota_check: bool = True


class ModelAccessDecision(BaseModel):
    """访问决策结果。"""

    allowed: bool
    model: str
    reason: str = ""
    matched_permission: ModelPermission | None = None
    max_tokens_per_call: int = 0
    remaining_daily_tokens: int = -1  # -1 = 不限


# ── ModelGateway ───────────────────────────────────────────────────


class ModelGateway:
    """模型授权网关。

    控制哪些模型可以被哪些 Agent/Session 使用，提供权限规则管理、
    使用量记录与限额检查。权限规则和使用量持久化到 SQLite。
    """

    def __init__(
        self,
        config: ModelGatewayConfig | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        self._config: ModelGatewayConfig = config or ModelGatewayConfig()
        self._db_path: Path = Path(db_path) if db_path else get_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock: threading.RLock = threading.RLock()
        # "date:model" -> token count
        self._daily_usage: dict[str, int] = {}
        self._init_db()
        self._load_permissions_from_db()
        self._load_today_usage_from_db()

    # ── Public API: 访问检查 ──────────────────────────────────────

    def check_access(
        self,
        model: str,
        agent: str = "",
        session_id: str = "",
    ) -> ModelAccessDecision:
        """检查是否允许访问指定模型。

        决策流程：
          1. 在 permissions 中找到最优先匹配 model 的规则
             （按 priority 升序，第一个匹配的）
          2. 如果没匹配到规则，按 default_policy 决定
          3. 如果允许，检查 daily_token_limit 是否已超
          4. 如果允许，检查 global_daily_token_limit 是否已超
          5. 返回 ModelAccessDecision
        """
        with self._lock:
            permission = self._find_matching_permission(model)

            # 步骤 1+2：决定是否允许
            if permission is not None:
                allowed = permission.allowed
                reason = (
                    f"matched rule: {permission.model_pattern} "
                    f"(allowed={permission.allowed})"
                )
                max_tokens_per_call = permission.max_tokens_per_call
                daily_limit = permission.daily_token_limit
            else:
                policy = self._config.default_policy.lower()
                allowed = policy == "allow"
                reason = f"no matching rule; default_policy={policy}"
                max_tokens_per_call = 0
                daily_limit = 0

            if not allowed:
                return ModelAccessDecision(
                    allowed=False,
                    model=model,
                    reason=reason,
                    matched_permission=permission,
                    max_tokens_per_call=max_tokens_per_call,
                    remaining_daily_tokens=-1,
                )

            # 步骤 3+4：额度检查
            remaining = -1
            if self._config.enable_quota_check:
                used = self._get_daily_used(model)

                # 检查规则级日限额
                if daily_limit > 0:
                    if used >= daily_limit:
                        return ModelAccessDecision(
                            allowed=False,
                            model=model,
                            reason=(
                                f"daily_token_limit exceeded: "
                                f"{used}/{daily_limit}"
                            ),
                            matched_permission=permission,
                            max_tokens_per_call=max_tokens_per_call,
                            remaining_daily_tokens=0,
                        )
                    remaining = daily_limit - used

                # 检查全局日限额
                global_limit = self._config.global_daily_token_limit
                if global_limit > 0:
                    total_used = self._get_daily_used_all()
                    if total_used >= global_limit:
                        return ModelAccessDecision(
                            allowed=False,
                            model=model,
                            reason=(
                                f"global_daily_token_limit exceeded: "
                                f"{total_used}/{global_limit}"
                            ),
                            matched_permission=permission,
                            max_tokens_per_call=max_tokens_per_call,
                            remaining_daily_tokens=0,
                        )
                    global_remaining = global_limit - total_used
                    if remaining == -1 or global_remaining < remaining:
                        remaining = global_remaining

            return ModelAccessDecision(
                allowed=True,
                model=model,
                reason=reason,
                matched_permission=permission,
                max_tokens_per_call=max_tokens_per_call,
                remaining_daily_tokens=remaining,
            )

    # ── Public API: 使用量记录 ────────────────────────────────────

    def record_usage(
        self,
        model: str,
        tokens: int,
        agent: str = "",
        session_id: str = "",
    ) -> None:
        """记录模型使用量（Token 数）。

        同时更新内存缓存和 SQLite 持久化。

        跨天运行时清理 ``_daily_usage`` 中非今日的旧数据，防止内存泄漏。
        """
        if tokens <= 0:
            return
        with self._lock:
            today = self._today_str()
            # 清理非今日的旧数据，防止跨天运行时 _daily_usage 内存泄漏
            old_keys = [k for k in self._daily_usage if not k.startswith(f"{today}:")]
            for k in old_keys:
                del self._daily_usage[k]
            key = f"{today}:{model}"
            self._daily_usage[key] = self._daily_usage.get(key, 0) + tokens
            self._persist_usage(model, tokens, agent)

    def get_daily_usage(self, model: str = "") -> dict[str, int]:
        """获取今日使用量。

        Parameters
        ----------
        model : str
            指定模型时返回 {model: tokens}；为空时返回所有模型的使用量。
        """
        with self._lock:
            today = self._today_str()
            if model:
                key = f"{today}:{model}"
                return {model: self._daily_usage.get(key, 0)}
            # 聚合所有今日模型
            result: dict[str, int] = {}
            prefix = f"{today}:"
            for k, v in self._daily_usage.items():
                if k.startswith(prefix):
                    m = k[len(prefix):]
                    result[m] = v
            return result

    # ── Public API: 权限规则管理 ──────────────────────────────────

    def add_permission(self, permission: ModelPermission) -> None:
        """添加权限规则。

        若 ``model_pattern`` 已存在则覆盖。同时持久化到 SQLite。
        """
        with self._lock:
            # 移除同 pattern 的旧规则
            self._config.permissions = [
                p
                for p in self._config.permissions
                if p.model_pattern != permission.model_pattern
            ]
            self._config.permissions.append(permission)
            self._persist_permission(permission)
            logger.debug(
                "Added permission: pattern=%s allowed=%s priority=%d",
                permission.model_pattern,
                permission.allowed,
                permission.priority,
            )

    def remove_permission(self, model_pattern: str) -> bool:
        """移除权限规则。

        Returns
        -------
        bool
            True 如果找到并移除了规则；False 如果规则不存在。
        """
        with self._lock:
            original_len = len(self._config.permissions)
            self._config.permissions = [
                p
                for p in self._config.permissions
                if p.model_pattern != model_pattern
            ]
            if len(self._config.permissions) == original_len:
                return False
            self._delete_permission_from_db(model_pattern)
            return True

    def list_permissions(self) -> list[ModelPermission]:
        """列出所有权限规则（按 priority 升序）。"""
        with self._lock:
            return sorted(
                self._config.permissions,
                key=lambda p: p.priority,
            )

    def update_config(self, config: ModelGatewayConfig) -> None:
        """更新网关配置。

        替换当前配置，并将权限规则同步到 SQLite。
        """
        with self._lock:
            self._config = config
            self._sync_permissions_to_db()

    # ── Internal: 模式匹配 ────────────────────────────────────────

    def _match_pattern(self, pattern: str, model: str) -> bool:
        """通配符匹配（fnmatch）。

        使用 ``fnmatch.fnmatchcase`` 进行大小写敏感的 shell 风格通配符匹配，
        支持 ``*``、``?``、``[seq]``、``[!seq]``。
        ``fnmatchcase`` 保证跨平台行为一致（``fnmatch.fnmatch`` 在 Windows 上
        会做 ``os.path.normcase`` 大小写规范化，导致权限规则意外匹配）。
        """
        return fnmatch.fnmatchcase(model, pattern)

    def _find_matching_permission(self, model: str) -> ModelPermission | None:
        """找到最优先匹配的权限规则。

        按 priority 升序遍历，返回第一个 ``model_pattern`` 匹配 model 的规则。
        若无匹配则返回 None。
        """
        sorted_perms = sorted(
            self._config.permissions,
            key=lambda p: p.priority,
        )
        for perm in sorted_perms:
            if self._match_pattern(perm.model_pattern, model):
                return perm
        return None

    # ── Internal: SQLite 初始化与加载 ─────────────────────────────

    def _init_db(self) -> None:
        """初始化 SQLite 表。"""
        with sqlite_connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS model_gateway_usage (
                    date TEXT NOT NULL,
                    model TEXT NOT NULL,
                    agent TEXT DEFAULT '',
                    tokens INTEGER DEFAULT 0,
                    PRIMARY KEY (date, model, agent)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS model_gateway_permissions (
                    model_pattern TEXT PRIMARY KEY,
                    allowed INTEGER DEFAULT 1,
                    max_tokens_per_call INTEGER DEFAULT 0,
                    daily_token_limit INTEGER DEFAULT 0,
                    priority INTEGER DEFAULT 100
                )
            """)

    def _load_permissions_from_db(self) -> None:
        """从 SQLite 加载权限规则到内存。

        仅当内存中的 permissions 为空时加载（避免覆盖构造时传入的配置）。
        """
        if self._config.permissions:
            # 构造时已传入配置，同步到 DB
            self._sync_permissions_to_db()
            return
        with sqlite_connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT model_pattern, allowed, max_tokens_per_call, "
                "daily_token_limit, priority "
                "FROM model_gateway_permissions"
            ).fetchall()
            for row in rows:
                self._config.permissions.append(
                    ModelPermission(
                        model_pattern=row[0],
                        allowed=bool(row[1]),
                        max_tokens_per_call=row[2],
                        daily_token_limit=row[3],
                        priority=row[4],
                    )
                )

    def _load_today_usage_from_db(self) -> None:
        """从 SQLite 加载今日使用量到内存。"""
        today = self._today_str()
        with sqlite_connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT model, SUM(tokens) as total "
                "FROM model_gateway_usage WHERE date = ? "
                "GROUP BY model",
                (today,),
            ).fetchall()
            for row in rows:
                model = row[0]
                total = row[1] or 0
                key = f"{today}:{model}"
                self._daily_usage[key] = total

    # ── Internal: 持久化 ──────────────────────────────────────────

    def _persist_usage(self, model: str, tokens: int, agent: str) -> None:
        """将使用量增量持久化到 SQLite（upsert 累加）。"""
        today = self._today_str()
        with sqlite_connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO model_gateway_usage (date, model, agent, tokens) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(date, model, agent) DO UPDATE SET "
                "tokens = tokens + excluded.tokens",
                (today, model, agent, tokens),
            )

    def _persist_permission(self, permission: ModelPermission) -> None:
        """将单条权限规则持久化到 SQLite（upsert）。"""
        with sqlite_connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO model_gateway_permissions "
                "(model_pattern, allowed, max_tokens_per_call, "
                "daily_token_limit, priority) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(model_pattern) DO UPDATE SET "
                "allowed = excluded.allowed, "
                "max_tokens_per_call = excluded.max_tokens_per_call, "
                "daily_token_limit = excluded.daily_token_limit, "
                "priority = excluded.priority",
                (
                    permission.model_pattern,
                    int(permission.allowed),
                    permission.max_tokens_per_call,
                    permission.daily_token_limit,
                    permission.priority,
                ),
            )

    def _delete_permission_from_db(self, model_pattern: str) -> None:
        """从 SQLite 删除权限规则。"""
        with sqlite_connect(self._db_path) as conn:
            conn.execute(
                "DELETE FROM model_gateway_permissions WHERE model_pattern = ?",
                (model_pattern,),
            )

    def _sync_permissions_to_db(self) -> None:
        """将内存中所有权限规则同步到 SQLite（全量替换）。"""
        with sqlite_connect(self._db_path) as conn:
            conn.execute("DELETE FROM model_gateway_permissions")
            for perm in self._config.permissions:
                conn.execute(
                    "INSERT INTO model_gateway_permissions "
                    "(model_pattern, allowed, max_tokens_per_call, "
                    "daily_token_limit, priority) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        perm.model_pattern,
                        int(perm.allowed),
                        perm.max_tokens_per_call,
                        perm.daily_token_limit,
                        perm.priority,
                    ),
                )

    # ── Internal: 日期/Key 辅助 ───────────────────────────────────

    def _today_str(self) -> str:
        """获取今日日期字符串（UTC, YYYY-MM-DD）。"""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _get_today_key(self, model: str) -> str:
        """获取今日的 usage key: "date:model"。"""
        return f"{self._today_str()}:{model}"

    def _get_daily_used(self, model: str) -> int:
        """获取今日指定模型的已用 Token 数。"""
        key = self._get_today_key(model)
        return self._daily_usage.get(key, 0)

    def _get_daily_used_all(self) -> int:
        """获取今日所有模型的已用 Token 总数。"""
        today = self._today_str()
        prefix = f"{today}:"
        return sum(
            v for k, v in self._daily_usage.items() if k.startswith(prefix)
        )