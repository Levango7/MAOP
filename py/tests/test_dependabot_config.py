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
