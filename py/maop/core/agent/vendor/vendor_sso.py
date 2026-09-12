"""VendorSSO — 厂商统一认证.

一个厂商凭证认证后，同厂商下所有 Agent 共享会话，无需逐 Agent 登录。
会话持久化到 SQLite，支持过期检查、主动登出与凭证派发。

设计要点：
    * 会话持久化到 SQLite 表 ``vendor_sso_sessions``。
    * 会话默认有效期 8 小时，可通过 ``login_vendor`` 的 credential 中的
      ``ttl_seconds`` 自定义。
    * ``get_agent_credential`` 从厂商 SSO 会话派发 Agent 级凭证——同厂商
      Agent 共享 token，但可按 Agent 维度附加差异化字段。
    * 全程使用 ``threading.RLock`` 保护内存状态。
    * ``credential_vault`` 为可选依赖：传入时将凭证加密托管，未传入时
      仅在会话内存中临时持有。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.agent.vendor.vendor_ecosystem import VendorEcosystem
from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)

# 默认会话有效期：8 小时
_DEFAULT_SESSION_TTL_SECONDS = 8 * 3600


# ── Pydantic 模型 ────────────────────────────────────────────────
class VendorSession(BaseModel):
    """厂商 SSO 会话.

    Attributes
    ----------
    session_id : str
        会话唯一标识（UUID）。
    vendor_name : str
        所属厂商。
    created_at : str
        创建时间（ISO 8601 UTC）。
    expires_at : str
        过期时间（ISO 8601 UTC）。
    status : str
        会话状态：``active`` / ``expired`` / ``revoked``。
    user_id : str
        用户标识（从凭证中提取）。
    token : str
        认证 token（从凭证中提取）。
    """

    session_id: str = Field(..., description="会话 UUID")
    vendor_name: str = Field(..., description="厂商名称")
    created_at: str = Field(..., description="创建时间 ISO 时间戳")
    expires_at: str = Field(..., description="过期时间 ISO 时间戳")
    status: str = Field(default="active", description="active/expired/revoked")
    user_id: str = Field(default="", description="用户标识")
    token: str = Field(default="", description="认证 token")


# ── VendorSSO ───────────────────────────────────────────────────
class VendorSSO:
    """厂商统一认证管理器.

    一个凭证认证同厂商所有 Agent，会话持久化到 SQLite。

    Parameters
    ----------
    ecosystem : VendorEcosystem
        厂商生态管理器，用于校验厂商存在性与查询 Agent 归属。
    credential_vault : Any, optional
        凭证保险库（``CredentialVault`` 实例）。传入时凭证加密托管；
        未传入时仅在会话内存中临时持有。
    db_path : Path | None
        SQLite 持久化路径。``None`` 时由 ``get_db_path("vendor_sso")`` 解析。
    """

    def __init__(
        self,
        ecosystem: VendorEcosystem,
        credential_vault: Any = None,
        db_path: Path | None = None,
    ) -> None:
        self._ecosystem = ecosystem
        self._vault = credential_vault
        self._db_path: Path = Path(db_path) if db_path else get_db_path("vendor_sso")
        self._lock = threading.RLock()
        # 内存缓存：session_id → 原始凭证 dict（用于派发 Agent 凭证）
        self._credentials: dict[str, dict] = {}
        self._init_db()

    # ── 初始化 ──────────────────────────────────────────────────
    def _init_db(self) -> None:
        """初始化会话表（幂等）。"""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS vendor_sso_sessions (
                    session_id TEXT PRIMARY KEY,
                    vendor_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    status TEXT DEFAULT 'active',
                    user_id TEXT DEFAULT '',
                    token TEXT DEFAULT ''
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sso_vendor "
                "ON vendor_sso_sessions(vendor_name)"
            )

    # ── 登录 / 登出 ─────────────────────────────────────────────
    def login_vendor(
        self,
        vendor_name: str,
        credential: dict,
    ) -> VendorSession:
        """厂商统一登录.

        用一个凭证认证厂商，成功后同厂商所有 Agent 共享此会话。

        Parameters
        ----------
        vendor_name : str
            厂商名称（必须已在 ecosystem 中注册）。
        credential : dict
            凭证字典，常见字段：``user_id`` / ``token`` / ``api_key`` /
            ``ttl_seconds``（会话有效期，秒）。

        Returns
        -------
        VendorSession
            创建的会话对象。

        Raises
        ------
        ValueError
            厂商不存在时抛出。
        """
        with self._lock:
            # 校验厂商存在
            vendor = self._ecosystem.get_vendor(vendor_name)
            if vendor is None:
                raise ValueError(f"厂商 {vendor_name!r} 未注册")

            # 解析会话有效期
            ttl_seconds = int(
                credential.get("ttl_seconds", _DEFAULT_SESSION_TTL_SECONDS)
            )
            now = datetime.now(timezone.utc)
            expires = now + timedelta(seconds=ttl_seconds)

            session = VendorSession(
                session_id=uuid.uuid4().hex,
                vendor_name=vendor_name,
                created_at=now.isoformat(),
                expires_at=expires.isoformat(),
                status="active",
                user_id=str(credential.get("user_id", "")),
                token=str(credential.get("token", credential.get("api_key", ""))),
            )

            # 持久化会话
            with sqlite_connect(self._db_path) as conn:
                conn.execute(
                    """INSERT INTO vendor_sso_sessions
                       (session_id, vendor_name, created_at, expires_at,
                        status, user_id, token)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session.session_id,
                        session.vendor_name,
                        session.created_at,
                        session.expires_at,
                        session.status,
                        session.user_id,
                        session.token,
                    ),
                )

            # 内存缓存原始凭证（用于派发 Agent 凭证）
            self._credentials[session.session_id] = dict(credential)

            # 可选：托管到 CredentialVault
            if self._vault is not None:
                try:
                    self._vault.store_from_dict(vendor_name, credential)
                except Exception as exc:
                    # Vault 托管失败不影响登录主流程
                    logger.warning(
                        "[vendor_sso] CredentialVault 托管失败（不影响登录）: %s", exc
                    )

        logger.debug(
            "[vendor_sso] 厂商 %s 登录成功，会话 %s", vendor_name, session.session_id
        )
        return session

    def logout_vendor(self, vendor_name: str, session_id: str) -> None:
        """厂商统一登出.

        撤销指定会话。会话状态置为 ``revoked``。

        Parameters
        ----------
        vendor_name : str
            厂商名称。
        session_id : str
            会话 ID。
        """
        with self._lock:
            with sqlite_connect(self._db_path) as conn:
                conn.execute(
                    """UPDATE vendor_sso_sessions
                       SET status = 'revoked'
                       WHERE session_id = ? AND vendor_name = ?""",
                    (session_id, vendor_name),
                )
            # 清理内存缓存
            self._credentials.pop(session_id, None)
        logger.debug(
            "[vendor_sso] 厂商 %s 会话 %s 已登出", vendor_name, session_id
        )

    # ── 会话查询 ────────────────────────────────────────────────
    def get_session(self, session_id: str) -> VendorSession | None:
        """获取会话信息.

        Returns
        -------
        VendorSession | None
            会话不存在时返回 ``None``。
        """
        with sqlite_connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM vendor_sso_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_session(row)

    def check_session_valid(self, session_id: str) -> bool:
        """检查会话是否有效.

        有效条件：会话存在、状态为 ``active``、未过期。

        Returns
        -------
        bool
            有效返回 ``True``，否则 ``False``。
        """
        session = self.get_session(session_id)
        if session is None:
            return False
        if session.status != "active":
            return False
        # 检查过期
        now = datetime.now(timezone.utc)
        try:
            expires = datetime.fromisoformat(session.expires_at)
        except ValueError:
            return False
        if now >= expires:
            # 惰性标记过期
            self._mark_expired(session_id)
            return False
        return True

    def get_agent_credential(
        self,
        agent_name: str,
        session_id: str,
    ) -> dict | None:
        """从厂商 SSO 会话获取 Agent 凭证.

        同厂商 Agent 共享会话 token，但可按 Agent 维度附加差异化字段
        （如 ``agent_name``）。

        Parameters
        ----------
        agent_name : str
            Agent 名称。
        session_id : str
            厂商 SSO 会话 ID。

        Returns
        -------
        dict | None
            Agent 凭证字典；会话无效或 Agent 不属于该厂商时返回 ``None``。
        """
        with self._lock:
            session = self.get_session(session_id)
            if session is None:
                return None
            if not self.check_session_valid(session_id):
                return None

            # 校验 Agent 属于该厂商
            vendor_of_agent = self._ecosystem.get_vendor_of_agent(agent_name)
            if vendor_of_agent != session.vendor_name:
                logger.warning(
                    "[vendor_sso] Agent %s 不属于会话厂商 %s",
                    agent_name,
                    session.vendor_name,
                )
                return None

            # 派发凭证：共享 token + Agent 维度字段
            base_credential = self._credentials.get(session_id, {})
            agent_credential = {
                "user_id": session.user_id,
                "token": session.token,
                "vendor_name": session.vendor_name,
                "agent_name": agent_name,
                "session_id": session_id,
            }
            # 合并原始凭证中的额外字段（不覆盖已派发字段）
            for key, value in base_credential.items():
                if key not in agent_credential:
                    agent_credential[key] = value
            return agent_credential

    def list_active_sessions(
        self,
        vendor_name: str = "",
    ) -> list[VendorSession]:
        """列出活跃会话.

        Parameters
        ----------
        vendor_name : str
            筛选指定厂商。空字符串表示不筛选（返回所有活跃会话）。

        Returns
        -------
        list[VendorSession]
            活跃且未过期的会话列表。
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        with sqlite_connect(self._db_path) as conn:
            if vendor_name:
                rows = conn.execute(
                    """SELECT * FROM vendor_sso_sessions
                       WHERE status = 'active' AND expires_at > ?
                         AND vendor_name = ?
                       ORDER BY created_at DESC""",
                    (now_iso, vendor_name),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM vendor_sso_sessions
                       WHERE status = 'active' AND expires_at > ?
                       ORDER BY created_at DESC""",
                    (now_iso,),
                ).fetchall()
        return [self._row_to_session(r) for r in rows]

    # ── 内部工具 ────────────────────────────────────────────────
    def _mark_expired(self, session_id: str) -> None:
        """惰性标记会话过期."""
        with sqlite_connect(self._db_path) as conn:
            conn.execute(
                "UPDATE vendor_sso_sessions SET status = 'expired' "
                "WHERE session_id = ? AND status = 'active'",
                (session_id,),
            )

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> VendorSession:
        """sqlite3.Row → VendorSession."""
        return VendorSession(
            session_id=row["session_id"],
            vendor_name=row["vendor_name"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            status=row["status"],
            user_id=row["user_id"],
            token=row["token"],
        )