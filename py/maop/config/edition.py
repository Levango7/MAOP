"""MAOP Edition Registry — Single Source of Truth for feature flags.

Centralises every edition-dependent decision so that no other module
duplicates edition logic.  All "is this feature available?" queries
flow through ``has_feature()`` / ``require_feature()``.

Architecture:
  - ``Edition`` enum: PERSONAL | ENTERPRISE
  - ``FeatureFlag`` enum: every toggleable capability
  - ``_FEATURE_MAP``: which features are ON per edition
  - ``detect_edition()``: auto-detect from env / installed packages
  - ``has_feature()`` / ``require_feature()``: runtime gate
  - ``edition_info()``: structured metadata for /api/info/edition
"""

from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class Edition(str, Enum):
    PERSONAL = "personal"
    ENTERPRISE = "enterprise"


class FeatureFlag(str, Enum):
    RBAC = "rbac"
    AUDIT_LOG = "audit_log"
    MULTI_USER = "multi_user"
    SSO = "sso"
    DASHBOARD_ANALYTICS = "dashboard_analytics"
    VUE_DASHBOARD = "vue_dashboard"
    POSTGRESQL = "postgresql"
    REDIS = "redis"
    RABBITMQ = "rabbitmq"
    VAULT = "vault"
    ETCD = "etcd"
    TENANT_ISOLATION = "tenant_isolation"
    COST_TRACKING = "cost_tracking"
    CIRCUIT_BREAKER = "circuit_breaker"
    MEMORY_STORE = "memory_store"
    HOT_RELOAD = "hot_reload"
    HOOKS = "hooks"
    PLUGIN_SYSTEM = "plugin_system"
    MCP_HUB = "mcp_hub"
    VECTOR_SEARCH = "vector_search"
    REACT_LOOP = "react_loop"
    BUDGET_GUARD = "budget_guard"
    TLS_AUTO = "tls_auto"
    AUTH_AUTO = "auth_auto"


_PERSONAL_FEATURES: frozenset[FeatureFlag] = frozenset({
    FeatureFlag.COST_TRACKING,
    FeatureFlag.CIRCUIT_BREAKER,
    FeatureFlag.MEMORY_STORE,
    FeatureFlag.HOT_RELOAD,
    FeatureFlag.HOOKS,
    FeatureFlag.PLUGIN_SYSTEM,
    FeatureFlag.MCP_HUB,
    FeatureFlag.VECTOR_SEARCH,
    FeatureFlag.REACT_LOOP,
    FeatureFlag.BUDGET_GUARD,
})

_ENTERPRISE_FEATURES: frozenset[FeatureFlag] = frozenset({
    FeatureFlag.RBAC,
    FeatureFlag.AUDIT_LOG,
    FeatureFlag.MULTI_USER,
    FeatureFlag.SSO,
    FeatureFlag.DASHBOARD_ANALYTICS,
    FeatureFlag.VUE_DASHBOARD,
    FeatureFlag.POSTGRESQL,
    FeatureFlag.REDIS,
    FeatureFlag.RABBITMQ,
    FeatureFlag.VAULT,
    FeatureFlag.ETCD,
    FeatureFlag.TENANT_ISOLATION,
    FeatureFlag.TLS_AUTO,
    FeatureFlag.AUTH_AUTO,
})

_FEATURE_MAP: dict[Edition, frozenset[FeatureFlag]] = {
    Edition.PERSONAL: _PERSONAL_FEATURES,
    Edition.ENTERPRISE: _PERSONAL_FEATURES | _ENTERPRISE_FEATURES,
}

_BACKEND_DEFAULTS: dict[Edition, dict[str, str]] = {
    Edition.PERSONAL: {
        "storage": "sqlite",
        "cache": "memory",
        "queue": "sqlite",
        "kv": "sqlite",
        "secret": "local",
    },
    Edition.ENTERPRISE: {
        "storage": "postgresql",
        "cache": "redis",
        "queue": "rabbitmq",
        "kv": "etcd",
        "secret": "vault",
    },
}

_current_edition: Edition | None = None
_feature_overrides: dict[FeatureFlag, bool] = {}
_degradation_log: list[dict[str, str]] = []


def detect_edition() -> Edition:
    """Auto-detect the running edition.

    Priority:
      1. Explicitly set via ``set_edition()`` (programmatic override)
      2. ``MAOP_EDITION`` environment variable
      3. ``maop.enterprise`` package importable → ENTERPRISE
      4. Default → PERSONAL
    """
    global _current_edition
    if _current_edition is not None:
        return _current_edition

    env_val = os.getenv("MAOP_EDITION", "").lower().strip()
    if env_val in ("enterprise", "ent"):
        return Edition.ENTERPRISE
    if env_val in ("personal", "pers", "community"):
        return Edition.PERSONAL

    try:
        if _is_enterprise_package_installed():
            return Edition.ENTERPRISE
    except Exception:
        pass

    return Edition.PERSONAL


def set_edition(edition: Edition | str) -> None:
    """Programmatically set the edition (overrides env detection)."""
    global _current_edition
    if isinstance(edition, str):
        edition = Edition(edition.lower())
    _current_edition = edition
    logger.info("[edition] Set to %s", edition.value)


def reset_edition() -> None:
    """Reset edition detection (useful for testing)."""
    global _current_edition, _feature_overrides, _degradation_log
    _current_edition = None
    _feature_overrides = {}
    _degradation_log = []


def get_edition() -> Edition:
    """Return the current edition (detects if not yet set)."""
    return detect_edition()


def has_feature(flag: FeatureFlag | str) -> bool:
    """Check if a feature flag is enabled for the current edition.

    Per-feature overrides (via ``set_feature_override``) take precedence
    over edition defaults.
    """
    if isinstance(flag, str):
        flag = FeatureFlag(flag)
    if flag in _feature_overrides:
        return _feature_overrides[flag]
    return flag in _FEATURE_MAP.get(get_edition(), frozenset())


def require_feature(flag: FeatureFlag | str) -> None:
    """Assert that a feature is available; raise ``FeatureNotAvailable`` if not."""
    if isinstance(flag, str):
        flag = FeatureFlag(flag)
    if not has_feature(flag):
        raise FeatureNotAvailable(flag, get_edition())


def set_feature_override(flag: FeatureFlag | str, enabled: bool) -> None:
    """Override a single feature flag regardless of edition.

    Useful for testing or for gradual rollouts.
    """
    if isinstance(flag, str):
        flag = FeatureFlag(flag)
    _feature_overrides[flag] = enabled


def clear_feature_overrides() -> None:
    """Remove all per-feature overrides."""
    global _feature_overrides
    _feature_overrides = {}


def backend_defaults() -> dict[str, str]:
    """Return default backend types for the current edition."""
    return dict(_BACKEND_DEFAULTS.get(get_edition(), _BACKEND_DEFAULTS[Edition.PERSONAL]))


def all_features() -> dict[str, bool]:
    """Return all feature flags and their status for the current edition."""
    return {f.value: has_feature(f) for f in FeatureFlag}


def record_degradation(backend: str, requested: str, fallback: str, reason: str = "import_error") -> None:
    """Record a backend degradation event (enterprise → personal fallback)."""
    entry = {
        "backend": backend,
        "requested": requested,
        "fallback": fallback,
        "reason": reason,
    }
    _degradation_log.append(entry)
    logger.warning(
        "[edition] Degradation: %s backend '%s' unavailable, falling back to '%s' (%s)",
        backend, requested, fallback, reason,
    )


def degradation_log() -> list[dict[str, str]]:
    """Return all recorded degradation events."""
    return list(_degradation_log)


def edition_info() -> dict[str, Any]:
    """Structured metadata for ``/api/info/edition`` endpoint."""
    ed = get_edition()
    return {
        "edition": ed.value,
        "features": all_features(),
        "backends": backend_defaults(),
        "enterprise_available": _is_enterprise_package_installed(),
        "degradations": degradation_log(),
    }


def _is_enterprise_package_installed() -> bool:
    """Check if ``maop.enterprise`` is importable."""
    try:
        import maop.enterprise as _  # noqa: F401
        return True
    except ImportError:
        return False


class FeatureNotAvailable(Exception):
    """Raised when an enterprise-only feature is accessed in personal edition."""

    def __init__(self, flag: FeatureFlag, edition: Edition) -> None:
        self.flag = flag
        self.edition = edition
        super().__init__(
            f"Feature '{flag.value}' is not available in {edition.value} edition. "
            f"Upgrade to MAOP Enterprise or enable it via set_feature_override()."
        )