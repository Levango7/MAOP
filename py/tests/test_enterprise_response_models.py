"""Ratchet: enterprise endpoints called by the dashboard must declare a response_model.

This is the other half of ``test_frontend_response_models.py``. That gate runs
against the personal app, so the seven enterprise-only routers — which exist in
this repository but are mounted only under ``FeatureFlag.MULTI_USER`` — were
never intersected: 31 frontend-called enterprise endpoints returned an untyped
dict with no line anywhere, and the personal gate reported a clean burn-down.

Measured, not estimated: the enterprise app exposes 74 routes, 32 of them
referenced by the dashboard, 0 of those typed. One is exempted as non-JSON
(SAML SP metadata comes back as ``application/xml``), leaving the 31 lines in
``fixtures/enterprise_response_model_allowlist.txt``.
"""

from __future__ import annotations

from pathlib import Path

from tests.dashboard_app_profiles import (
    ENTERPRISE_ROUTER_MODULES,
    enterprise_app,
    route_map,
)
from tests.test_frontend_response_models import (
    FRONTEND_SRC,
    has_typed_response,
    load_entries,
    normalize_path,
    scan_source_dir,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ALLOWLIST = FIXTURES / "enterprise_response_model_allowlist.txt"
NON_JSON = FIXTURES / "frontend_non_json_endpoints.txt"
PERSONAL_ALLOWLIST = FIXTURES / "frontend_response_model_allowlist.txt"

# Starting ceiling, measured 2026-10-24 — this list may only shrink.
RATCHET_CAP = 31


def _routes() -> dict[tuple[str, str], dict]:
    return route_map(enterprise_app(), normalize_path)


def coverage() -> tuple[set[tuple[str, str]], set[tuple[str, str]], set[tuple[str, str]]]:
    """Return (missing, calls, matched) for enterprise routes the UI calls."""
    calls = scan_source_dir(FRONTEND_SRC)[0]
    routes = _routes()
    matched = {key for key in routes if key in calls}
    exempt = load_entries(NON_JSON)
    missing = {
        key for key in matched
        if not has_typed_response(routes[key]) and key not in exempt
    }
    return missing, calls, matched


def test_every_enterprise_router_really_mounts() -> None:
    """Anti-vacuous: a router that failed to import would shrink the audit."""
    paths = list(enterprise_app().openapi().get("paths", {}))
    assert len(_routes()) >= 70, len(_routes())  # METHOD/PATH pairs
    assert len(paths) >= 60, len(paths)
    prefixes = {
        "tenant": "/api/tenant", "rbac": "/api/rbac", "sso": "/api/sso",
        "quotas": "/api/quotas", "n8n": "/api/n8n", "licenses": "/api/licenses",
        "compliance": "/api/compliance",
    }
    for module in ENTERPRISE_ROUTER_MODULES:
        prefix = prefixes[module]
        assert any(p.startswith(prefix) for p in paths), f"{module} mounted nothing"


def test_enterprise_scan_is_not_vacuous() -> None:
    _missing, calls, matched = coverage()
    assert len(calls) >= 200, len(calls)
    assert len(matched) >= 30, len(matched)


def test_only_allowlisted_enterprise_endpoints_lack_models() -> None:
    missing, _calls, _matched = coverage()
    allowlist = load_entries(ALLOWLIST)

    new_gaps = sorted(missing - allowlist)
    stale = sorted(allowlist - missing)

    assert new_gaps == [], (
        f"enterprise endpoints the dashboard calls without response_model: {new_gaps}"
    )
    assert stale == [], (
        f"allowlist entries that are no longer missing (remove these lines): {stale}"
    )
    assert len(allowlist) <= RATCHET_CAP, len(allowlist)


def test_the_two_profiles_are_disjoint() -> None:
    """Neither endpoint may be judged by both gates.

    If an enterprise route also mounted in the personal app, the two allowlists
    would have to carry the same line — and a fix landing in one list would keep
    the other gate demanding a model that already exists.
    """
    from tests.dashboard_app_profiles import personal_app, route_map

    shared = set(route_map(personal_app(), normalize_path)) & set(_routes())
    assert shared == set(), sorted(shared)[:5]

    personal_paths = {path for (_m, path) in load_entries(PERSONAL_ALLOWLIST)}
    enterprise_paths = {path for (_m, path) in load_entries(ALLOWLIST)}
    overlap = sorted(personal_paths & enterprise_paths)
    assert overlap == [], overlap
