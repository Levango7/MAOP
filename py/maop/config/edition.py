"""MAOP/MAOS Edition Registry — Single Source of Truth for feature flags.

品牌定位:
  - MAOP (Multi-Agent Orchestration Platform) — 个人版, 免费开源
  - MAOS (Multi-Agent Orchestration Suite)    — 企业版, 需 License

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

OPTIONAL backends (implemented, gated by optional deps + env override):
  - ``FeatureFlag.RABBITMQ``: RabbitMQ queue backend
    (``maop/core/backends_rabbitmq.py`` IS implemented; requires the
    optional ``pika`` dependency).  Enable at runtime via
    ``MAOP_QUEUE_BACKEND=rabbitmq``.  If ``pika`` is missing, the factory
    in ``core/backends.py`` degrades to Redis and records a degradation.
  - ``FeatureFlag.ETCD``: etcd/Consul distributed KV backend
    (``maop/core/backends_distributed.py`` IS implemented; requires the
    optional ``etcd3`` dependency).  Enable via ``MAOP_KV_BACKEND=etcd``.
    If ``etcd3`` is missing, the factory degrades to SQLite.

  These two flags are intentionally **excluded** from
  ``_ENTERPRISE_FEATURES`` to keep ``/api/info/edition`` honest about
  what is bundled by default (the dependencies are optional extras, not
  hard requirements).  The enum values are retained for backward
  compatibility (so existing string comparisons / config files do not
  break).  Enable them through the documented env overrides rather than
  the feature flag.  Do NOT re-add them to ``_ENTERPRISE_FEATURES``
  unless the optional dependencies become mandatory for the edition.
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
    RABBITMQ = "rabbitmq"  # OPTIONAL — backend implemented; enable via MAOP_QUEUE_BACKEND=rabbitmq (needs pika)
    VAULT = "vault"
    ETCD = "etcd"  # OPTIONAL — backend implemented; enable via MAOP_KV_BACKEND=etcd (needs etcd3)
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
    N8N_INTEGRATION = "n8n_integration"
    LICENSE_MANAGEMENT = "license_management"


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

# 注意：RABBITMQ 和 ETCD 未加入此集合，因为对应 backend 模块
# (backends_rabbitmq.py / backends_distributed.py) 虽已实现，但依赖
# 可选第三方包（pika / etcd3），不属于默认捆绑能力。
# 详见模块 docstring 中的 OPTIONAL backends 说明。
_ENTERPRISE_FEATURES: frozenset[FeatureFlag] = frozenset({
    FeatureFlag.RBAC,
    FeatureFlag.AUDIT_LOG,
    FeatureFlag.MULTI_USER,
    FeatureFlag.SSO,
    FeatureFlag.DASHBOARD_ANALYTICS,
    FeatureFlag.VUE_DASHBOARD,
    FeatureFlag.POSTGRESQL,
    FeatureFlag.REDIS,
    FeatureFlag.VAULT,
    FeatureFlag.TENANT_ISOLATION,
    FeatureFlag.TLS_AUTO,
    FeatureFlag.AUTH_AUTO,
    FeatureFlag.N8N_INTEGRATION,
    FeatureFlag.LICENSE_MANAGEMENT,
})

_FEATURE_MAP: dict[Edition, frozenset[FeatureFlag]] = {
    Edition.PERSONAL: _PERSONAL_FEATURES,
    Edition.ENTERPRISE: _PERSONAL_FEATURES | _ENTERPRISE_FEATURES,
}

# Enterprise 默认 backend 与实际实现保持一致：
# - queue: "redis"（rabbitmq 已实现但为可选依赖，需 pika）
# - kv: "sqlite"（etcd 已实现但为可选依赖，需 etcd3）
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
        "queue": "redis",
        "kv": "sqlite",
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
      3. ``maop.enterprise`` package importable → ENTERPRISE candidate (license check required)
      4. Default → PERSONAL

    授权模型（2026-08-11 防破解加固）:
      license 校验在 :func:`_detect_with_license_check` 中执行,缺失/无效降级为 PERSONAL。

    结果缓存（2026-08-11 fix）:
      首次调用后结果写入 ``_current_edition``,后续直接复用——避免每次
      get_edition/has_feature/edition_info 都重复触发 license 校验、
      重复追加 degradation 日志。若需刷新,显式调用 ``reset_edition()``.
    """
    global _current_edition
    if _current_edition is not None:
        return _current_edition

    # 重入保护 (2026-08-11): import maop.enterprise 时其 __init__ 会调
    # get_edition(), 而此时 detect_edition() 正在执行、_current_edition 尚未赋值,
    # 导致重复进入 _detect_with_license_check、重复记录 degradation。
    # 先占位为 PERSONAL(保守值),检测完成后覆盖为真实结果。
    _current_edition = Edition.PERSONAL

    result: Edition
    env_val = os.getenv("MAOP_EDITION", "").lower().strip()
    if env_val in ("enterprise", "ent"):
        result = _detect_with_license_check(Edition.ENTERPRISE)
    elif env_val in ("personal", "pers", "community"):
        result = Edition.PERSONAL
    else:
        try:
            if _is_enterprise_package_installed():
                # 二次重入保护: import maop.enterprise 触发递归调用时,
                # 这里 _current_edition=PERSONAL 占位符已被该调用读到,
                # 但本次(外层)detect 仍要继续走完 license 校验。
                result = _detect_with_license_check(Edition.ENTERPRISE)
            else:
                result = Edition.PERSONAL
        except ImportError:
            logger.debug("Enterprise package not available")
            result = Edition.PERSONAL
        except Exception:
            logger.exception("[edition] Unexpected error during edition detection")
            result = Edition.PERSONAL

    _current_edition = result
    return result


def _detect_with_license_check(requested: Edition) -> Edition:
    """Verify license when enterprise is requested; degrade on failure.

    授权模型（2026-08-11 防破解加固）:
      - 单发行包:maop 与 maop.enterprise 同一 wheel 发布（不再分离）.
      - 认证才激活:enterprise 功能必须提供有效 license key,否则静默降级为 personal.
      - 原 honor-system(企业包可导入即放行)视为安全漏洞,已移除.

    行为:
      - 有有效 license → ENTERPRISE
      - 无 license 或无效 → PERSONAL(开发模式下日志 INFO,生产 ERROR)
      - 验证器内部错误 → PERSONAL(保守失败)
    """
    if requested != Edition.ENTERPRISE:
        return requested

    # Stage 1: import the license module.  If it is unavailable we degrade
    # to PERSONAL immediately — this also guarantees that ``LicenseError``
    # is bound before the ``except LicenseError`` clause in stage 2.
    try:
        from maop.enterprise.license import LicenseError, LicenseValidator
    except ImportError:
        # maop.enterprise.license not available — treat as no license.
        # Prior behavior honored package detection; that is a bypass path
        # (patch out the license module to get enterprise) — now degraded.
        logger.info(
            "[edition] License validation module unavailable; "
            "enterprise features disabled (personal edition)."
        )
        record_degradation("license", "enterprise", "personal", "no_license_module")
        return Edition.PERSONAL

    # Stage 2: validate the license.  LicenseError is guaranteed to be
    # defined here because stage 1 returned early on ImportError.
    try:
        validator = LicenseValidator()
        info = validator.validate_from_env()
        if info is None:
            is_prod = os.getenv("MAOP_ENV", "development").lower() == "production"
            if is_prod:
                logger.error(
                    "[edition] MAOP_LICENSE_KEY is required in production. "
                    "Degrading to PERSONAL."
                )
            else:
                logger.info(
                    "[edition] No license key; enterprise features disabled "
                    "(personal edition). Set MAOP_LICENSE_KEY to activate."
                )
            record_degradation("license", "enterprise", "personal", "no_license_key")
            return Edition.PERSONAL
        # License validated successfully — now anti-tamper: also verify the
        # enterprise modules themselves match the signed manifest (if present).
        # A tampered module set degrades to PERSONAL even with a valid license.
        # BUT: a missing manifest (e.g. dev build before signing) is a warning,
        # not a degradation — only signature/hash mismatches downgrade.
        try:
            from maop.enterprise.license import verify_module_integrity
            ok, reason = verify_module_integrity(strict=False)
            if not ok and "manifest not found" not in reason:
                logger.error(
                    "[edition] Enterprise modules failed integrity check (%s). "
                    "Degrading to PERSONAL despite valid license.", reason,
                )
                record_degradation("license", "enterprise", "personal", "module_tampered")
                return Edition.PERSONAL
            elif not ok:
                logger.debug("[edition] integrity manifest not present; skipping check")
        except ImportError:
            pass  # older installs without integrity module — license check already passed

        logger.info(
            "[edition] Enterprise license valid for '%s' (expires %s)",
            info.customer, info.expires_at.isoformat(),
        )
        return Edition.ENTERPRISE
    except LicenseError as exc:
        logger.error(
            "[edition] License validation failed: %s. Degrading to PERSONAL.", exc
        )
        record_degradation("license", "enterprise", "personal", "license_invalid")
        return Edition.PERSONAL
    except Exception:
        logger.exception(
            "[edition] Unexpected error during license validation. "
            "Degrading to PERSONAL.",
        )
        record_degradation("license", "enterprise", "personal", "license_error")
        return Edition.PERSONAL


def set_edition(edition: Edition | str) -> None:
    """Programmatically set the edition (overrides env detection).

    M-2 fix (2026-08-30): blocked in production, mirroring
    ``set_feature_override``. ``set_edition(ENTERPRISE)`` is an in-process
    license bypass — without this guard any code path (plugin, test
    leftover, injected module) could flip a production personal deployment
    to ENTERPRISE without a license. Programmatic overrides remain
    available in dev/test; production must activate via
    ``MAOP_LICENSE_KEY`` + ``detect_edition()`` instead.
    """
    if isinstance(edition, str):
        edition = Edition(edition.lower())
    if edition == Edition.ENTERPRISE and os.getenv("MAOP_ENV", "development").lower() == "production":
        try:
            # The enterprise package's __init__ calls set_edition(ENTERPRISE)
            # at import time as its "present = activated" mechanism; that
            # legitimate path is allowed to proceed through license
            # validation below. A *raw* programmatic call in production is
            # not.
            from maop.enterprise.license import LicenseValidator
            info = LicenseValidator().validate_from_env()
            if info is None:
                raise RuntimeError(
                    "SECURITY: set_edition(ENTERPRISE) refused in production "
                    "without a valid MAOP_LICENSE_KEY. Use license-based "
                    "activation (MAOP_LICENSE_KEY) in production."
                )
        except ImportError:
            raise RuntimeError(
                "SECURITY: set_edition(ENTERPRISE) refused in production "
                "without the enterprise package + valid license."
            )
    global _current_edition
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
    Blocked in production to prevent runtime tampering.
    """
    if os.getenv("MAOP_ENV", "development").lower() == "production":
        raise RuntimeError("Feature overrides are not allowed in production")
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
    """Record a backend degradation event (enterprise → personal fallback).

    Persists to data/degradation.log (JSONL) for audit trail across restarts.
    """
    from datetime import datetime, timezone

    entry = {
        "backend": backend,
        "requested": requested,
        "fallback": fallback,
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _degradation_log.append(entry)
    logger.warning(
        "[edition] Degradation: %s backend '%s' unavailable, falling back to '%s' (%s)",
        backend, requested, fallback, reason,
    )
    try:
        import json
        log_path = os.path.join(os.getenv("MAOP_ROOT", "."), "data", "degradation.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


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
            f"Upgrade to MAOS or enable it via set_feature_override()."
        )
