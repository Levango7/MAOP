"""Locate a usable MAOP home and bootstrap install assets.

Why this exists
---------------
``maop`` can be run from three very different layouts:

* a source checkout (``<repo>/py/maop`` with ``<repo>/config``),
* an installed wheel (``site-packages/maop`` with no repo assets),
* a container (``MAOP_ROOT_DIR=/app``).

Historically the CLI derived the root from ``__file__``, which silently
pointed at ``site-packages`` after ``pip install``: ``maop start`` then
found neither ``config/agents.yaml`` nor the dashboard bundle.  This
module gives every entry point one resolver and one ``maop init``
implementation so a wheel install becomes usable without a git checkout.
"""

from __future__ import annotations

import filecmp
import json
import os
import shutil
import warnings
from pathlib import Path
from typing import TypedDict

ROOT_DIR_VAR = "MAOP_ROOT_DIR"
ROOT_LEGACY_VAR = "MAOP_ROOT"
DEFAULT_HOME_DIRNAME = ".maop"

CONFIG_FILES = (
    "agents.yaml",
    "models.yaml",
    "mcp_servers.yaml",
    "rules.yaml",
    "tool_whitelist.yaml",
)


class InitResult(TypedDict):
    home: Path
    config_copied: list[str]
    dashboard_copied: int
    dashboard_available: bool


def default_home() -> Path:
    """Return the default writable home (``~/.maop`` or ``$MAOP_HOME``)."""
    explicit = os.environ.get("MAOP_HOME", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (Path.home() / DEFAULT_HOME_DIRNAME).resolve()


def _is_repo_root(path: Path) -> bool:
    return (path / "config" / "agents.yaml").is_file() and (
        path / "py" / "maop" / "__init__.py"
    ).is_file()


def _walk_up(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if _is_repo_root(candidate):
            return candidate
    return None


def resolve_maop_root(start: Path | None = None) -> Path:
    """Resolve the runtime root without assuming the package is in a repo.

    Priority: ``MAOP_ROOT_DIR`` (canonical) > legacy ``MAOP_ROOT`` > source
    checkout found from the working directory or package location > the
    writable home (``~/.maop``).  The returned path is not created here.
    """
    for var in (ROOT_DIR_VAR, ROOT_LEGACY_VAR):
        value = os.environ.get(var, "").strip()
        if value:
            if var == ROOT_LEGACY_VAR:
                warnings.warn(
                    "环境变量 MAOP_ROOT 已弃用，请改用 MAOP_ROOT_DIR。",
                    DeprecationWarning,
                    stacklevel=2,
                )
            return Path(value).expanduser().resolve()

    checkout = _walk_up((start or Path.cwd()).resolve())
    if checkout is not None:
        return checkout

    checkout = _walk_up(Path(__file__).resolve())
    if checkout is not None:
        return checkout

    return default_home()


def package_assets_dir() -> Path:
    """Directory force-included into the wheel by hatch."""
    return Path(__file__).resolve().parent.parent / "_assets"


def bundled_config_dir() -> Path | None:
    """Default config shipped with the wheel, else the source checkout."""
    packaged = package_assets_dir() / "config"
    if (packaged / "agents.yaml").is_file():
        return packaged
    checkout = _walk_up(Path(__file__).resolve())
    if checkout is not None and (checkout / "config" / "agents.yaml").is_file():
        return checkout / "config"
    return None


def bundled_dashboard_dir() -> Path | None:
    """Prebuilt Vue bundle shipped with the wheel, else the source checkout."""
    packaged = package_assets_dir() / "dashboard" / "dist-enterprise"
    if (packaged / "index.html").is_file():
        return packaged
    checkout = _walk_up(Path(__file__).resolve())
    if checkout is not None:
        built = checkout / "dashboard" / "dist-enterprise"
        if (built / "index.html").is_file():
            return built
    return None


def _copy_missing(source: Path, target: Path, force: bool) -> list[str]:
    target.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for item in source.rglob("*"):
        if not item.is_file():
            continue
        relative = item.relative_to(source)
        destination = target / relative
        if destination.exists() and not force:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, destination)
        copied.append(str(relative).replace("\\", "/"))
    return copied


def _sync_tree(source: Path, target: Path) -> list[str]:
    """Copy files whose content differs (generated assets, not user edits)."""
    target.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for item in source.rglob("*"):
        if not item.is_file():
            continue
        relative = item.relative_to(source)
        destination = target / relative
        if destination.exists() and filecmp.cmp(item, destination, shallow=False):
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, destination)
        copied.append(str(relative).replace("\\", "/"))
    return copied


def _package_version() -> str:
    from maop import __version__

    return str(__version__)


def _marker_path(home: Path) -> Path:
    return home / ".maop-home.json"


def _write_marker(home: Path, config_source: Path, dashboard_source: Path | None) -> None:
    _marker_path(home).write_text(
        json.dumps(
            {
                "home": str(home),
                "config_source": str(config_source),
                "dashboard_source": str(dashboard_source) if dashboard_source else "",
                "assets_version": _package_version(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _refresh_home_assets(home: Path) -> None:
    """Refresh generated UI assets when the installed version changed.

    Config stays user-owned (missing files only); the dashboard is build
    output and is replaced so ``pip install -U`` followed by a restart
    serves the new bundle instead of a stale index.html. Only homes with
    a marker are touched, so source checkouts are never modified.
    """
    marker = _marker_path(home)
    if not marker.is_file():
        return
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict) or data.get("assets_version") == _package_version():
        return
    try:
        init_home(home)
    except OSError:
        return


def init_home(
    target: str | Path | None = None,
    *,
    force: bool = False,
    include_dashboard: bool = True,
) -> InitResult:
    """Create a writable home containing config and (optionally) the UI bundle.

    Existing files are preserved unless ``force`` is true, so re-running
    ``maop init`` never destroys local edits.
    """
    home = Path(target).expanduser().resolve() if target else default_home()
    config_source = bundled_config_dir()
    if config_source is None:
        raise FileNotFoundError(
            "找不到默认 config/agents.yaml。请从源码运行，或重新安装包含资产的 wheel。"
        )

    (home / "data").mkdir(parents=True, exist_ok=True)
    (home / "logs").mkdir(parents=True, exist_ok=True)
    copied_config = _copy_missing(config_source, home / "config", force)

    copied_dashboard: list[str] = []
    dashboard_source = bundled_dashboard_dir() if include_dashboard else None
    if dashboard_source is not None:
        # The UI is generated build output, not user data: always refresh it
        # so a version upgrade does not keep serving the old index.html.
        copied_dashboard = _sync_tree(
            dashboard_source, home / "dashboard" / "dist-enterprise"
        )

    _write_marker(home, config_source, dashboard_source)
    return {
        "home": home,
        "config_copied": copied_config,
        "dashboard_copied": len(copied_dashboard),
        "dashboard_available": dashboard_source is not None,
    }


def ensure_runtime_root() -> Path:
    """Return a root that has ``config/agents.yaml``, bootstrapping if needed.

    A source checkout is used as-is.  An installed wheel auto-initializes
    ``~/.maop`` so ``maop start`` works on first run without a git clone;
    an already-initialized home is refreshed when the wheel version changed.
    """
    root = resolve_maop_root()
    if (root / "config" / "agents.yaml").is_file():
        _refresh_home_assets(root)
        return root

    home = default_home()
    if not (home / "config" / "agents.yaml").is_file():
        init_home(home)
    else:
        _refresh_home_assets(home)
    return home


def dashboard_dir(root: Path | None = None) -> Path | None:
    """Resolve the static UI directory for *root*, including packaged assets."""
    runtime_root = root or resolve_maop_root()
    for candidate in (
        runtime_root / "dashboard" / "dist-enterprise",
        package_assets_dir() / "dashboard" / "dist-enterprise",
        runtime_root / "dashboard-enterprise",
    ):
        if (candidate / "index.html").is_file():
            return candidate
    return None
