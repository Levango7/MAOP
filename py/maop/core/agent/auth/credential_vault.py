"""Credential Vault — 统一管理所有 Agent 的授权凭证。

支持五种凭证类型（API Key / OAuth / 账号密码 / License / Cookie，外加
Bearer Token），敏感字段经 ``cryptography.fernet.Fernet``（AES-128-CBC +
HMAC）对称加密后持久化到 SQLite。当 ``cryptography`` 不可用时降级为
base64+hash 混淆并记录 degradation 警告。

安全措施：
    * 敏感字段绝不以明文落盘——加密后整体存入 ``encrypted_data`` 列。
    * 加密密钥从环境变量 ``MAOP_CREDENTIAL_KEY`` 读取；未设置则自动生成
      并写入 ``<data_dir>/credential.key``（0600 权限）。
    * ``list_all()`` 只返回类型与掩码预览（如 ``sk-****xxxx``），不泄露敏感值。
    * 每次读取/写入/删除/轮换均记录审计日志（who / when / what / result）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import ConnectionPool, get_pool

logger = logging.getLogger(__name__)

# ── 常量 ────────────────────────────────────────────────────────────

_CREDENTIAL_KEY_FILE = "credential.key"
_CREDENTIAL_KEY_ENV = "MAOP_CREDENTIAL_KEY"
_VAULT_TABLE = "credential_vault"
_AUDIT_TABLE = "credential_vault_audit"

# 视为敏感需加密的字段名（与 Credential 模型字段对应）。
_SENSITIVE_FIELDS = (
    "api_key",
    "oauth_access_token",
    "oauth_refresh_token",
    "password",
    "license_key",
    "cookie_string",
    "bearer_token",
)


# ── Pydantic 模型 ──────────────────────────────────────────────────


class CredentialType(str, Enum):
    """凭证类型枚举。"""

    API_KEY = "api_key"
    OAUTH_TOKEN = "oauth_token"
    USERNAME_PASSWORD = "username_password"
    LICENSE_KEY = "license_key"
    COOKIE = "cookie"
    BEARER_TOKEN = "bearer_token"


class Credential(BaseModel):
    """完整凭证（含敏感字段，仅在 retrieve / store 内部流转）。"""

    id: str = ""
    agent_name: str = Field(..., min_length=1, max_length=200)
    credential_type: CredentialType
    api_key: str = ""
    oauth_access_token: str = ""
    oauth_refresh_token: str = ""
    oauth_expires_at: float = 0.0
    username: str = ""
    password: str = ""
    license_key: str = ""
    cookie_string: str = ""
    bearer_token: str = ""
    extra: dict[str, str] = Field(default_factory=dict)


class CredentialSummary(BaseModel):
    """凭证摘要（list_all 返回，不含敏感字段明文）。"""

    id: str
    agent_name: str
    credential_type: CredentialType
    preview: str = ""          # 掩码预览，如 sk-****abcd
    expires_at: float = 0.0
    last_used_at: float = 0.0
    last_tested_at: float = 0.0
    last_test_result: int = 0
    created_by: str = ""
    created_at: str = ""
    updated_at: str = ""


# ── 加密引擎 ────────────────────────────────────────────────────────


class _CryptoEngine:
    """对称加密引擎，优先 Fernet，降级为 base64+hash 混淆。

    降级模式仅用于 ``cryptography`` 未安装的环境，会记录 degradation 警告。
    降级模式不具备真正的机密性——仅做编码混淆，生产环境必须安装 cryptography。
    """

    def __init__(self, key_material: bytes) -> None:
        self.degraded: bool = False
        try:
            from cryptography.fernet import Fernet  # type: ignore[import-untyped]
        except ImportError:
            self.degraded = True
            logger.warning(
                "cryptography 不可用，CredentialVault 降级为 base64 混淆模式——"
                "不具备真正机密性，生产环境请安装 cryptography。"
            )
            self._fernet = None
            self._混淆盐 = hashlib.sha256(key_material).digest()
            return
        # Fernet 需要 32 字节 url-safe base64 密钥。
        fernet_key = base64.urlsafe_b64encode(hashlib.sha256(key_material).digest())
        self._fernet: Any = Fernet(fernet_key)

    def encrypt(self, plaintext: str) -> str:
        if self.degraded:
            # 降级：base64 + 盐混淆（非安全加密）。
            raw = plaintext.encode()
            salted = bytes(a ^ self._混淆盐[i % len(self._混淆盐)] for i, a in enumerate(raw))
            return "DEG::" + base64.b64encode(salted).decode()
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        if self.degraded:
            if token.startswith("DEG::"):
                salted = base64.b64decode(token[5:])
                raw = bytes(a ^ self._混淆盐[i % len(self._混淆盐)] for i, a in enumerate(salted))
                return raw.decode()
            raise ValueError("降级模式下密文格式不正确")
        return self._fernet.decrypt(token.encode()).decode()


# ── 凭证保险库 ──────────────────────────────────────────────────────


class CredentialVault:
    """凭证保险库 — AES 加密存储，审计日志，支持轮换与测试。

    线程安全：依赖底层 :class:`ConnectionPool`（WAL + busy_timeout），
    辅入/输出层无共享可变状态。
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            from maop.core.backends.db_utils import get_db_path
            db_path = get_db_path("auth")
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._pool: ConnectionPool = get_pool(self.db_path)
        self._lock = threading.RLock()
        self._crypto = _CryptoEngine(self._load_or_create_key())
        self._init_db()

    # ── 密钥管理 ───────────────────────────────────────────────────

    @staticmethod
    def _resolve_data_dir() -> Path:
        data_dir = os.getenv("MAOP_DATA_DIR", "").strip()
        if data_dir:
            return Path(data_dir)
        # 回退到项目根 data 目录。
        from maop.core.backends.db_utils import find_project_root
        return find_project_root() / "data"

    def _load_or_create_key(self) -> bytes:
        """从环境变量或密钥文件加载加密密钥；不存在则生成并落盘。"""
        env_key = os.environ.get(_CREDENTIAL_KEY_ENV, "").strip()
        if env_key:
            return env_key.encode()
        key_file = self._resolve_data_dir() / _CREDENTIAL_KEY_FILE
        key_file.parent.mkdir(parents=True, exist_ok=True)
        if key_file.exists():
            return key_file.read_bytes()
        # 生成 32 字节随机密钥。
        key_material = secrets.token_bytes(32)
        key_file.write_bytes(key_material)
        try:
            key_file.chmod(0o600)
        except OSError:
            # Windows 上 chmod 限制，忽略。
            pass
        logger.info("生成新凭证加密密钥: %s", key_file)
        return key_material

    # ── 建表 ───────────────────────────────────────────────────────

    def _init_db(self) -> None:
        conn = self._pool.acquire()
        try:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_VAULT_TABLE} (
                    id              TEXT PRIMARY KEY,
                    agent_name      TEXT NOT NULL,
                    credential_type TEXT NOT NULL,
                    encrypted_data  TEXT NOT NULL,
                    expires_at      REAL DEFAULT 0.0,
                    last_used_at    REAL DEFAULT 0.0,
                    last_tested_at  REAL DEFAULT 0.0,
                    last_test_result INTEGER DEFAULT 0,
                    created_by      TEXT DEFAULT '',
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT
                )
                """
            )
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_AUDIT_TABLE} (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    credential_id TEXT NOT NULL,
                    actor       TEXT NOT NULL DEFAULT '',
                    action      TEXT NOT NULL,
                    detail      TEXT DEFAULT '',
                    result      TEXT DEFAULT '',
                    timestamp   REAL NOT NULL
                )
                """
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{_AUDIT_TABLE}_cid_ts "
                f"ON {_AUDIT_TABLE}(credential_id, timestamp)"
            )
            conn.commit()
        finally:
            self._pool.release(conn)

    # ── 加密 / 解密 ────────────────────────────────────────────────

    def _encrypt_credential(self, cred: Credential) -> str:
        """将敏感字段打包为 JSON 后加密。"""
        payload = {f: getattr(cred, f) for f in _SENSITIVE_FIELDS}
        payload["extra"] = cred.extra
        payload["oauth_expires_at"] = cred.oauth_expires_at
        payload["username"] = cred.username
        return self._crypto.encrypt(json.dumps(payload, ensure_ascii=False))

    def _decrypt_credential(self, encrypted: str, row: Any) -> Credential:
        """解密并重建 Credential（非敏感字段从 DB 行取）。"""
        payload = json.loads(self._crypto.decrypt(encrypted))
        return Credential(
            id=row["id"],
            agent_name=row["agent_name"],
            credential_type=CredentialType(row["credential_type"]),
            api_key=payload.get("api_key", ""),
            oauth_access_token=payload.get("oauth_access_token", ""),
            oauth_refresh_token=payload.get("oauth_refresh_token", ""),
            oauth_expires_at=payload.get("oauth_expires_at", 0.0),
            username=payload.get("username", ""),
            password=payload.get("password", ""),
            license_key=payload.get("license_key", ""),
            cookie_string=payload.get("cookie_string", ""),
            bearer_token=payload.get("bearer_token", ""),
            extra=payload.get("extra", {}),
        )

    # ── 掩码预览 ───────────────────────────────────────────────────

    @staticmethod
    def _mask(value: str) -> str:
        """生成掩码预览，如 ``sk-abcd1234****wxyz`` → ``sk-ab****yz``。"""
        if not value:
            return ""
        if len(value) <= 8:
            return "*" * len(value)
        return value[:4] + "****" + value[-2:]

    def _primary_secret(self, cred: Credential) -> str:
        """根据凭证类型取主秘密字段用于预览。"""
        ct = cred.credential_type
        if ct == CredentialType.API_KEY:
            return cred.api_key
        if ct == CredentialType.OAUTH_TOKEN:
            return cred.oauth_access_token
        if ct == CredentialType.USERNAME_PASSWORD:
            return cred.password
        if ct == CredentialType.LICENSE_KEY:
            return cred.license_key
        if ct == CredentialType.COOKIE:
            return cred.cookie_string
        if ct == CredentialType.BEARER_TOKEN:
            return cred.bearer_token
        return ""

    # ── 审计日志 ───────────────────────────────────────────────────

    def _audit(
        self, credential_id: str, action: str, actor: str = "",
        detail: str = "", result: str = "",
    ) -> None:
        conn = self._pool.acquire()
        try:
            conn.execute(
                f"INSERT INTO {_AUDIT_TABLE} "
                f"(credential_id, actor, action, detail, result, timestamp) "
                f"VALUES (?, ?, ?, ?, ?, ?)",
                (credential_id, actor, action, detail, result, time.time()),
            )
            conn.commit()
        finally:
            self._pool.release(conn)

    # ── CRUD ───────────────────────────────────────────────────────

    def store(self, credential: Credential, *, created_by: str = "") -> str:
        """存储凭证（加密后落盘）。返回凭证 ID。"""
        with self._lock:
            cred_id = credential.id or secrets.token_hex(16)
            now = time.time()
            encrypted = self._encrypt_credential(credential)
            expires_at = credential.oauth_expires_at if (
                credential.credential_type == CredentialType.OAUTH_TOKEN
                and credential.oauth_expires_at
            ) else 0.0
            conn = self._pool.acquire()
            try:
                conn.execute(
                    f"INSERT OR REPLACE INTO {_VAULT_TABLE} "
                    f"(id, agent_name, credential_type, encrypted_data, expires_at, "
                    f"last_used_at, last_tested_at, last_test_result, created_by, "
                    f"created_at, updated_at) "
                    f"VALUES (?, ?, ?, ?, ?, 0, 0, 0, ?, ?, ?)",
                    (
                        cred_id, credential.agent_name,
                        credential.credential_type.value, encrypted, expires_at,
                        created_by, str(now), str(now),
                    ),
                )
                conn.commit()
            finally:
                self._pool.release(conn)
            self._audit(cred_id, "store", actor=created_by, result="ok")
            logger.info("[credential_vault] 存储凭证 id=%s agent=%s type=%s",
                        cred_id, credential.agent_name, credential.credential_type.value)
            return cred_id

    def retrieve(self, credential_id: str, *, actor: str = "") -> Credential:
        """读取并解密凭证。记录审计日志与 last_used_at。"""
        with self._lock:
            conn = self._pool.acquire()
            try:
                row = conn.execute(
                    f"SELECT * FROM {_VAULT_TABLE} WHERE id = ?", (credential_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(f"凭证不存在: {credential_id}")
                conn.execute(
                    f"UPDATE {_VAULT_TABLE} SET last_used_at = ? WHERE id = ?",
                    (time.time(), credential_id),
                )
                conn.commit()
            finally:
                self._pool.release(conn)
            cred = self._decrypt_credential(row["encrypted_data"], row)
            self._audit(credential_id, "retrieve", actor=actor, result="ok")
            return cred

    def list_all(self) -> list[CredentialSummary]:
        """列出所有凭证摘要（不含敏感字段明文）。"""
        with self._lock:
            conn = self._pool.acquire()
            try:
                rows = conn.execute(
                    f"SELECT * FROM {_VAULT_TABLE} ORDER BY created_at DESC"
                ).fetchall()
            finally:
                self._pool.release(conn)
            summaries: list[CredentialSummary] = []
            for row in rows:
                try:
                    cred = self._decrypt_credential(row["encrypted_data"], row)
                    preview = self._mask(self._primary_secret(cred))
                except Exception:
                    preview = "[decrypt-error]"
                summaries.append(CredentialSummary(
                    id=row["id"],
                    agent_name=row["agent_name"],
                    credential_type=CredentialType(row["credential_type"]),
                    preview=preview,
                    expires_at=row["expires_at"],
                    last_used_at=row["last_used_at"],
                    last_tested_at=row["last_tested_at"],
                    last_test_result=row["last_test_result"],
                    created_by=row["created_by"] or "",
                    created_at=row["created_at"] or "",
                    updated_at=row["updated_at"] or "",
                ))
            return summaries

    def delete(self, credential_id: str, *, actor: str = "") -> bool:
        """删除凭证。返回是否有行被删除。"""
        with self._lock:
            conn = self._pool.acquire()
            try:
                cur = conn.execute(
                    f"DELETE FROM {_VAULT_TABLE} WHERE id = ?", (credential_id,)
                )
                conn.commit()
                deleted = cur.rowcount > 0
            finally:
                self._pool.release(conn)
            self._audit(credential_id, "delete", actor=actor,
                         result="ok" if deleted else "not_found")
            return deleted

    def rotate(self, credential_id: str, *, actor: str = "") -> str:
        """轮换凭证：生成新随机秘密替换原值，返回新凭证 ID（同 ID）。

        对 API_KEY / BEARER_TOKEN / LICENSE_KEY 生成新随机串；
        对 OAUTH_TOKEN 清空 access/refresh token（需外部重新授权）；
        对 USERNAME_PASSWORD / COOKIE 生成新随机密码占位（需外部同步更新）。
        """
        with self._lock:
            cred = self.retrieve(credential_id, actor=actor)
            new_secret = secrets.token_urlsafe(32)
            ct = cred.credential_type
            if ct == CredentialType.API_KEY:
                cred.api_key = new_secret
            elif ct == CredentialType.BEARER_TOKEN:
                cred.bearer_token = new_secret
            elif ct == CredentialType.LICENSE_KEY:
                cred.license_key = new_secret
            elif ct == CredentialType.OAUTH_TOKEN:
                cred.oauth_access_token = ""
                cred.oauth_refresh_token = ""
                cred.oauth_expires_at = 0.0
            elif ct == CredentialType.USERNAME_PASSWORD:
                cred.password = new_secret
            elif ct == CredentialType.COOKIE:
                cred.cookie_string = new_secret
            # 重新加密落盘（保留原 created_at / created_by）。
            encrypted = self._encrypt_credential(cred)
            now = time.time()
            conn = self._pool.acquire()
            try:
                conn.execute(
                    f"UPDATE {_VAULT_TABLE} SET encrypted_data = ?, updated_at = ? "
                    f"WHERE id = ?",
                    (encrypted, str(now), credential_id),
                )
                conn.commit()
            finally:
                self._pool.release(conn)
            self._audit(credential_id, "rotate", actor=actor, result="ok")
            logger.info("[credential_vault] 轮换凭证 id=%s type=%s", credential_id, ct.value)
            return credential_id

    def test(self, credential_id: str, *, actor: str = "") -> bool:
        """测试凭证可用性（解密成功即视为结构有效）。记录测试结果。"""
        try:
            cred = self.retrieve(credential_id, actor=actor)
            ok = bool(self._primary_secret(cred))
        except Exception as exc:
            ok = False
            logger.warning("[credential_vault] 测试凭证失败 id=%s: %s", credential_id, exc)
        now = time.time()
        conn = self._pool.acquire()
        try:
            conn.execute(
                f"UPDATE {_VAULT_TABLE} SET last_tested_at = ?, last_test_result = ? "
                f"WHERE id = ?",
                (now, 1 if ok else 0, credential_id),
            )
            conn.commit()
        finally:
            self._pool.release(conn)
        self._audit(credential_id, "test", actor=actor,
                     result="pass" if ok else "fail")
        return ok

    def get_audit_log(self, credential_id: str = "", *, limit: int = 100) -> list[dict[str, Any]]:
        """查询审计日志（按时间倒序）。credential_id 为空则查全部。"""
        conn = self._pool.acquire()
        try:
            if credential_id:
                rows = conn.execute(
                    f"SELECT * FROM {_AUDIT_TABLE} WHERE credential_id = ? "
                    f"ORDER BY timestamp DESC LIMIT ?",
                    (credential_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT * FROM {_AUDIT_TABLE} ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        finally:
            self._pool.release(conn)
        return [dict(r) for r in rows]

    def close(self) -> None:
        """关闭底层连接池。"""
        self._pool.close_all()