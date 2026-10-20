"""First-run bootstrap: root resolution, `maop init`, and packaged assets."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from maop.config import home as home_mod
from maop.config.home import (
    dashboard_dir,
    ensure_runtime_root,
    init_home,
    resolve_maop_root,
)
from maop.config.settings import MAOPSettings


def _fake_checkout(root: Path) -> Path:
    (root / "config").mkdir(parents=True)
    (root / "config" / "agents.yaml").write_text("agents: {}\n", encoding="utf-8")
    (root / "py" / "maop").mkdir(parents=True)
    (root / "py" / "maop" / "__init__.py").write_text("", encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("MAOP_ROOT_DIR", "MAOP_ROOT", "MAOP_HOME"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def tmp_home(tmp_path: Path) -> Path:
    """A temp home already resolved.

    The bootstrap functions return ``.resolve()``d paths. pytest's tmp_path is
    *not* resolved, and on macOS it sits behind the ``/var`` → ``/private/var``
    symlink while Windows CI accounts resolve to an 8.3 short name
    (``RUNNER~1``), so an unresolved fixture value compares unequal on exactly
    the two platforms whose legs would then fail.
    """
    return (tmp_path / "home").resolve()


def test_explicit_root_dir_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "explicit"
    target.mkdir()
    monkeypatch.setenv("MAOP_ROOT_DIR", str(target))
    assert resolve_maop_root() == target.resolve()


def test_source_checkout_detected_from_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _fake_checkout(tmp_path / "repo")
    nested = repo / "py" / "maop"
    monkeypatch.chdir(nested)
    assert resolve_maop_root() == repo.resolve()


def test_uninitialized_install_falls_back_to_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(home_mod, "default_home", lambda: (tmp_path / "home").resolve())
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.chdir(tmp_path / "elsewhere")
    # The package location is inside this source checkout, so the resolver
    # still finds the repo; simulate a wheel by pointing the walk-up at a
    # directory that has no repo siblings.
    monkeypatch.setattr(home_mod, "_walk_up", lambda start: None)
    assert resolve_maop_root() == (tmp_path / "home").resolve()


def test_init_home_copies_config_and_dashboard_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "assets"
    (source / "config").mkdir(parents=True)
    (source / "config" / "agents.yaml").write_text("agents: {}\n", encoding="utf-8")
    (source / "dashboard" / "dist-enterprise").mkdir(parents=True)
    (source / "dashboard" / "dist-enterprise" / "index.html").write_text("ui", encoding="utf-8")
    monkeypatch.setattr(home_mod, "bundled_config_dir", lambda: source / "config")
    monkeypatch.setattr(
        home_mod, "bundled_dashboard_dir", lambda: source / "dashboard" / "dist-enterprise"
    )

    target = tmp_path / "home"
    first = init_home(target)
    assert (target / "config" / "agents.yaml").is_file()
    assert (target / "dashboard" / "dist-enterprise" / "index.html").is_file()
    assert (target / "data").is_dir()
    assert first["config_copied"] == ["agents.yaml"]

    # Local edits survive a second init; --force overwrites them.
    (target / "config" / "agents.yaml").write_text("local: true\n", encoding="utf-8")
    second = init_home(target)
    assert second["config_copied"] == []
    assert "local" in (target / "config" / "agents.yaml").read_text(encoding="utf-8")
    init_home(target, force=True)
    assert (target / "config" / "agents.yaml").read_text(encoding="utf-8") == "agents: {}\n"


def test_init_refreshes_dashboard_even_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "assets"
    (source / "config").mkdir(parents=True)
    (source / "config" / "agents.yaml").write_text("agents: {}\n", encoding="utf-8")
    dashboard = source / "dashboard" / "dist-enterprise"
    dashboard.mkdir(parents=True)
    (dashboard / "index.html").write_text("v1", encoding="utf-8")
    monkeypatch.setattr(home_mod, "bundled_config_dir", lambda: source / "config")
    monkeypatch.setattr(home_mod, "bundled_dashboard_dir", lambda: dashboard)

    target = tmp_path / "home"
    init_home(target)
    (dashboard / "index.html").write_text("v2", encoding="utf-8")
    init_home(target)
    assert (target / "dashboard" / "dist-enterprise" / "index.html").read_text(
        encoding="utf-8"
    ) == "v2"


def test_ensure_runtime_root_refreshes_stale_assets(
    tmp_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_home
    (home / "config").mkdir(parents=True)
    (home / "config" / "agents.yaml").write_text("agents: {}\n", encoding="utf-8")
    (home / "dashboard" / "dist-enterprise").mkdir(parents=True)
    (home / "dashboard" / "dist-enterprise" / "index.html").write_text(
        "old", encoding="utf-8"
    )
    (home / ".maop-home.json").write_text(
        '{"home": "x", "assets_version": "0.0.1"}\n', encoding="utf-8"
    )
    source = tmp_path / "assets"
    (source / "config").mkdir(parents=True)
    (source / "config" / "agents.yaml").write_text("agents: {}\n", encoding="utf-8")
    dashboard = source / "dashboard" / "dist-enterprise"
    dashboard.mkdir(parents=True)
    (dashboard / "index.html").write_text("new", encoding="utf-8")
    monkeypatch.setattr(home_mod, "default_home", lambda: home)
    monkeypatch.setattr(home_mod, "resolve_maop_root", lambda: home)
    monkeypatch.setattr(home_mod, "bundled_config_dir", lambda: source / "config")
    monkeypatch.setattr(home_mod, "bundled_dashboard_dir", lambda: dashboard)

    assert ensure_runtime_root() == home
    assert (home / "dashboard" / "dist-enterprise" / "index.html").read_text(
        encoding="utf-8"
    ) == "new"
    marker = json.loads((home / ".maop-home.json").read_text(encoding="utf-8"))
    assert marker["assets_version"] == home_mod._package_version()


def test_returned_root_is_normalized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The resolver hands back normalized paths, on every platform.

    The CI red this pins: a fixture compared an unresolved ``tmp_path`` child
    against a resolved return value. Linux passed, macOS (``/var`` symlink) and
    Windows CI (``RUNNER~1``) both failed. ``..`` reproduces the same
    unresolved-vs-resolved mismatch portably.
    """
    (tmp_path / "home").mkdir()
    indirect = tmp_path / "somewhere" / ".." / "home"
    monkeypatch.setenv("MAOP_ROOT_DIR", str(indirect))
    assert resolve_maop_root() == (tmp_path / "home").resolve()


def test_ensure_runtime_root_bootstraps_installed_home(
    tmp_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "assets"
    (source / "config").mkdir(parents=True)
    (source / "config" / "agents.yaml").write_text("agents: {}\n", encoding="utf-8")
    home = tmp_home
    monkeypatch.setattr(home_mod, "default_home", lambda: home)
    monkeypatch.setattr(home_mod, "resolve_maop_root", lambda: home)
    monkeypatch.setattr(home_mod, "bundled_config_dir", lambda: source / "config")
    monkeypatch.setattr(home_mod, "bundled_dashboard_dir", lambda: None)

    root = ensure_runtime_root()
    assert root == home
    assert (root / "config" / "agents.yaml").is_file()


def test_settings_data_dir_follows_explicit_root(tmp_path: Path) -> None:
    settings = MAOPSettings(root_dir=str(tmp_path))
    assert settings.resolved_data_dir() == (tmp_path / "data").resolve()
    assert settings.resolved_db_path() == (tmp_path / "data" / "maop.db").resolve()


def test_dashboard_dir_prefers_root_then_packaged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "root"
    packaged = tmp_path / "pkg"
    (packaged / "dashboard" / "dist-enterprise").mkdir(parents=True)
    (packaged / "dashboard" / "dist-enterprise" / "index.html").write_text("ui", encoding="utf-8")
    monkeypatch.setattr(home_mod, "package_assets_dir", lambda: packaged)
    assert dashboard_dir(root) == packaged / "dashboard" / "dist-enterprise"

    (root / "dashboard" / "dist-enterprise").mkdir(parents=True)
    (root / "dashboard" / "dist-enterprise" / "index.html").write_text("ui", encoding="utf-8")
    assert dashboard_dir(root) == root / "dashboard" / "dist-enterprise"
