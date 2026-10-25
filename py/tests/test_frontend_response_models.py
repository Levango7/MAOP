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

Seeing the calls is the part that has to be right, so extraction is not one
regex: ``api.get('/api/x')`` is easy, ``const url = '/api/x' + qs`` followed
by ``api.get(url)`` — and ``fetch(API_ENDPOINTS.AUTH_REFRESH, ...)`` — are
not, and for a long time were invisible to the gate (a blind spot worth 15
paths, including three auth endpoints that had been shipped through two
"model every frontend endpoint" batches without ever being modeled).

Three rules, in order, plus a completeness guard:

  1. the first argument is a string literal  → that path, that method;
  2. the first argument is an expression → the literals inside it, plus what
     its leading reference can hold: a local variable's assignment
     (``const url = '/api/x' + qs``), a constant map's value
     (``API_ENDPOINTS.AUTH_REFRESH``, ``endpoints[key] || '/api/health'``), or
     the arguments a same-file helper is called with
     (``const get = async (url, sink) => api.get(url)``);
  3. ``fetch``/``fetchWithTimeout``/``EventSource`` calls take their verb from
     a ``method: 'VERB'`` inside the same call, defaulting to GET;
  4. guard: every ``/api/`` literal in the frontend source must be attributed
     by rules 1-3 or listed in ``fixtures/frontend_url_reference_exceptions
     .txt`` with a reason. A URL the gate cannot place fails the build
     instead of silently shrinking coverage.

Rules 1-3 stay inside one file on purpose: the one call shape that crosses a
file boundary (Chat.vue's SSE helper) is listed in that fixture rather than
resolved by guessing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from maop.dashboard.server import app

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_SRC = REPO_ROOT / "dashboard-enterprise" / "src"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
ALLOWLIST = FIXTURES / "frontend_response_model_allowlist.txt"
NON_JSON = FIXTURES / "frontend_non_json_endpoints.txt"
URL_EXCEPTIONS = FIXTURES / "frontend_url_reference_exceptions.txt"

# Allowlist ceiling. The ratchet only shrinks, so this number moving up is a
# decision, taken in the open: 2026-10-24, 75 -> 82. The extractor learned to
# follow computed URLs and found seven endpoints two earlier "model every
# frontend endpoint" batches had walked past (three auth, two edition, graph
# stats, knowledge-graph). They are burned down next.
RATCHET_CAP = 75

_QUOTES = {"`": "`", "'": "'", '"': '"'}
_CALL_HEAD_RE = re.compile(
    r"\b(?:api\.(get|post|put|patch|delete)|fetch|fetchWithTimeout|EventSource)\s*\("
)
_METHOD_RE = re.compile(r"""method\s*:\s*['"](GET|POST|PUT|PATCH|DELETE)['"]""", re.IGNORECASE)
_INTERP_RE = re.compile(r"\$\{(?:[^{}]|\{[^{}]*\})*\}")
_PLACEHOLDER_RE = re.compile(r"\{[^}]*\}")
_REF_RE = re.compile(
    r"^(?P<base>[A-Za-z_$][\w$]*)"
    r"(?:\s*\.\s*(?P<dot>[A-Za-z_$][\w$]*)"
    r"|\s*\[\s*(?:(?P<q>['\"])(?P<brack>[^'\"]*)(?P=q)\s*\]|(?P<dynamic>[^\]]*\]))?)?",
)
_PARAM_RE = re.compile(r"^\s*([A-Za-z_$][\w$]*)\s*(?:=[^,)]*)?$")
_METHODS = ("get", "post", "put", "patch", "delete")


def normalize_path(raw: str) -> str:
    """Make frontend literals and FastAPI templates comparable.

    ``${...}`` directly after a ``/`` is a path parameter and becomes ``{}``;
    anywhere else it is an interpolated query string or suffix and is dropped
    (``/api/worktree/list${qs ? '?' + qs : ''}`` is ``/api/worktree/list``).
    """
    out: list[str] = []
    pos = 0
    while pos < len(raw):
        match = _INTERP_RE.match(raw, pos)
        if match:
            if out and out[-1] == "/":
                out.append("{}")
            pos = match.end()
            continue
        out.append(raw[pos])
        pos += 1
    path = "".join(out).split("?")[0].rstrip("/")
    path = _PLACEHOLDER_RE.sub("{}", path)
    return path or "/"


def _read_string_literal(text: str, at: int) -> tuple[str, int] | None:
    """Read the string literal starting at ``text[at]`` (a quote character).

    Returns ``(content, index_of_closing_quote)`` or ``None`` when ``at`` does
    not open a literal. Inner quotes of the other kinds are part of the
    content, which is what makes `` `/api/x${q ? '?' + q : ''}` `` readable.
    """
    quote = text[at]
    if quote not in _QUOTES:
        return None
    closing = _QUOTES[quote]
    chunks: list[str] = []
    pos = at + 1
    while pos < len(text):
        char = text[pos]
        if char == "\\":
            chunks.append(text[pos:pos + 2])
            pos += 2
            continue
        if char == "\n":
            break
        if quote == "`" and char == "$" and pos + 1 < len(text) and text[pos + 1] == "{":
            match = _INTERP_RE.match(text, pos)
            if match:
                chunks.append(match.group(0))
                pos = match.end()
                continue
        if char == closing:
            return "".join(chunks), pos
        chunks.append(char)
        pos += 1
    return None


def _skip_space(text: str, pos: int) -> int:
    while pos < len(text) and text[pos] in " \t\r\n":
        pos += 1
    return pos


def _arg_spans(text: str, open_paren: int) -> list[tuple[int, int]]:
    """Top-level argument spans of the call whose ``(`` is at ``open_paren``."""
    depth = 1
    spans: list[tuple[int, int]] = []
    start = open_paren + 1
    pos = start
    while pos < len(text):
        char = text[pos]
        if char in _QUOTES:
            literal = _read_string_literal(text, pos)
            if literal is None:
                break
            pos = literal[1] + 1
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                spans.append((start, pos))
                return spans
        elif char == "," and depth == 1:
            spans.append((start, pos))
            start = pos + 1
        pos += 1
    spans.append((start, len(text)))
    return spans


def _iter_url_literals(text: str) -> list[tuple[str, int]]:
    """Every ``/api/...`` string literal in ``text``, with its position."""
    found: list[tuple[str, int]] = []
    pos = 0
    while pos < len(text):
        if text[pos] in _QUOTES:
            literal = _read_string_literal(text, pos)
            if literal is None:
                pos += 1
                continue
            content, closing = literal
            if content.startswith("/api/"):
                found.append((normalize_path(content), pos))
            pos = closing + 1
            continue
        pos += 1
    return found


def strip_comments(text: str) -> str:
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    text = re.sub(r"^\s*//.*$", " ", text, flags=re.MULTILINE)
    return text


def _rhs_end(text: str, pos: int) -> int:
    """End of the expression starting at ``pos``: line end or ``;`` at depth 0."""
    depth = 0
    while pos < len(text):
        char = text[pos]
        if char in _QUOTES:
            literal = _read_string_literal(text, pos)
            if literal is None:
                break
            pos = literal[1] + 1
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                return pos
            depth -= 1
        elif depth == 0 and char in ";\n":
            return pos
        pos += 1
    return pos


def _paths_in(text: str, start: int, end: int) -> set[str]:
    return {path for path, pos in _iter_url_literals(text) if start <= pos < end}


def _assignment_spans(text: str, name: str) -> list[tuple[int, int]]:
    """RHS spans of every ``const/let/var name = …`` and ``name = …``."""
    spans: list[tuple[int, int]] = []
    for pattern in (
        rf"(?:const|let|var)\s+{re.escape(name)}\s*=\s*",
        rf"(?:^|[;{{\n])\s*{re.escape(name)}\s*=\s*(?!=)",
    ):
        for match in re.finditer(pattern, text):
            spans.append((match.end(), _rhs_end(text, match.end())))
    return spans


def _object_value_paths(text: str, base: str, key: str | None) -> set[str]:
    """Values of one property — or of every property — of an object literal."""
    paths: set[str] = set()
    for start, end in _assignment_spans(text, base):
        body = text[start:end]
        if key is None:
            paths |= _paths_in(text, start, end)
            continue
        for match in re.finditer(
            rf"""['\"]?{re.escape(key)}['\"]?\s*:\s*""", body,
        ):
            literal = _read_string_literal(body, _skip_space(body, match.end()))
            if literal and literal[0].startswith("/api/"):
                paths.add(normalize_path(literal[0]))
    return paths


def _function_defs(text: str) -> list[tuple[str, list[str]]]:
    """``(name, parameters)`` of every function declared in this file."""
    defs: list[tuple[str, list[str]]] = []
    pattern = re.compile(
        r"(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*(?:=>|\{)"
        r"|(?:function\s+(\w+)\s*\(([^)]*)\))"
    )
    for match in pattern.finditer(text):
        name = match.group(1) or match.group(3)
        raw_params = match.group(2) if match.group(1) else match.group(4)
        params: list[str] = []
        for part in (raw_params or "").split(","):
            param = _PARAM_RE.match(part)
            if param:
                params.append(param.group(1))
        defs.append((name, params))
    return defs


def _call_site_paths(text: str, fname: str, index: int) -> set[str]:
    """Literals passed at argument position ``index`` of ``fname(...)`` calls."""
    paths: set[str] = set()
    for match in re.finditer(rf"\b{re.escape(fname)}\s*\(", text):
        args = _arg_spans(text, match.end() - 1)
        if len(args) > index:
            start, end = args[index]
            paths |= _paths_in(text, start, end)
    return paths


def _resolve_arg(text: str, start: int, end: int, defs: list[tuple[str, list[str]]]) -> set[str]:
    """Rule 2 — the paths an indirect first argument can hold.

    Four shapes, all of them real in this dashboard: a literal folded into the
    expression (``endpoints[key] || '/api/health'``), a local variable
    (``const url = '/api/x' + qs``), a member of a constant map
    (``API_ENDPOINTS.AUTH_REFRESH``, ``endpoints[m.titleKey ? 'A' : 'B']``),
    and a parameter of a helper defined in the same file
    (``const get = async (url, sink, keys) => api.get(url)``).
    """
    arg = text[start:end].strip()
    if not arg or arg[0] in _QUOTES:
        return set()
    paths = _paths_in(text, start, end)
    ref = _REF_RE.match(arg)
    if not ref:
        return paths
    base = ref.group("base")
    if ref.group("dot") or ref.group("brack") or ref.group("dynamic"):
        key = ref.group("dot") or ref.group("brack")
        return paths | _object_value_paths(text, base, key)
    assigned: set[str] = set()
    for lhs, rhs in _assignment_spans(text, base):
        assigned |= _paths_in(text, lhs, rhs)
    if assigned:
        return paths | assigned
    for fname, params in defs:
        if base in params:
            paths |= _call_site_paths(text, fname, params.index(base))
    return paths


def scan_source_dir(root: Path) -> tuple[set[tuple[str, str]], dict[str, set[str]]]:
    """Return (attributed calls, unattributed literals per file).

    ``unattributed`` maps a file name to the ``/api/`` paths it references
    that no call rule could place — the input the completeness guard judges.
    """
    calls: set[tuple[str, str]] = set()
    unattributed: dict[str, set[str]] = {}
    if not root.is_dir():
        return calls, unattributed
    for file in sorted(root.rglob("*")):
        if file.suffix not in (".vue", ".js") or "__tests__" in file.parts:
            continue
        text = strip_comments(file.read_text(encoding="utf-8", errors="replace"))
        file_paths = {path for path, _ in _iter_url_literals(text)}
        defs = _function_defs(text)
        placed: set[str] = set()
        for match in _CALL_HEAD_RE.finditer(text):
            args = _arg_spans(text, match.end() - 1)
            if not args:
                continue
            verb = (match.group(1) or "").upper()
            if not verb:
                # fetch-family: the verb lives in the options argument.
                tail = text[args[0][1]:args[-1][1]] if len(args) > 1 else ""
                found = _METHOD_RE.search(tail)
                verb = found.group(1).upper() if found else "GET"
            start = _skip_space(text, args[0][0])
            literal = _read_string_literal(text, start) if text[start:start + 1] in _QUOTES else None
            if literal is not None:
                if not literal[0].startswith("/api/"):
                    continue
                paths = {normalize_path(literal[0])}
            else:
                paths = _resolve_arg(text, args[0][0], args[0][1], defs)
            for path in paths:
                calls.add((verb, path))
            placed |= paths
        orphaned = file_paths - placed
        if orphaned:
            unattributed[file.name] = orphaned
    return calls, unattributed


def frontend_calls() -> set[tuple[str, str]]:
    return scan_source_dir(FRONTEND_SRC)[0]


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


def load_entries(path: Path) -> set[tuple[str, str]]:
    entries: set[tuple[str, str]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        method, rest = line.split(None, 1)
        entries.add((method.upper(), normalize_path(rest.split("#", 1)[0].strip())))
    return entries


def coverage() -> tuple[set[tuple[str, str]], set[tuple[str, str]], set[tuple[str, str]]]:
    """Return (missing, calls, matched) for frontend-called routes."""
    calls = frontend_calls()
    routes = route_schema()
    matched = {key: op for key, op in routes.items() if key in calls}
    exempt = load_entries(NON_JSON)
    missing = {
        key for key, op in matched.items()
        if not has_typed_response(op) and key not in exempt
    }
    return missing, calls, set(matched)


# ── the extractor itself ──────────────────────────────────────────


def test_path_normalization_handles_templates_and_queries():
    assert normalize_path("/api/agents/${id}/memory?limit=1") == "/api/agents/{}/memory"
    assert normalize_path("/api/notifications/channels/{channel_id}") == "/api/notifications/channels/{}"
    assert normalize_path("/api/notifications/list/") == "/api/notifications/list"
    # A suffix interpolation is not a path parameter.
    assert normalize_path("/api/worktree/list${qs ? '?' + qs : ''}") == "/api/worktree/list"
    assert normalize_path("/api/graph/neighbors?node=${enc(q)}") == "/api/graph/neighbors"


def test_resolver_sees_every_indirection_shape(tmp_path: Path):
    """Each rule gets its own source shape, so a regression in one is visible."""
    (tmp_path / "literal.js").write_text(
        "const a = await api.get('/api/literal');\n"
        "await api.post(`/api/literal/${id}/run`, body);\n", encoding="utf-8",
    )
    (tmp_path / "concat.js").write_text(
        "const qs = 'x=1';\n"
        "const url = '/api/concat' + (qs ? '?' + qs : '');\n"
        "await api.get(url);\n", encoding="utf-8",
    )
    (tmp_path / "multiline.js").write_text(
        "const d = await api.get(\n"
        "  `/api/multi?rule_id=${enc(id)}`,\n"
        ");\n", encoding="utf-8",
    )
    (tmp_path / "map.js").write_text(
        "async function run(m) {\n"
        "  const endpoints = { 'A': '/api/from-map', 'B': '/api/also-from-map' };\n"
        "  await api.post(endpoints[m.key], {});\n"
        "}\n", encoding="utf-8",
    )
    (tmp_path / "rawfetch.js").write_text(
        "const EP = { LOGIN: '/api/raw-login', LOGOUT: '/api/raw-logout' };\n"
        "await fetch(EP.LOGIN, { method: 'POST', body: '{}' });\n"
        "await fetchWithTimeout(EP.LOGOUT, { method: 'POST' });\n"
        "await fetch('/api/raw-get');\n", encoding="utf-8",
    )
    calls, unattributed = scan_source_dir(tmp_path)
    assert ("GET", "/api/literal") in calls
    assert ("POST", "/api/literal/{}/run") in calls
    assert ("GET", "/api/concat") in calls, sorted(calls)
    assert ("GET", "/api/multi") in calls, sorted(calls)
    assert ("POST", "/api/from-map") in calls, sorted(calls)
    assert ("POST", "/api/also-from-map") in calls, sorted(calls)
    assert ("POST", "/api/raw-login") in calls, sorted(calls)
    assert ("POST", "/api/raw-logout") in calls, sorted(calls)
    assert ("GET", "/api/raw-get") in calls, sorted(calls)
    assert unattributed == {}, unattributed


def test_unattributable_url_is_reported(tmp_path: Path):
    """The guard needs a real signal: a URL no call rule touches is reported."""
    (tmp_path / "constants.js").write_text(
        "export const DOC_PATH = '/api/from-a-constant';\n", encoding="utf-8",
    )
    calls, unattributed = scan_source_dir(tmp_path)
    assert calls == set()
    assert unattributed == {"constants.js": {"/api/from-a-constant"}}


def test_frontend_scan_is_not_vacuous():
    calls = frontend_calls()
    assert len(calls) >= 200, len(calls)
    assert ("GET", "/api/notifications/list") in calls
    assert ("GET", "/api/agents") in calls


def test_gate_sees_the_endpoints_it_previously_missed():
    """The paths the old single regex could not see, pinned by name.

    Without this, "the scan found them" is an unfalsifiable claim about the
    extractor — the whole point of rules 2 and 3.
    """
    calls = frontend_calls()
    for key in (
        ("POST", "/api/auth/login"),
        ("POST", "/api/auth/logout"),
        ("POST", "/api/auth/refresh"),
        ("GET", "/api/info/edition"),
        ("GET", "/api/graph/stats"),
        ("GET", "/api/knowledge-graph"),
        ("GET", "/api/billing/records"),
        ("GET", "/api/worktree/list"),
        ("GET", "/api/quotas/history"),
        ("POST", "/api/control/maintain"),
        ("GET", "/api/feedback/export"),
        ("GET", "/api/stream/agent/{}"),
        ("GET", "/api/stream/dag/{}"),
    ):
        assert key in calls, key


def test_referenced_but_unattributable_urls_are_listed():
    """The two literals rules 1-3 cannot place, and still must not hide."""
    exempt = load_entries(URL_EXCEPTIONS)
    assert ("POST", "/api/chat/stream") in exempt, exempt
    assert ("GET", "/api/prometheus") in exempt, exempt


def test_every_frontend_url_is_attributed_or_exempted():
    """No ``/api/`` literal may hide from the gate.

    A URL that the resolver cannot place has to be listed with a reason —
    that list is where SSE, downloads and other non-store transports live.
    """
    _calls, unattributed = scan_source_dir(FRONTEND_SRC)
    exempt_paths = {path for (_m, path) in load_entries(URL_EXCEPTIONS)}
    orphans = {
        (name, path)
        for name, paths in unattributed.items()
        for path in paths
        if path not in exempt_paths
    }
    assert orphans == set(), (
        f"unattributed /api/ literals in {sorted({n for n, _ in orphans})}: "
        "resolve the call shape in scan_source_dir or add METHOD PATH # reason "
        "to frontend_url_reference_exceptions.txt"
    )


def test_non_json_exemptions_are_real_routes() -> None:
    """An exemption that no longer names a route is a stale comment.

    Judged against both profiles: SSO lives only in the enterprise app, so a
    personal-only lookup would fail a legitimate exemption (and tempt someone
    to delete it).
    """
    from tests.dashboard_app_profiles import all_route_map

    exempt = load_entries(NON_JSON)
    routes = all_route_map(normalize_path)
    unknown = sorted(key for key in exempt if key not in routes)
    assert unknown == [], f"non-JSON exemptions with no mounted route: {unknown}"
    typed = sorted(
        key for key in exempt if key in routes and has_typed_response(routes[key])
    )
    assert typed == [], f"exempted endpoints that DO declare a model: {typed}"


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
    allowlist = load_entries(ALLOWLIST)

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
        f"allowlist entries that are no longer missing (remove these lines): {stale}"
    )
    # Burn-down ratchet: the allowlist may only shrink (see RATCHET_CAP).
    assert len(allowlist) <= RATCHET_CAP, len(allowlist)
