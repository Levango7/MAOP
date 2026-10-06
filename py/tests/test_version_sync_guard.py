"""版本同步守卫自证：四处版本站点必须一致，且守卫真的读到了后两处。

背景：``py/scripts/check_config_drift.py`` 的 ``version_drift()`` 此前只比
``__init__.py`` 与 ``pyproject.toml``，而 ``CHANGELOG.md`` 发布前 checklist 第 3 条
要求"版本号在 pyproject.toml / __init__.py / Dockerfile / package.json 同步"。
盲区意味着发版时只抬其中两处不会有任何检查变红 —— 装出来的包和跑起来的镜像
可以不是同一个版本。

本文件既验证真实仓库当前一致，也用合成树证明**每一个**站点单独漂移都会被抓到
（少了后一类用例，"扩了两处站点"的改动其实无法证明它在工作）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "py" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_config_drift as drift

REQUIRED_SITES = {
    "py/maop/__init__.py",
    "py/pyproject.toml",
    "py/Dockerfile",
    "dashboard-enterprise/package.json",
}


def _build_tree(root: Path, versions: dict[str, str | None]) -> Path:
    """按站点写一棵最小仓库树，返回 ``py/`` 目录。

    值为 ``None`` 表示该站点文件整体缺失（模拟"文件被改名/删掉"）。
    """
    for rel, value in versions.items():
        if value is None:
            continue
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if rel.endswith("package.json"):
            path.write_text(json.dumps({"name": "dash", "version": value}), encoding="utf-8")
        elif rel.endswith("Dockerfile"):
            path.write_text(f'FROM python:3.13\nLABEL version="{value}"\n', encoding="utf-8")
        elif rel.endswith("pyproject.toml"):
            path.write_text(f'[project]\nname = "maop"\nversion = "{value}"\n', encoding="utf-8")
        else:
            path.write_text(f'__version__ = "{value}"\n', encoding="utf-8")
    return root / "py"


def _all_same(version: str) -> dict[str, str | None]:
    return dict.fromkeys(REQUIRED_SITES, version)


class TestRealRepo:
    def test_four_sites_in_sync(self) -> None:
        assert drift.version_drift(REPO / "py") == ""

    def test_site_registry_covers_all_declared_sites(self) -> None:
        # 有人从清单里摘掉站点 → 这里必须红，否则盲区悄悄回来
        assert {rel for rel, _ in drift.VERSION_SITES} == REQUIRED_SITES

    def test_release_checklist_sites_are_all_guarded(self) -> None:
        """CHANGELOG 的发布 checklist 点名的站点，必须都在守卫清单里。

        文档宣称有四处要同步，而守卫只读两处 —— 这个差异正是本轮修的 bug。
        """
        text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8-sig")
        line = next(
            (ln for ln in text.splitlines() if "版本号在" in ln and "同步" in ln),
            "",
        )
        assert line, "发布 checklist 里找不到版本号同步条目，本用例的前提已变，请一并更新"
        for token in ("pyproject", "__init__", "Dockerfile", "package.json"):
            assert token in line, f"checklist 未点名 {token}，但守卫在读它（口径需对齐）"


class TestSyntheticTree:
    def test_all_equal_passes(self, tmp_path: Path) -> None:
        py_dir = _build_tree(tmp_path, _all_same("9.9.9"))
        assert drift.version_drift(py_dir) == ""

    @pytest.mark.parametrize("site", sorted(REQUIRED_SITES))
    def test_single_site_bump_is_caught(self, tmp_path: Path, site: str) -> None:
        versions = _all_same("9.9.9")
        versions[site] = "9.9.10"
        py_dir = _build_tree(tmp_path, versions)
        msg = drift.version_drift(py_dir)
        assert msg, f"只改 {site} 应当报漂移，实际返回空串（该站点没被读到）"
        assert site in msg
        assert "9.9.9" in msg and "9.9.10" in msg

    @pytest.mark.parametrize("site", sorted(REQUIRED_SITES))
    def test_missing_site_is_not_silently_green(self, tmp_path: Path, site: str) -> None:
        versions = _all_same("9.9.9")
        versions[site] = None
        py_dir = _build_tree(tmp_path, versions)
        msg = drift.version_drift(py_dir)
        assert site in msg and "无法解析" in msg

    def test_unparseable_package_json_is_not_silently_green(self, tmp_path: Path) -> None:
        py_dir = _build_tree(tmp_path, _all_same("9.9.9"))
        bad = tmp_path / "dashboard-enterprise" / "package.json"
        bad.write_text("{ not json", encoding="utf-8")
        msg = drift.version_drift(py_dir)
        assert "package.json" in msg and "无法解析" in msg
