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
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = pathlib.Path(__file__).resolve().parents[2]
CI = REPO / ".github" / "workflows" / "ci.yml"
SCRIPTS = REPO / "py" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import attach_release_assets
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


class TestPublishArtifactPath:
    """产物目录必须与构建步骤的实际产出对齐 —— 发布链上最贵的一类"静默错位"。

    实况（2026-10-10 定位）：publish 作业 `defaults.run.working-directory: py`，
    `python -m build` 产出在 `py/dist`；而 pypa action 是**容器 action**，不受
    defaults 影响，`packages-dir` 按**工作区根**解析。原写 `dist/` ⇒
    `FileNotFoundError: /github/workspace/dist` —— v5.2.1 与 v5.3.0 两次 tag 发布
    都倒在这里，还被误诊为"trusted publisher 未配的预期红"。
    本守卫把「构建在哪里产出」与「action 去哪里找」绑死。
    """

    @pytest.mark.parametrize("job_name", ["publish", "publish-manual"])
    def test_packages_dir_matches_build_output(self, job_name: str) -> None:
        job = _doc()["jobs"][job_name]
        wd = str(
            ((job.get("defaults") or {}).get("run") or {}).get("working-directory") or ""
        ).rstrip("/")
        step = next(
            s
            for s in job["steps"]
            if str(s.get("uses", "")).startswith("pypa/gh-action-pypi-publish")
        )
        packages_dir = str((step.get("with") or {}).get("packages-dir") or "").rstrip("/")
        expected = f"{wd}/dist" if wd else "dist"
        assert packages_dir == expected, (
            f"{job_name}: 构建在 {wd or '仓库根'}/ 下执行（产物 {expected}），"
            f"但 packages-dir={packages_dir!r}（按工作区根解析）⇒ 找不到产物"
        )

    @pytest.mark.parametrize("job_name", ["publish", "publish-manual"])
    def test_build_step_runs_in_the_declared_workdir(self, job_name: str) -> None:
        """packages-dir 的对齐前提：构建步骤确实受 defaults 影响（即 run 步骤）。"""
        job = _doc()["jobs"][job_name]
        build = next(s for s in job["steps"] if "Build packages" in str(s.get("name", "")))
        assert "run" in build, "构建步骤必须是 run 步骤，否则不继承 defaults.working-directory"


class TestManualPublishEscapeHatch:
    """手工发布逃生舱（2026-10-10 建）：tag 发布链自身有 bug 时的修复通道。

    背景：GitHub 的 "Re-run failed jobs" 使用**原 run 提交里的 workflow 文件**，
    所以修好 ci.yml 无法让旧 tag run 变绿；escape hatch 从 master 侧 checkout
    目标 tag、重建并走同一 OIDC 路径。它必须只在手工 dispatch 时触发。
    """

    def _job(self) -> dict:
        return _doc()["jobs"]["publish-manual"]

    def test_dispatch_only(self) -> None:
        cond = str(self._job().get("if") or "")
        assert "workflow_dispatch" in cond, "逃生舱不得在 push/PR 上触发"
        assert "inputs.publish_tag" in cond, "必须要求明确给出要发布的 tag"

    def test_workflow_dispatch_declares_publish_tag_input(self) -> None:
        dispatch = _triggers(_doc()).get("workflow_dispatch")
        assert isinstance(dispatch, dict), "workflow_dispatch 必须是映射才能带 inputs"
        assert "publish_tag" in (dispatch.get("inputs") or {}), (
            "dispatch 缺 publish_tag 输入 ⇒ 逃生舱无法被指向目标 tag"
        )

    def test_checks_out_the_requested_tag(self) -> None:
        step = next(
            s for s in self._job()["steps"]
            if str(s.get("uses", "")).startswith("actions/checkout")
        )
        ref = str((step.get("with") or {}).get("ref") or "")
        assert "inputs.publish_tag" in ref, "必须从目标 tag 的提交构建（与 tag 发布同源）"

    def test_precheck_guards_tag_and_runs_before_build(self) -> None:
        steps = self._job()["steps"]
        names = [str(s.get("name") or s.get("uses") or "") for s in steps]
        pre = next((i for i, n in enumerate(names) if "Release integrity precheck" in n), None)
        build = next((i for i, n in enumerate(names) if "Build packages" in n), None)
        assert pre is not None and build is not None and pre < build
        pre_step = steps[pre]
        assert "check_release_tag.py" in str(pre_step.get("run", ""))
        assert "inputs.publish_tag" in str(pre_step.get("run", "")), (
            "完整性检查必须校验的是**目标 tag** 的版本，不是当前 ref"
        )


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


class TestReleaseAssets:
    """GitHub Release 附件作业：零外部账号的分发路径，必须同样"可达且真做事"。"""

    JOB = "release-assets"

    def _job(self) -> dict:
        jobs = _doc()["jobs"]
        assert self.JOB in jobs, f"ci.yml 缺 {self.JOB} 作业 ⇒ 没有 PyPI 账号时又变成无分发路径"
        return jobs[self.JOB]

    def test_reachable_on_tag_push(self) -> None:
        assert "refs/tags/v" in str(self._job().get("if", "")), "作业条件没锁定 tag，等于永远不跑或每次都跑"

    def test_dependency_chain_runs_on_tag_push(self) -> None:
        jobs = _doc()["jobs"]
        offenders = [
            f"{name}: {jobs[name].get('if')}"
            for name in _dependency_closure(jobs, self.JOB)
            if "refs/heads/" in str(jobs[name].get("if") or "")
        ]
        assert not offenders, "Release 附件链上有分支限定条件，tag push 会被级联跳过：" + " | ".join(offenders)

    def test_has_contents_write_permission(self) -> None:
        perms = self._job().get("permissions") or {}
        assert perms.get("contents") == "write", f"上传 release 附件需要 contents: write，实际 {perms}"

    def test_not_a_required_check(self) -> None:
        manifest = json.loads((REPO / ".github" / "ci-required-checks.json").read_text(encoding="utf-8-sig"))
        assert self.JOB not in {c["job"] for c in manifest["contexts"]}, (
            "Release 附件进 required 会让每次 tag push 的失败锁住主干 PR —— 它必须是独立后果"
        )

    def test_step_invokes_the_script_with_tag_and_dist(self) -> None:
        steps = self._job()["steps"]
        step = next((s for s in steps if "attach_release_assets.py" in str(s.get("run", ""))), None)
        assert step is not None, "没有调用 attach_release_assets.py 的步骤 ⇒ 构建产物无处可去"
        run = str(step["run"])
        assert "GITHUB_REF_NAME" in run, "没把真实 tag 传给脚本"
        assert "dist" in run, "没指向构建产物目录"
        assert "|| true" not in run and "continue-on-error" not in str(step), "上传失败不许被吞"


class TestAttachReleaseAssetsScript:
    def _dist(self, tmp_path: Path, names: dict[str, int]) -> Path:
        d = tmp_path / "dist"
        d.mkdir()
        for name, size in names.items():
            (d / name).write_bytes(b"x" * size)
        return d

    def test_picks_wheel_and_sdist(self, tmp_path: Path) -> None:
        d = self._dist(tmp_path, {
            "maop_orchestrator-5.2.1-py3-none-any.whl": 10,
            "maop_orchestrator-5.2.1.tar.gz": 20,
            "random.log": 5,
        })
        got = [p.name for p in attach_release_assets.expected_files(d)]
        assert got == ["maop_orchestrator-5.2.1-py3-none-any.whl", "maop_orchestrator-5.2.1.tar.gz"]

    def test_empty_file_is_not_an_artifact(self, tmp_path: Path) -> None:
        d = self._dist(tmp_path, {"maop-5.2.1-py3-none-any.whl": 0, "x.tar.gz": 3})
        assert [p.name for p in attach_release_assets.expected_files(d)] == ["x.tar.gz"]

    def test_existing_release_uses_upload_clobber(self) -> None:
        files = [Path("a.whl"), Path("a.tar.gz")]
        cmd = attach_release_assets.build_command(tag="v5.2.1", files=files, release_exists=True)
        assert cmd[:3] == ["gh", "release", "upload"] and "--clobber" in cmd

    def test_missing_release_creates_it_with_verified_tag(self) -> None:
        cmd = attach_release_assets.build_command(tag="v5.2.1", files=[Path("a.whl")], release_exists=False)
        assert cmd[:3] == ["gh", "release", "create"]
        assert "--verify-tag" in cmd, "不校验 tag 就创建 release，会把不存在的 tag 当成新建对象"

    def test_dry_run_with_no_artifacts_fails_loudly(self, tmp_path: Path) -> None:
        d = tmp_path / "dist"
        d.mkdir()
        code = attach_release_assets.main([
            "--tag", "v5.2.1", "--dist-dir", str(d), "--repo", "o/r", "--dry-run",
        ])
        assert code == 1, "空产物目录必须拦下：否则会出现「release 建好了但什么都没挂上」"

    def test_dry_run_without_repo_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # 在 Actions 里跑测试时 GITHUB_REPOSITORY 是有值的，必须显式清掉才能测到"缺 repo"分支
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        d = self._dist(tmp_path, {"a-py3-none-any.whl": 1})
        code = attach_release_assets.main(["--tag", "v5.2.1", "--dist-dir", str(d), "--dry-run"])
        assert code == 1, "没有 --repo 也没有 GITHUB_REPOSITORY 时必须失败而不是猜默认值"
