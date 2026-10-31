"""Ratchet: enterprise endpoints called by the dashboard must declare a response_model.

This is the other half of ``test_frontend_response_models.py``. That gate runs
against the personal app, so the seven enterprise-only routers — which exist in
this repository but are mounted only under ``FeatureFlag.MULTI_USER`` — were
never intersected: 31 frontend-called enterprise endpoints returned an untyped
dict with no line anywhere, and the personal gate reported a clean burn-down.

Measured, not estimated: the enterprise app exposed 74 METHOD/PATH pairs, 32 of
them referenced by the dashboard, 0 typed. One is exempted as non-JSON (SAML SP
metadata comes back as ``application/xml``). 17 of the 31 were modelled on
2026-10-30 (tenant / rbac / quotas) and the last 14 on 2026-10-31 (sso / n8n /
licenses, whose models are mirrored in-repo so they are typed even where the
commercial overlay is absent). The allowlist is now empty and must stay that way.

Three routers cannot be imported at all in a clean CI checkout: they pull the
commercial overlay in at module level, and that overlay ships in a second
repository (ADR-017). Mounting them — the first version of this gate — passed on
a development machine and failed on every runner. Their surface is now read from
source, and the two views are asserted to agree for every router this
environment can import, so the scanner cannot quietly disagree with FastAPI.

This file must stay free of the overlay's import path: the collection guard in
``tests/conftest.py`` drops every ``test_*.py`` whose text mentions it, which
would switch this gate off in exactly the environment that needs it. The name
lives in ``dashboard_app_profiles.OVERLAY_IMPORT``, and
``test_the_gate_file_stays_collectible`` below is what keeps it that way.
"""

from __future__ import annotations

from pathlib import Path

from tests._overlay_rule import needs_overlay
from tests.dashboard_app_profiles import (
    ENTERPRISE_ROUTER_MODULES,
    OVERLAY_IMPORT,
    OVERLAY_ROUTER_MODULES,
    ROUTER_PREFIXES,
    enterprise_app,
    enterprise_route_map,
    overlay_probe,
    route_map,
    static_route_map,
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
# 2026-10-30: 31 → 14 (tenant / rbac / quotas); 2026-10-31: 14 → 0.
RATCHET_CAP = 0


def _routes() -> dict[tuple[str, str], dict]:
    return enterprise_route_map(normalize_path)


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
    """Anti-vacuous: a router that contributed nothing would shrink the audit."""
    routes = _routes()
    paths = {path for (_method, path) in routes}
    assert len(routes) >= 70, len(routes)  # METHOD/PATH pairs
    assert len(paths) >= 60, len(paths)
    for module in ENTERPRISE_ROUTER_MODULES:
        prefix = ROUTER_PREFIXES[module]
        assert any(path.startswith(prefix) for path in paths), f"{module} audited nothing"


def test_the_source_view_matches_the_imported_view() -> None:
    """The scanner may not quietly disagree with what FastAPI actually mounts.

    Every router importable here is checked both ways: same route keys, same
    typed/untyped verdict. On a machine with the commercial overlay that covers
    all seven routers; in a clean CI checkout it covers the four that import,
    and the remaining three use the very same scanner.
    """
    imported = route_map(enterprise_app(), normalize_path)
    checked = 0
    for module, prefix in ROUTER_PREFIXES.items():
        subset = {key: op for key, op in imported.items() if key[1].startswith(prefix)}
        if not subset:
            continue  # overlay absent here: only the source view exists
        source = static_route_map(module, normalize_path)
        assert set(source) == set(subset), (module, sorted(set(source) ^ set(subset))[:5])
        for key, op in subset.items():
            assert has_typed_response(source[key]) == has_typed_response(op), (module, key)
        checked += 1
    assert checked >= 4, checked


def test_only_three_routers_need_the_commercial_overlay() -> None:
    """The overlay split is a measured fact, not a convenient excuse.

    If a router drops the module-level import (or gains one), this goes red and
    the audit surface gets re-decided instead of silently narrowing.
    """
    result = overlay_probe(ENTERPRISE_ROUTER_MODULES)
    for module in OVERLAY_ROUTER_MODULES:
        assert str(result[module]).startswith(OVERLAY_IMPORT), (module, result[module])
    for module in set(ENTERPRISE_ROUTER_MODULES) - set(OVERLAY_ROUTER_MODULES):
        assert result[module] == "ok", (module, result[module])


def test_the_gate_file_stays_collectible() -> None:
    """A module-level overlay import here would disable the gate on CI, silently.

    ``tests/conftest.py`` drops any ``test_*.py`` that cannot be imported without
    ``maop.enterprise`` — which is every CI checkout. Green-but-never-run is the
    failure mode this guard exists to catch. Mentioning the overlay in prose is
    fine (the rule is structural, see ``tests/_overlay_rule.py``); importing it
    at module level is not.
    """
    assert not needs_overlay(Path(__file__).read_text(encoding="utf-8")), (
        "this gate would stop being collected in a clean CI"
    )
    assert OVERLAY_ROUTER_MODULES, "the split this file depends on moved"


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
    from tests.dashboard_app_profiles import personal_app

    shared = set(route_map(personal_app(), normalize_path)) & set(_routes())
    assert shared == set(), sorted(shared)[:5]

    personal_paths = {path for (_m, path) in load_entries(PERSONAL_ALLOWLIST)}
    enterprise_paths = {path for (_m, path) in load_entries(ALLOWLIST)}
    overlap = sorted(personal_paths & enterprise_paths)
    assert overlap == [], overlap
