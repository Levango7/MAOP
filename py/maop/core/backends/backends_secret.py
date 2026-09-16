"""MAOP Secret Backend Abstractions.

Extracted from ``backends.py`` to keep each module under 500 lines.
Contains the secrets management backend ABC, its default local encrypted
implementation, the environment-driven factory function, and the
convenience helpers.

Backend provided:
  - SecretBackend (Local / HashiCorp Vault)

Selection via env var::

    MAOP_SECRET_BACKEND=local|vault
"""

from __future__ import annotations

import logging
import os
import threading
from abc import ABC, abstractmethod

from maop.config.edition import get_edition, record_degradation

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# SecretBackend — secrets / API key management
# ═══════════════════════════════════════════════════════════════════════

class SecretBackend(ABC):
    """Abstract secrets management backend.

    Default: Local Fernet-encrypted vault.
    Cloud:   HashiCorp Vault.
    """

    @abstractmethod
    def get_secret(self, key: str) -> str | None:
        ...

    @abstractmethod
    def set_secret(self, key: str, value: str) -> None:
        ...

    @abstractmethod
    def delete_secret(self, key: str) -> bool:
        ...

    @abstractmethod
    def list_secrets(self, prefix: str = "") -> list[str]:
        ...


class LocalSecretBackend(SecretBackend):
    """Default local encrypted vault (delegates to api_key_vault)."""

    def __init__(self, root_dir: str = "") -> None:
        from maop.core.security.api_key_vault import ApiKeyVault
        self._vault = ApiKeyVault(root_dir=root_dir) if root_dir else ApiKeyVault()

    def get_secret(self, key: str) -> str | None:
        return self._vault.retrieve(key)

    def set_secret(self, key: str, value: str) -> None:
        self._vault.store(key, value)

    def delete_secret(self, key: str) -> bool:
        return self._vault.delete(key)

    def list_secrets(self, prefix: str = "") -> list[str]:
        return [k for k in self._vault.list_providers() if k.startswith(prefix)]


# ═══════════════════════════════════════════════════════════════════════
# Factory functions — environment variable driven
# ═══════════════════════════════════════════════════════════════════════

_secret: SecretBackend | None = None

# 工厂单例锁：保护 get_*_backend() 中的 check-then-set 临界区，
# 避免多线程并发首次调用时重复创建后端实例（来源：并发安全审计经验）。
_factory_lock = threading.Lock()


def _edition_defaults() -> dict[str, str]:
    """Return default backend types based on MAOP edition.

    Delegates to ``config.edition.backend_defaults()`` — the single source
    of truth.  Individual MAOP_*_BACKEND env vars always override edition
    defaults.
    """
    from maop.config.edition import backend_defaults
    return backend_defaults()


def get_secret_backend(root_dir: str = "") -> SecretBackend:
    """Get the configured secrets backend.

    Selection priority:
      1. MAOP_SECRET_BACKEND env var (explicit override)
      2. MAOP_EDITION=enterprise → HashiCorp Vault
      3. Default → Local encrypted vault

    Fail-fast policy: 与 storage/cache/queue/kv 一致，Vault 不可用时
    默认抛出 RuntimeError，只有 ``MAOP_SECRET_ALLOW_FALLBACK=1`` 时才
    降级到 local 加密 vault。
    """
    global _secret
    if _secret is not None:
        return _secret
    with _factory_lock:
        if _secret is not None:
            return _secret
        defaults = _edition_defaults()
        backend_type = os.getenv("MAOP_SECRET_BACKEND", defaults["secret"]).lower()
        if backend_type == "vault":
            try:
                from maop.core.backends.backends_vault import VaultSecretBackend
                _secret = VaultSecretBackend()
                logger.info("[backends] Secrets: HashiCorp Vault (edition=%s)", get_edition().value)
                return _secret
            except (ImportError, RuntimeError, OSError) as exc:
                # Secret backend 默认 fail-fast（与 storage/cache/queue/kv 一致）。
                # 只有 MAOP_SECRET_ALLOW_FALLBACK=1 时才降级到 local 加密 vault。
                # A missing Vault server, failed auth, or unresolvable address must
                # not silently degrade — fail fast unless explicitly opted in.
                if os.getenv("MAOP_SECRET_ALLOW_FALLBACK", "0") == "1":
                    logger.warning(
                        "[backends] Vault secrets backend unavailable (%s: %s), "
                        "MAOP_SECRET_ALLOW_FALLBACK=1 → falling back to local",
                        type(exc).__name__, exc,
                    )
                    record_degradation("secret", "vault", "local", "unavailable_vault")
                else:
                    raise RuntimeError(
                        f"Vault secrets backend was requested (MAOP_SECRET_BACKEND={backend_type}) "
                        f"but is unavailable ({type(exc).__name__}: {exc}). "
                        "Install hvac/backends_vault deps and check MAOP_VAULT_ADDR/MAOP_VAULT_TOKEN, "
                        "or set MAOP_SECRET_ALLOW_FALLBACK=1 to allow degrading to local."
                    ) from exc
        _secret = LocalSecretBackend(root_dir=root_dir)
        logger.debug("[backends] Secrets: Local")
        return _secret


def _reset_secret() -> None:
    """Reset cached secret backend instance (useful for testing)."""
    global _secret
    _secret = None


# ═══════════════════════════════════════════════════════════════════════
# Convenience helpers — drop-in replacements for direct module access
# ═══════════════════════════════════════════════════════════════════════

def secret_get(key: str) -> str | None:
    """Shortcut: get_secret_backend().get_secret(...)."""
    return get_secret_backend().get_secret(key)


def secret_set(key: str, value: str) -> None:
    """Shortcut: get_secret_backend().set_secret(...)."""
    return get_secret_backend().set_secret(key, value)