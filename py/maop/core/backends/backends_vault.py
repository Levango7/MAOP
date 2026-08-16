"""MAOP HashiCorp Vault Secret Backend.

Implements SecretBackend ABC for HashiCorp Vault, used when:
  - MAOP_SECRET_BACKEND=vault
  - MAOP_EDITION=enterprise (and hvac is installed)

Connection config via environment variables:
  - MAOP_VAULT_ADDR       — Vault server URL (default http://127.0.0.1:8200)
  - MAOP_VAULT_TOKEN      — Vault auth token
  - MAOP_VAULT_MOUNT      — KV v2 mount path (default secret)
  - MAOP_VAULT_PATH       — base path for secrets (default maop)

Falls back to LocalSecretBackend with a degradation warning if hvac is not installed.
"""

from __future__ import annotations

import logging
import os

from maop.core.backends.backends import SecretBackend

logger = logging.getLogger(__name__)


class VaultSecretBackend(SecretBackend):
    """HashiCorp Vault secrets backend using hvac client."""

    def __init__(self) -> None:
        import hvac
        self._addr = os.getenv("MAOP_VAULT_ADDR", "http://127.0.0.1:8200")
        self._token = os.getenv("MAOP_VAULT_TOKEN", "")
        self._mount = os.getenv("MAOP_VAULT_MOUNT", "secret")
        self._base_path = os.getenv("MAOP_VAULT_PATH", "maop")
        self._client = hvac.Client(url=self._addr, token=self._token)
        if not self._client.is_authenticated():
            raise RuntimeError(f"Vault authentication failed at {self._addr}")
        self._ensure_mount()

    def _ensure_mount(self) -> None:
        """Ensure KV v2 mount exists; create if missing."""
        try:
            mounts = self._client.sys.list_mounted_secrets_engines()
            if self._mount + "/" not in mounts:
                self._client.sys.enable_secrets_engine(
                    backend_type="kv",
                    path=self._mount,
                    options={"version": "2"},
                )
                logger.info("[vault] Created KV v2 mount: %s", self._mount)
        except Exception as exc:
            logger.warning("[vault] Mount check/create failed: %s", exc)

    def _full_path(self, key: str) -> str:
        return f"{self._base_path}/{key}"

    def get_secret(self, key: str) -> str | None:
        try:
            resp = self._client.secrets.kv.v2.read_secret_version(
                path=self._full_path(key), mount_point=self._mount,
            )
            if resp and "data" in resp and "data" in resp["data"]:
                return resp["data"]["data"].get("value")  # type: ignore
        except Exception as exc:
            logger.debug("[vault] get_secret(%s) failed: %s", key, exc)
        return None

    def set_secret(self, key: str, value: str) -> None:
        self._client.secrets.kv.v2.create_or_update_secret(
            path=self._full_path(key), mount_point=self._mount,
            secret={"value": value},
        )

    def delete_secret(self, key: str) -> bool:
        try:
            self._client.secrets.kv.v2.delete_metadata_and_all_versions(
                path=self._full_path(key), mount_point=self._mount,
            )
            return True
        except Exception as exc:
            logger.debug("[vault] delete_secret(%s) failed: %s", key, exc)
            return False

    def list_secrets(self, prefix: str = "") -> list[str]:
        try:
            path = self._base_path + ("/" + prefix if prefix else "")
            resp = self._client.secrets.kv.v2.list_secrets(
                path=path, mount_point=self._mount,
            )
            keys = resp.get("data", {}).get("keys", [])
            # Strip trailing / from directory entries
            return [k.rstrip("/") for k in keys]
        except Exception as exc:
            logger.debug("[vault] list_secrets(%s) failed: %s", prefix, exc)
            return []

    def close(self) -> None:
        """Close the Vault client (hvac has no explicit close, but for API compatibility)."""
        self._client.adapter.close()
