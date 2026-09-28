"""`.github/dependabot.yml` 的结构守卫。

背景：2026-09-26 那批 dependabot PR 里，github-actions / docker 因为没有 groups，
一次扫描开出 4 个 PR，各跑一整套 9 平台矩阵，还都改 ci.yml 的相邻行互相冲突。
分组是解药，但配置文件本身没有任何东西在验证它 —— 写错键名时 Dependabot 只是
"不再开 PR"，静默失效。这里补一层本地可判定的结构校验。

边界（明说）：这是**结构**校验，不等同于 GitHub 服务端的全部语义校验；
真正未知键会被下面 allowed-keys 白名单挡掉，但服务端独有的规则仍可能漏。
不引入任何第三方 validator action（现成可选的都是 0~33 星的个人仓库）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

CONFIG = Path(__file__).resolve().parents[2] / ".github" / "dependabot.yml"

ENTRY_KEYS = {
    "package-ecosystem", "directory", "schedule", "allow", "ignore", "labels",
    "exclude-paths", "modifiable-by", "open-pull-requests-limit", "pull-request-branch-name",
    "rebase-strategy", "reviewers", "target-branch", "commit-message", "groups",
    "registries", "vendor", "insecure-external-code-execution", "cooldown",
}
GROUP_KEYS = {
    "patterns", "exclude-patterns", "dependency-type", "update-types",
    "applies-to", "labels", "maximum", "exclude-paths",
}
UPDATE_TYPES = {"major", "minor", "patch"}
ECOSYSTEMS = {
    "bundler", "cargo", "composer", "docker", "elm", "fingerprint", "gitea-actions",
    "github-actions", "gleam", "go", "gradle", "hex", "maven", "npm", "nuget",
    "other", "pip", "pub", "terraform", "uv", "docker-compose", "swift", "devcontainers",
    "submodules", "opentofu", "uv-pip",
}


def _config() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def test_config_is_valid_yaml_with_expected_shape():
    cfg = _config()
    assert cfg.get("version") == 2, "dependabot.yml 必须是 version: 2"
    updates = cfg.get("updates")
    assert isinstance(updates, list) and updates, "updates 必须是非空列表"
    for entry in updates:
        assert entry["package-ecosystem"] in ECOSYSTEMS, entry["package-ecosystem"]
        assert isinstance(entry.get("directory"), str)
        interval = entry["schedule"]["interval"]
        assert interval in {"daily", "weekly", "monthly", "quarterly"}, interval
        unknown = set(entry) - ENTRY_KEYS
        assert not unknown, f"未知的条目键（可能是拼写错误，会被静默忽略）：{unknown}"


def test_no_duplicate_ecosystem_directory_pair():
    """GitHub 会直接拒绝同一个 ecosystem+directory 出现两次。"""
    pairs = [(e["package-ecosystem"], e["directory"]) for e in _config()["updates"]]
    dupes = {p for p in pairs if pairs.count(p) > 1}
    assert not dupes, f"重复的 package-ecosystem + directory 组合：{dupes}"


def test_groups_use_legal_keys_and_update_types():
    for entry in _config()["updates"]:
        for name, group in (entry.get("groups") or {}).items():
            unknown = set(group) - GROUP_KEYS
            assert not unknown, f"分组 {name} 含未知键：{unknown}"
            for ut in group.get("update-types") or []:
                assert ut in UPDATE_TYPES, f"分组 {name} 的 update-types 非法：{ut}"
            if not (group.get("patterns") or group.get("exclude-patterns")
                    or group.get("update-types") or group.get("dependency-type")):
                raise AssertionError(f"分组 {name} 没有任何匹配条件，等于不生效")


def test_every_ecosystem_batches_minor_patch():
    """2026-09-26 的教训：不分组 → 一次扫描开出多个 PR，各跑一整套矩阵还互相撞文件。"""
    ungrouped = []
    for entry in _config()["updates"]:
        groups = (entry.get("groups") or {})
        has_minor_patch = any(
            {"minor", "patch"} & set(g.get("update-types") or {"minor", "patch"})
            for g in groups.values()
        ) or bool(groups)
        if not has_minor_patch:
            ungrouped.append(f"{entry['package-ecosystem']}@{entry['directory']}")
    assert not ungrouped, f"这些生态没有分组，会重演'多 PR × 全矩阵'的开销：{ungrouped}"


def test_labels_are_declared_for_triage():
    for entry in _config()["updates"]:
        labels = entry.get("labels") or []
        assert "dependencies" in labels, f"{entry['package-ecosystem']} 缺 dependencies 标签"
        for label in labels:
            assert re.match(r"^[a-z0-9._/-]+$", label), f"标签命名异常：{label}"


# --- 出现两次以上的 action 前缀必须成批 -----------------------------------
# 为什么需要这条：#28 当时只给 `actions/*` 加了分组，`docker/*` 漏了 —— 后果是
# 2026-09-27 那次周一扫描又开出 #35 / #36 / #38 三个独立 PR，各跑一轮 ~20 分钟的
# 9 平台矩阵，并且三者都改 ci.yml 的相邻行、互相冲突要反复 rebase。
# 靠人记住"哪些前缀该成批"必然会再漏，所以让仓库自己说：凡是在 workflow 里
# 出现 ≥2 个不同包的 action 前缀，都必须被某个分组 pattern 覆盖。
_USES_RE = re.compile(r"^\s*(?:-\s+)?uses:\s*([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", re.MULTILINE)


def _action_packages() -> dict[str, set[str]]:
    """{owner: {repo, ...}}，只统计 GitHub 托管的 action（排除 ./ 本地与 docker://）。"""
    out: dict[str, set[str]] = {}
    workflows = (CONFIG.parent / "workflows").glob("*.y*ml")
    for wf in workflows:
        for owner, repo in _USES_RE.findall(wf.read_text(encoding="utf-8")):
            out.setdefault(owner, set()).add(repo)
    return out


def _group_patterns_for_ecosystem(ecosystem: str) -> list[str]:
    patterns: list[str] = []
    for entry in _config()["updates"]:
        if entry["package-ecosystem"] != ecosystem:
            continue
        for group in (entry.get("groups") or {}).values():
            patterns.extend(group.get("patterns") or [])
    return patterns


def test_owners_with_multiple_action_packages_are_batched():
    """≥2 个包的同前缀 action 必须被某个 dependabot 分组 pattern 覆盖。"""
    from fnmatch import fnmatch

    patterns = _group_patterns_for_ecosystem("github-actions")
    assert patterns, "github-actions 生态里一个分组都没有，批量策略未落地"
    unbatched: dict[str, set[str]] = {}
    for owner, repos in _action_packages().items():
        if len(repos) < 2:
            continue  # 单包前缀只可能开一个 PR，本来就不需要成批
        if not any(fnmatch(f"{owner}/*", p) or fnmatch(f"{owner}/{r}", p)
                   for p in patterns for r in repos):
            unbatched[owner] = repos
    assert not unbatched, (
        "这些 action 前缀在 workflow 里有多个包却没被任何 dependabot 分组覆盖，"
        f"每次扫描都会开一堆互相冲突的独立 PR：{unbatched}"
    )
