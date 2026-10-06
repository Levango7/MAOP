"""`Publish to PyPI` 必须真的可达 —— 防止"写了 if 但平台永远不发那个事件"回潮。

起因（本仓自证）：``ci.yml`` 的 publish 作业带 ``if: ... refs/tags/v``，但 ``on.push``
只声明了 ``branches``，而 GitHub 的 branches 过滤会排除所有 tag push ⇒ 该作业从
2026-09-29 起"永不产生"。当时的处理是在注释里认错（"当前不可达"），行为没变 ——
于是"发布管线已就位"这句暗示一直是假的，PyPI 上两个包名实测 404。

本文件把这件事变成可执行事实，并覆盖两类同形失效：
① 触发面没有 tag；② 触发面有 tag，但依赖链上某个作业只在分支上跑 ⇒ publish 被级联跳过。
"""

from __future__ import annotations

import fnmatch
import json
import pathlib
import sys

import pytest

yaml = pytest.importorskip("yaml")

REPO = pathlib.Path(__file__).resolve().parents[2]
CI = REPO / ".github" / "workflows" / "ci.yml"
SCRIPTS = REPO / "py" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_release_tag
import ci_path_scope


def _doc() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def _triggers(doc: dict) -> dict:
    return doc.get("on") or doc.get(True) or {}


def _needs_of(jobs: dict, name: str) -> list[str]:
    needs = jobs[name].get("needs")
    if needs is None:
        return []
    return [needs] if isinstance(needs, str) else list(needs)


def _dependency_closure(jobs: dict, root: str) -> list[str]:
    """root 及其所有祖先作业（发布链上的每一环都必须能在该事件上运行）。"""
    out: list[str] = []
    stack = [root]
    while stack:
        name = stack.pop()
        if name in out:
            continue
        out.append(name)
        stack.extend(_needs_of(jobs, name))
    return out


def unreachable_jobs_for_tag_push(doc: dict) -> list[str]:
    """给定一份 workflow 文档，返回"只在 tag 上跑、却拿不到 tag 事件"的作业名。

    判定用的是文档自己的声明，不是文本猜测：``if`` 里出现 ``refs/tags/`` 的作业，
    要求 ``on.push.tags`` 存在且模式能匹配 ``v1.2.3``。
    """
    jobs = doc.get("jobs") or {}
    push = _triggers(doc).get("push")
    tags = [] if not isinstance(push, dict) else list(push.get("tags") or [])
    admitted = any(fnmatch.fnmatch("v1.2.3", pattern) for pattern in tags)
    blocked = []
    for name, job in jobs.items():
        condition = str(job.get("if") or "")
        if "refs/tags/" in condition and not admitted:
            blocked.append(name)
    return sorted(blocked)


class TestTriggerSurface:
    def test_push_declares_tag_filter(self) -> None:
        push = _triggers(_doc()).get("push")
        assert isinstance(push, dict), "on.push 必须是映射才能同时声明 branches 与 tags"
        assert push.get("tags"), "on.push.tags 缺失 ⇒ publish 的 tag 条件永不成立"

    def test_tag_pattern_admits_current_release(self) -> None:
        patterns = _triggers(_doc())["push"]["tags"]
        version = check_release_tag.package_version(REPO / "py")
        assert version, "读不到 __version__，本用例失去意义"
        tag = f"v{version}"
        assert any(fnmatch.fnmatch(tag, p) for p in patterns), f"{tag} 不被 {patterns} 接纳"

    def test_branch_trigger_unchanged(self) -> None:
        # 打通 tag 不许顺手改掉分支行为：三处 trunk 仍要在
        assert _triggers(_doc())["push"]["branches"] == ["main", "master", "develop"]

    def test_publish_is_not_a_required_check(self) -> None:
        """发布失败不该拦住合并 —— 若有人把它加进 required 清单，这条会红并提醒决策。"""
        manifest = json.loads((REPO / ".github" / "ci-required-checks.json").read_text(encoding="utf-8-sig"))
        jobs = {c["job"] for c in manifest["contexts"]}
        assert "publish" not in jobs, (
            "publish 进 required 需单独决策：PyPI 侧 trusted publisher 未就绪时，"
            "它会把主干上所有 PR 锁死"
        )


class TestPublishJobShape:
    def _publish(self) -> dict:
        return _doc()["jobs"]["publish"]

    def test_condition_targets_version_tags(self) -> None:
        assert "refs/tags/v" in str(self._publish().get("if", ""))

    def test_no_job_is_tag_only_yet_unreachable(self) -> None:
        assert unreachable_jobs_for_tag_push(_doc()) == []

    def test_dependency_chain_has_no_branch_only_condition(self) -> None:
        """publish 的每个祖先都得能在 tag push 上跑。

        典型失效：某个 needs 写了 ``github.ref == 'refs/heads/master'`` ⇒ tag push 上它
        skipped，publish 连带 skipped，"补了 tags 触发"看起来做了其实没做。
        """
        jobs = _doc()["jobs"]
        offenders = []
        for name in _dependency_closure(jobs, "publish"):
            condition = str(jobs[name].get("if") or "")
            if "refs/heads/" in condition:
                offenders.append(f"{name}: {condition}")
        assert not offenders, "发布链上存在分支限定条件：" + " | ".join(offenders)

    def test_scope_is_conservative_on_tag_push(self) -> None:
        """tag push 拿不到 diff 基线 ⇒ 变更集为空 ⇒ 必须判"跑全量"，否则前端/E2E 被跳过。"""
        assert ci_path_scope.needs_heavy_ci([]) is True

    def test_release_precheck_runs_before_build(self) -> None:
        names = [str(step.get("name") or step.get("uses") or "") for step in self._publish()["steps"]]
        pre = next((i for i, n in enumerate(names) if "Release integrity precheck" in n), None)
        build = next((i for i, n in enumerate(names) if "Build packages" in n), None)
        assert pre is not None, "publish 缺发布前完整性检查步骤"
        assert build is not None and pre < build, "前置检查必须在构建之前，否则红得没有意义"

    def test_precheck_step_uses_the_script(self) -> None:
        steps = self._publish()["steps"]
        step = next(s for s in steps if "Release integrity precheck" in str(s.get("name", "")))
        assert "check_release_tag.py" in str(step.get("run", ""))
        assert "github.ref" in str(step.get("run", "")), "没把真实 ref 传进去，检查等于空转"


class TestReleaseTagScript:
    def test_matching_versions_pass(self, tmp_path: pathlib.Path) -> None:
        init = tmp_path / "maop"
        init.mkdir()
        (init / "__init__.py").write_text('__version__ = "5.2.1"\n', encoding="utf-8")
        ok, msg = check_release_tag.check("refs/tags/v5.2.1", tmp_path)
        assert ok, msg

    @pytest.mark.parametrize(
        "ref",
        ["refs/tags/v5.2.2", "refs/tags/5.2.1", "refs/heads/master", "refs/tags/v5.2"],
    )
    def test_bad_input_is_rejected(self, tmp_path: pathlib.Path, ref: str) -> None:
        init = tmp_path / "maop"
        init.mkdir()
        (init / "__init__.py").write_text('__version__ = "5.2.1"\n', encoding="utf-8")
        ok, msg = check_release_tag.check(ref, tmp_path)
        assert not ok and msg, f"{ref} 应当被拦下"

    def test_mismatch_names_both_versions(self, tmp_path: pathlib.Path) -> None:
        init = tmp_path / "maop"
        init.mkdir()
        (init / "__init__.py").write_text('__version__ = "5.2.1"\n', encoding="utf-8")
        ok, msg = check_release_tag.check("refs/tags/v9.9.9", tmp_path)
        assert not ok
        assert "9.9.9" in msg and "5.2.1" in msg

    def test_missing_version_is_not_silently_ok(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "maop").mkdir()
        ok, msg = check_release_tag.check("refs/tags/v5.2.1", tmp_path)
        assert not ok and "__version__" in msg


class TestGuardIsNotVacuous:
    def test_synthetic_missing_tag_trigger_is_caught(self) -> None:
        doc = {
            "on": {"push": {"branches": ["master"]}},
            "jobs": {"publish": {"if": "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')"}},
        }
        assert unreachable_jobs_for_tag_push(doc) == ["publish"]

    def test_synthetic_fixed_version_of_the_same_doc_passes(self) -> None:
        doc = {
            "on": {"push": {"branches": ["master"], "tags": ["v*"]}},
            "jobs": {"publish": {"if": "startsWith(github.ref, 'refs/tags/v')"}},
        }
        assert unreachable_jobs_for_tag_push(doc) == []
