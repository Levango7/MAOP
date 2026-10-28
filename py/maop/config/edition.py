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
import threading
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
    # 通知中心：后端（/api/notifications/*）依赖 `maop.enterprise.notification`
    # 的 EventBus / NotificationManager，个人版首次调用即 ImportError，故整个
    # 功能（含前端入口）只对企业版开放。个人版的告警入站 /api/alerts 不受影响。
    NOTIFICATION_CENTER = "notification_center"


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
    FeatureFlag.NOTIFICATION_CENTER,
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
# P2-5 fix: 保护模块级可变状态（_current_edition / _feature_overrides /
# _degradation_log）的并发读写。detect_edition() 的 check-then-set
# （if _current_edition is not None: return ...; _current_edition = ...）
# 在无锁时存在 TOCTOU 竞态：多线程可能同时通过 None 检查，重复执行
# license 校验并覆盖彼此结果。用模块级 Lock 保护所有读写这些状态的
# 复合操作。
# （来源：coding-pattern/python-shared-dict-cache-concurrency-audit-fix-playbook）
_edition_lock = threading.Lock()


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
    with _edition_lock:
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

    with _edition_lock:
        _current_edition = result
    # 企业版已激活时再做一次 MAOS 版本地板校验（见 `_check_maos_version_floor`）。
    # 放在缓存赋值之后：即便校验抛错，进程状态也已是终态，不会出现
    # "半初始化 edition" 让后续 get_edition() 返回陈旧值的窗口。
    if result is Edition.ENTERPRISE:
        _check_maos_version_floor()
    return result


# ════════════════════════════════════════════════════════════════════════════
# MAOS（maop-enterprise）版本地板
# ════════════════════════════════════════════════════════════════════════════

#: MAOP 侧要求的最低 MAOS 发行版（与 README「版本兼容地板」节一致）。
#: 5.2.3 起 MAOS 的 ``tenant_id=""`` 不再等于"所有租户"，MAOP 5.2.1 的通知
#: 列表端点据此改为显式传 ``all_tenants=True``；旧 MAOS 收到该参数会 TypeError
#: （路由 500），新 MAOS 配旧 MAOP 则 admin 列表恒空 —— 属于"装得上、跑不动"。
MAOS_DIST_NAME = "maop-enterprise"
MIN_MAOS_VERSION = "5.2.3"

#: 开发/联调逃生阀：允许在版本地板不满足时继续启动（仅告警）。
MAOS_VERSION_SKIP_ENV = "MAOP_SKIP_MAOS_VERSION_CHECK"


def _parse_version_tuple(text: str) -> tuple[int, ...]:
    """``"5.2.3"`` / ``"5.2.3rc1"`` → 可比较的数字元组（预发布按主版本比较）。"""
    parts: list[int] = []
    for piece in str(text).strip().split("."):
        digits = ""
        for ch in piece:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def get_installed_maos_version() -> str:
    """已安装的 ``maop-enterprise`` 发行版版本号；不可用/读不到时返回 ``""``。"""
    try:
        from importlib.metadata import version

        return version(MAOS_DIST_NAME)
    except Exception:
        return ""


def _check_maos_version_floor() -> None:
    """企业版激活后校验 MAOS 版本下限，避免"装得上、跑不动"。

    为什么不能只靠 ``pyproject.toml``：MAOS 是私有包，``maop-enterprise`` 既不在
    ``maop-orchestrator`` 的依赖里，也不从 PyPI 解析（约束只能写在 README 的
    「版本兼容地板」节）。而 ``pip install --no-deps``、手工拼 wheel、旧 venv
    残留都会绕过安装期检查，症状是运行期零散 500（通知列表 TypeError /
    admin 列表恒空），排查成本极高。这里在 edition 决策完成处 fail fast。

    行为：
      - 读到版本且 >= 地板 → 通过
      - 读到版本且 < 地板 → ``RuntimeError``（可用 ``MAOP_SKIP_MAOS_VERSION_CHECK=1``
        或 test runner 环境降级为 ERROR 日志 —— 测试需要强制 edition 的场景不应
        被版本地板打断）
      - 读不到版本 → WARNING（被裁剪的 wheel / editable 元数据缺失），不阻断
    """
    if os.getenv(MAOS_VERSION_SKIP_ENV, "").strip().lower() in ("1", "true", "yes"):
        logger.warning(
            "[edition] %s set — skipping MAOS version floor check (>=%s)",
            MAOS_VERSION_SKIP_ENV, MIN_MAOS_VERSION,
        )
        return

    installed = get_installed_maos_version()
    if not installed:
        logger.warning(
            "[edition] Cannot determine %s version; skipping floor check (>=%s)",
            MAOS_DIST_NAME, MIN_MAOS_VERSION,
        )
        return

    if _parse_version_tuple(installed) >= _parse_version_tuple(MIN_MAOS_VERSION):
        return

    msg = (
        f"{MAOS_DIST_NAME} {installed} is below the version floor "
        f"{MIN_MAOS_VERSION} required by this MAOP build. "
        f"Upgrade with: pip install -U '{MAOS_DIST_NAME}>={MIN_MAOS_VERSION}'"
    )
    if _in_test_runner():
        logger.error("[edition] %s (ignored under test runner)", msg)
        return
    raise RuntimeError(msg)


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
        except ImportError:  # silent-ok: optional-dep — 可选依赖未安装
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


def _in_test_runner() -> bool:
    """True when running under a test runner (pytest).

    Used to scope the ``set_edition`` / ``set_feature_override`` guards:
    test suites legitimately force editions without a license (fixtures
    such as MAOS's ``enterprise_edition``), while deployments must not be
    able to flip editions programmatically without one.

    This exemption is deliberate and NOT a security boundary against
    in-process attackers (who could assign ``_current_edition`` directly);
    it exists so the documented API path cannot casually unlock enterprise
    features in a deployed process. Production keeps the full license
    requirement regardless of the test-runner check.
    """
    if "PYTEST_CURRENT_TEST" in os.environ:
        return True
    import sys

    return "pytest" in sys.modules


def _ensure_enterprise_activation_allowed() -> None:
    """Refuse programmatic ENTERPRISE activation without a valid license.

    2026-09-28 bypass closure: previously only production was guarded
    (M-2 fix), so a default deployment (``MAOP_ENV`` unset → development)
    could unlock every enterprise feature with one in-process
    ``set_edition("enterprise")`` call and no license. Now any
    non-test-runner context requires license validation; test runners are
    exempt (see :func:`_in_test_runner`).

    Raises:
        RuntimeError: enterprise package missing or no valid license.
    """
    try:
        from maop.enterprise.license import LicenseValidator

        info = LicenseValidator().validate_from_env()
        if info is None:
            env = os.getenv("MAOP_ENV", "development").lower()
            raise RuntimeError(
                "SECURITY: set_edition(ENTERPRISE) refused without a valid "
                f"MAOP_LICENSE_KEY (env={env}). Activate via license instead "
                "(MAOP_LICENSE_KEY or data/license.key)."
            )
    except ImportError:
        raise RuntimeError(
            "SECURITY: set_edition(ENTERPRISE) refused without the "
            "enterprise package + valid license."
        )


def set_edition(edition: Edition | str) -> None:
    """Programmatically set the edition (overrides env detection).

    M-2 fix (2026-08-30): blocked in production, mirroring
    ``set_feature_override``. ``set_edition(ENTERPRISE)`` is an in-process
    license bypass — without this guard any code path (plugin, test
    leftover, injected module) could flip a production personal deployment
    to ENTERPRISE without a license.

    2026-09-28 bypass closure: the production-only guard left the default
    deployment shape (``MAOP_ENV`` unset → development) unprotected — one
    line of in-process code unlocked the whole enterprise surface. Now
    ENTERPRISE requires a valid license in EVERY environment; only test
    runners are exempt (see :func:`_in_test_runner`).
    """
    if isinstance(edition, str):
        edition = Edition(edition.lower())
    if edition == Edition.ENTERPRISE and not _in_test_runner():
        _ensure_enterprise_activation_allowed()
    global _current_edition
    with _edition_lock:
        _current_edition = edition
    logger.info("[edition] Set to %s", edition.value)


def reset_edition() -> None:
    """Reset edition detection (useful for testing)."""
    global _current_edition, _feature_overrides, _degradation_log
    with _edition_lock:
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
    with _edition_lock:
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

    2026-09-28: also blocked outside test runners in non-production — the
    production-only check let a default (development-mode) deployment
    unlock enterprise-only flags with one call and no license. There are
    no production callers of this function; deployments activate features
    via edition + license (``detect_edition``), not overrides. Tests are
    exempt (see :func:`_in_test_runner`).
    """
    if os.getenv("MAOP_ENV", "development").lower() == "production":
        raise RuntimeError("Feature overrides are not allowed in production")
    if not _in_test_runner():
        raise RuntimeError(
            "SECURITY: set_feature_override() refused outside test runners — "
            "feature availability is decided by edition + license, not "
            "runtime overrides."
        )
    if isinstance(flag, str):
        flag = FeatureFlag(flag)
    with _edition_lock:
        _feature_overrides[flag] = enabled


def clear_feature_overrides() -> None:
    """Remove all per-feature overrides."""
    global _feature_overrides
    with _edition_lock:
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
    with _edition_lock:
        _degradation_log.append(entry)
    logger.warning(
        "[edition] Degradation: %s backend '%s' unavailable, falling back to '%s' (%s)",
        backend, requested, fallback, reason,
    )
    try:
        import json
        # P2-6 fix: 优先读 MAOP_ROOT_DIR（当前规范环境变量），回退到旧名
        # MAOP_ROOT 以保持向后兼容，最终回退到 "." 。
        root_dir = os.getenv("MAOP_ROOT_DIR", os.getenv("MAOP_ROOT", "."))
        log_path = os.path.join(root_dir, "data", "degradation.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as exc:
        # t88-L3: previously `except OSError: pass` silently swallowed
        # persistence failures. The in-memory _degradation_log still
        # captured the event (above), so the audit trail is not lost for
        # this process, but the on-disk JSONL log would silently stop
        # appending — making cross-restart audit impossible to debug.
        # Per the silent-swallow key-path focus strategy, persistence
        # failures must be surfaced via logger.warning (not error, since
        # the system continues to operate with the in-memory log).
        logger.warning(
            "[edition] Failed to persist degradation event to %s "
            "(in-memory log still captured this entry): %s",
            log_path, exc, exc_info=True,
        )


def degradation_log() -> list[dict[str, str]]:
    """Return all recorded degradation events."""
    with _edition_lock:
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
