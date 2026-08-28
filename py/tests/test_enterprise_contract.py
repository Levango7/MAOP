"""Contract tests: ensure MAOP ↔ MAOS enterprise API doesn't drift.

These tests verify that all ``maop.enterprise.*`` symbols referenced by
the MAOP main package are available when ``maop-enterprise`` is installed.
If ``maop-enterprise`` is not installed, tests are skipped (personal edition).

The contract is derived from grepping the MAOP source tree for
``from maop.enterprise.* import ...`` statements.  When MAOS adds a new
symbol that MAOP references, it must appear here so that a missing symbol
is caught by CI rather than at runtime.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

# ── Path reconciliation ────────────────────────────────────────────
# MAOP's conftest.py imports ``maop.dashboard.server`` at collection time,
# which triggers ``import maop``.  If the MAOP repo root appears earlier in
# ``sys.path`` than the MAOS repo root, Python loads MAOP's plain
# ``maop/__init__.py`` (not a namespace package) and ``maop.enterprise``
# becomes unresolvable.
#
# Fix: ensure the MAOS repo root is at ``sys.path[0]`` so its namespace
# ``__init__.py`` (``pkgutil.extend_path``) runs first and merges both
# directories into ``maop.__path__``.  If ``maop`` was already loaded as a
# plain package, purge it from ``sys.modules`` so the next import picks up
# the namespace version.
_MAOS_PATH = os.environ.get("MAOS_REPO_PATH") or str(
    Path(__file__).resolve().parents[3]  # ../../..  from py/tests/ → MAOS sibling
)
# Fallback: known absolute path
if not (Path(_MAOS_PATH) / "maop" / "enterprise").exists():
    _MAOS_PATH = r"F:\Nexus\MAOS"

if _MAOS_PATH not in sys.path:
    sys.path.insert(0, _MAOS_PATH)
# Force MAOS to position 0 regardless of pytest's own insertions.
if sys.path[0] != _MAOS_PATH:
    sys.path.remove(_MAOS_PATH)
    sys.path.insert(0, _MAOS_PATH)

# If maop was already loaded as a plain package (single __path__ entry),
# purge it so the namespace package is picked up on next import.
if "maop" in sys.modules:
    _existing_maop = sys.modules["maop"]
    if len(getattr(_existing_maop, "__path__", [])) < 2:
        for _key in list(sys.modules):
            if _key == "maop" or _key.startswith("maop."):
                sys.modules.pop(_key, None)
        importlib.invalidate_caches()


import pytest

# ── Contract definition ─────────────────────────────────────────────
# Every module that MAOP references, with the specific symbols it imports.
# Source: grep -rn "maop\.enterprise" py/maop/ in the MAOP repo.
#
# When adding a new enterprise import to MAOP, add the symbol here too.
# When removing an import, remove it here.  This file is the single source
# of truth for the MAOP → MAOS API surface.

ENTERPRISE_CONTRACT: dict[str, list[str]] = {
    # ── Core enterprise modules ────────────────────────────────────
    "maop.enterprise": [],
    "maop.enterprise.rbac": [
        "RBACManager",
        "Role",
        "Permission",
        "ROLE_PERMISSIONS",
    ],
    "maop.enterprise.tenant": [
        "TenantManager",
        "TenantStatus",
        "TenantQuota",
    ],
    "maop.enterprise.sso": [
        "SSOConfig",
        "SSOManager",
        "SSOProvider",
        "SSOError",
    ],
    "maop.enterprise.sso_registry": [
        "SSOProviderRegistry",
    ],
    "maop.enterprise.sso_store": [
        "import_env_provider_if_present",
        "SSOProviderCreate",
        "SSOProviderUpdate",
        "SSOProviderStore",
    ],
    "maop.enterprise.quota": [
        "QuotaManager",
    ],
    "maop.enterprise.quota_middleware": [
        "QuotaMiddleware",
    ],
    "maop.enterprise.audit": [
        "EnterpriseAuditLogger",
        "AuditSeverity",
    ],
    "maop.enterprise.audit_enhanced": [
        "AuditAlertEngine",
        "AuditEventQuery",
        "filter_events",
        "export_events_csv",
        "export_events_json",
        "compute_stats",
        "compute_timeline",
        "compute_heatmap",
        "AuditAlertRuleCreate",
        "AuditAlertRuleUpdate",
    ],
    "maop.enterprise.license": [
        "LicenseValidator",
        "LicenseInfo",
        "LicenseError",
        "verify_module_integrity",
    ],
    "maop.enterprise.license_manager": [
        "LicenseManager",
        "LicenseCreateRequest",
        "LicenseValidateRequest",
        "LicenseNotFoundError",
        "LicenseUpdateRequest",
        "LicenseRenewRequest",
        "LicenseRevokeRequest",
    ],

    # ── Notification sub-package ──────────────────────────────────
    "maop.enterprise.notification": [
        "EventBus",
        "NotificationManager",
    ],
    "maop.enterprise.notification.models": [
        "ChannelCreate",
        "ChannelUpdate",
        "RuleCreate",
        "RuleUpdate",
        "TemplateCreate",
        "NotificationLevel",
        "PreferenceUpdate",
    ],

    # ── Integration modules ───────────────────────────────────────
    "maop.enterprise.n8n": [
        "N8nClient",
        "N8nIntegrationError",
        "handle_n8n_webhook",
    ],
    "maop.enterprise.ha": [
        "HAManager",
    ],
    "maop.enterprise.crl": [
        "CRLChecker",
    ],

    # ── Persistence / infra modules (imported but no specific symbols) ──
    "maop.enterprise.pg_persist": [
        "PgRBACStore",
        "PgTenantStore",
        "PgAuditStore",
    ],
    "maop.enterprise.container": [],
    "maop.enterprise.tls_auto": [],
}


# ── Helpers ────────────────────────────────────────────────────────

def _enterprise_available() -> bool:
    """Check if ``maop.enterprise`` is importable.

    Performs just-in-time sys.path reconciliation: ensures the MAOS repo
    root is at ``sys.path[0]`` and purges any stale ``maop`` module loaded
    as a plain package (single ``__path__`` entry) so the namespace package
    is picked up on the next import.
    """
    # ── Just-in-time path reconciliation ───────────────────────────
    # pytest may insert the MAOP rootdir at sys.path[0] *after* this
    # module's top-level code runs.  Re-assert MAOS at position 0 here.
    if sys.path[0] != _MAOS_PATH:
        if _MAOS_PATH in sys.path:
            sys.path.remove(_MAOS_PATH)
        sys.path.insert(0, _MAOS_PATH)

    # If maop is loaded as a plain package (single __path__ entry), purge
    # it so the namespace package (extend_path) is used on next import.
    _existing = sys.modules.get("maop")
    if _existing is not None and len(getattr(_existing, "__path__", [])) < 2:
        for _key in list(sys.modules):
            if _key == "maop" or _key.startswith("maop."):
                sys.modules.pop(_key, None)
        importlib.invalidate_caches()

    try:
        importlib.import_module("maop.enterprise")
        return True
    except ImportError:
        return False


# ── Auto-skip fixture ──────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def require_enterprise():
    """Skip all tests in this module if enterprise is not installed.

    In personal edition (maop-enterprise not installed), the contract
    tests are skipped rather than failed — the contract only applies
    when both packages are present.
    """
    if not _enterprise_available():
        pytest.skip(
            "maop-enterprise not installed — contract tests skipped "
            "(personal edition)"
        )


# ── Tests ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("module_name", list(ENTERPRISE_CONTRACT.keys()))
def test_module_importable(module_name):
    """Each contracted module must be importable.

    A failure here means MAOS removed or renamed a module that MAOP
    still references — a breaking API change.
    """
    importlib.import_module(module_name)


@pytest.mark.parametrize(
    "module_name,symbols",
    [(k, v) for k, v in ENTERPRISE_CONTRACT.items() if v],
)
def test_symbols_exist(module_name, symbols):
    """Each contracted symbol must exist in its module.

    A failure here means MAOS removed or renamed a symbol that MAOP
    still imports — a breaking API change.
    """
    mod = importlib.import_module(module_name)
    for symbol in symbols:
        assert hasattr(mod, symbol), (
            f"{module_name}.{symbol} not found — API drift detected! "
            f"MAOP references this symbol but MAOS no longer provides it."
        )