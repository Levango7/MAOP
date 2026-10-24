"""Read endpoints listed as admin-only must actually call ``require_admin``.

``scripts/check_admin_coverage.py`` walks POST/PUT/DELETE/PATCH bodies only, so
a guarded *read* has never been anyone's job — that is how
``GET /api/tenant-blackboard/tenants`` returned every tenant's id and entry
counts to a read-only caller while its sibling ``DELETE`` was properly guarded.

The check runs on the endpoint's own source (via the router object), so
``functools.wraps`` under ``@handle_api_errors`` is unwrapped by ``inspect``
and the assertion is about the function that will run, not the decorator.
"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path

import pytest

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "admin_required_read_endpoints.txt"


def _entries() -> list[tuple[str, str, str, str]]:
    rows = []
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        head, _, reason = line.partition("#")
        parts = head.split()
        if len(parts) != 3:
            pytest.fail(f"malformed fixture line: {line!r} (want ROUTER_FILE METHOD PATH # reason)")
        rows.append((parts[0], parts[1].upper(), parts[2], reason.strip()))
    return rows


def _endpoint(router_file: str, method: str, path: str):
    module = importlib.import_module(f"maop.dashboard.routers.{Path(router_file).stem}")
    for route in module.router.routes:
        if getattr(route, "path", None) == path and method in (getattr(route, "methods", None) or set()):
            return route.endpoint
    pytest.fail(f"route {method} {path} not found in {router_file} — the fixture moved")


@pytest.mark.parametrize("router_file,method,path,reason", _entries())
def test_admin_required_reads_call_the_guard(router_file, method, path, reason) -> None:
    source = inspect.getsource(_endpoint(router_file, method, path))
    assert "require_admin(request)" in source, (
        f"{method} {path} returns {reason} but does not call require_admin:\n{source}"
    )


def test_the_fixture_itself_is_not_empty_or_stale() -> None:
    """A guard list that lost its entries would silently guard nothing."""
    rows = _entries()
    assert len(rows) >= 2, rows
    for router_file, method, path, _reason in rows:
        assert method == "GET", (router_file, method, path)
        assert path.startswith("/api/"), (router_file, method, path)
