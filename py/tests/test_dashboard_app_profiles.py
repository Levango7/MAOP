"""`dashboard_app_profiles.overlay_probe` 的子进程边界用例。

探针是这条门禁里唯一"读外部进程输出"的地方：它把子进程 stdout 的最后一行
当 JSON 解析，下游（`overlay_only_modules`）直接按 `dict[str, str]` 比较
`'ok'` 与缺失模块名。CI 只跑 `mypy maop/`，`tests/` 不在类型检查面内，
所以"解析结果没验形状"这件事不会被任何门禁抓到——这条文件把它钉住。
"""

from __future__ import annotations

import json
import types

import pytest

from tests import dashboard_app_profiles as profiles


def _fake_run(stdout: str, returncode: int = 0, stderr: str = ""):
    def runner(*args, **kwargs):
        return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    return runner


def test_returns_the_str_mapping_the_callers_expect(monkeypatch):
    payload = json.dumps({"sso": "ok", "n8n": "maop.enterprise.n8n"})
    # 真实探针会把 import 过程的噪音打在前面，只有最后一行是 JSON。
    monkeypatch.setattr(profiles.subprocess, "run", _fake_run(f'import noise\n{payload}'))
    got = profiles.overlay_probe(("maop.dashboard.routers.sso", "maop.dashboard.routers.n8n"))
    assert got == {"sso": "ok", "n8n": "maop.enterprise.n8n"}
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in got.items())


@pytest.mark.parametrize(
    "payload",
    [
        '["ok", "ok"]',          # 顶层不是 dict
        '"plain string"',        # 顶层是字符串
        '{"sso": 1}',            # 值不是 str —— 下游会拿它和 "ok" 比
        '{"sso": null}',
    ],
)
def test_rejects_malformed_child_output_instead_of_passing_it_on(monkeypatch, payload):
    monkeypatch.setattr(profiles.subprocess, "run", _fake_run(f'["x"]\n{payload}'))
    with pytest.raises(RuntimeError, match="unexpected shape"):
        profiles.overlay_probe(("maop.dashboard.routers.sso",))


def test_child_failure_is_a_hard_error_not_an_empty_probe(monkeypatch):
    # 探针自己挂了不能当成"所有 router 都能导入"，否则盲区会伪装成通过。
    monkeypatch.setattr(profiles.subprocess, "run", _fake_run("", returncode=1, stderr="boom"))
    with pytest.raises(RuntimeError, match="probe failed"):
        profiles.overlay_probe(("maop.dashboard.routers.sso",))
