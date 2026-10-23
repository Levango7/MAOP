"""Ratchet: endpoints called by the dashboard must declare a response_model.

Why this exists: 211 of the 220 frontend-called endpoints returned
``dict[str, Any]``, so the OpenAPI schema said "some object" for almost every
page. That is how the notification list shipped reading ``id/message/read``
while the API returned ``notification_id/body/read_at`` — nothing in the
schema or the mocks could disagree.

The allowlist under ``fixtures/`` is the burn-down list: remove a line when
the endpoint gets a model. The gate fails in both directions:

  * a frontend-called endpoint missing a model that is not allowlisted
    (new gap), and
  * an allowlisted entry that is no longer missing (stale line).

Scope: routes reachable in the personal app. Enterprise-only endpoints that
are not mounted here stay out of ``matched`` by construction.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from maop.dashboard.server import app

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_SRC = REPO_ROOT / "dashboard-enterprise" / "src"
ALLOWLIST = Path(__file__).resolve().parent / "fixtures" / "frontend_response_model_allowlist.txt"

# `api.get('/api/x')`, backtick templates, and double-quoted calls.
_CALL_RE = re.compile(r"api\.(get|post|put|delete)\(\s*[`'\"]([^`'\"\n]+)")
_TEMPLATE_RE = re.compile(r"\$\{[^}]+\}")
_PLACEHOLDER_RE = re.compile(r"\{[^}]*\}")
_METHODS = ("get", "post", "put", "patch", "delete")


def normalize_path(raw: str) -> str:
    """Make frontend literals and FastAPI templates comparable."""
    path = _TEMPLATE_RE.sub("{param}", raw)
    path = path.split("?")[0].rstrip("/")
    path = _PLACEHOLDER_RE.sub("{}", path)
    return path or "/"


def frontend_calls() -> set[tuple[str, str]]:
    calls: set[tuple[str, str]] = set()
    if not FRONTEND_SRC.is_dir():
        return calls
    for file in FRONTEND_SRC.rglob("*"):
        if file.suffix not in (".vue", ".js"):
            continue
        text = file.read_text(encoding="utf-8", errors="replace")
        for match in _CALL_RE.finditer(text):
            raw = match.group(2)
            if not raw.startswith("/api/"):
                continue
            calls.add((match.group(1).upper(), normalize_path(raw)))
    return calls


def route_schema() -> dict[tuple[str, str], dict]:
    schema = app.openapi()
    routes: dict[tuple[str, str], dict] = {}
    for path, methods in schema.get("paths", {}).items():
        for method, op in methods.items():
            if method not in _METHODS:
                continue
            routes[(method.upper(), normalize_path(path))] = op
    return routes


def has_typed_response(op: dict) -> bool:
    """True when a 2xx response declares a schema (component or inline)."""
    for code in ("200", "201", "202", "204"):
        resp = op.get("responses", {}).get(code)
        if not resp:
            continue
        if code == "204":
            return True
        schema = (resp.get("content") or {}).get("application/json", {}).get("schema") or {}
        if "$ref" in schema or "properties" in schema or "$ref" in (schema.get("items") or {}):
            return True
    return False


def coverage() -> tuple[set[tuple[str, str]], set[tuple[str, str]], set[tuple[str, str]]]:
    """Return (missing, calls, matched) for frontend-called routes."""
    calls = frontend_calls()
    routes = route_schema()
    matched = {key: op for key, op in routes.items() if key in calls}
    missing = {key for key, op in matched.items() if not has_typed_response(op)}
    return missing, calls, set(matched)


def load_allowlist() -> set[tuple[str, str]]:
    entries: set[tuple[str, str]] = set()
    for line in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        method, path = line.split(None, 1)
        entries.add((method.upper(), normalize_path(path)))
    return entries


def test_frontend_scan_is_not_vacuous():
    calls = frontend_calls()
    assert len(calls) >= 200, len(calls)
    assert ("GET", "/api/notifications/list") in calls
    assert ("GET", "/api/agents") in calls


def test_path_normalization_handles_templates_and_queries():
    assert normalize_path("/api/agents/${id}/memory?limit=1") == "/api/agents/{}/memory"
    assert normalize_path("/api/notifications/channels/{channel_id}") == "/api/notifications/channels/{}"
    assert normalize_path("/api/notifications/list/") == "/api/notifications/list"


def test_notification_router_is_fully_typed():
    # The router imports its models from maop-enterprise and is not mounted
    # in a personal-only install (CI); the generic ratchet still applies there.
    pytest.importorskip("maop.enterprise", reason="notifications router needs maop-enterprise")
    routes = route_schema()
    notification_routes = [key for key in routes if key[1].startswith("/api/notifications")]
    assert len(notification_routes) == 26, len(notification_routes)
    untyped = sorted(key for key in notification_routes if not has_typed_response(routes[key]))
    assert untyped == []


def test_agents_router_endpoints_in_use_are_typed():
    routes = route_schema()
    agents_calls = {key for key in frontend_calls() if key[1].startswith("/api/agents")}
    assert len(agents_calls) == 16, sorted(agents_calls)
    untyped = sorted(
        key for key in agents_calls
        if key in routes and not has_typed_response(routes[key])
    )
    assert untyped == []


def test_only_allowlisted_frontend_endpoints_lack_models():
    missing, calls, matched = coverage()
    allowlist = load_allowlist()

    # Anti-vacuous: the scan must really be intersecting route definitions.
    assert len(calls) >= 200, len(calls)
    assert len(matched) >= 200, len(matched)

    new_gaps = sorted(missing - allowlist)
    stale = sorted(allowlist - missing)

    assert new_gaps == [], (
        "frontend-called endpoints without response_model that are not "
        f"allowlisted: {new_gaps}. Add a model or the allowlist entry."
    )
    assert stale == [], (
        "allowlist entries that are no longer missing (remove these lines): "
        f"{stale}"
    )
    # Burn-down ratchet: the allowlist may only shrink. Raising this number is
    # an explicit decision, not something a feature PR should do silently.
    assert len(allowlist) <= 85, len(allowlist)
