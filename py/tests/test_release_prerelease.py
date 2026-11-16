"""发布灰度通道守卫（2026-11-16）。

两条被测事实：
1. rc/a/b tag 创建 GitHub Release 时必须带 ``--prerelease`` —— 否则灰度版本
   会以正式版姿态出现在 Release 页（PyPI 侧由 PEP 440 自动标记，GitHub 侧不会）。
2. 已存在的 release 走 ``gh release upload --clobber``，不带 ``--prerelease``
   （gh upload 不支持该参数），且不应试图事后改标记。
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "py" / "scripts"


@pytest.fixture(scope="module")
def attach():
    spec = importlib.util.spec_from_file_location(
        "attach_release_assets", SCRIPTS / "attach_release_assets.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _files(tmp_path):
    whl = tmp_path / "maop-5.8.0rc1-py3-none-any.whl"
    whl.write_bytes(b"x")
    return [whl]


@pytest.mark.parametrize("tag", ["v5.8.0rc1", "v5.8.0a1", "v5.8.0b2", "V5.8.0RC1"])
def test_prerelease_tags_recognized(attach, tag):
    assert attach.is_prerelease_tag(tag) is True


@pytest.mark.parametrize("tag", ["v5.8.0", "v5.8.0.dev1", "v5.8.0.post1", "5.8.0rc1", "v5.8"])
def test_stable_and_other_tags_not_prerelease(attach, tag):
    assert attach.is_prerelease_tag(tag) is False


def test_rc_tag_create_command_marks_prerelease(attach, tmp_path):
    cmd = attach.build_command(tag="v5.8.0rc1", files=_files(tmp_path), release_exists=False)
    assert cmd[:3] == ["gh", "release", "create"]
    assert "--prerelease" in cmd


def test_stable_tag_create_command_no_prerelease(attach, tmp_path):
    cmd = attach.build_command(tag="v5.8.0", files=_files(tmp_path), release_exists=False)
    assert cmd[:3] == ["gh", "release", "create"]
    assert "--prerelease" not in cmd


def test_existing_release_upload_has_no_prerelease(attach, tmp_path):
    cmd = attach.build_command(tag="v5.8.0rc1", files=_files(tmp_path), release_exists=True)
    assert cmd[:3] == ["gh", "release", "upload"]
    assert "--clobber" in cmd
    assert "--prerelease" not in cmd
