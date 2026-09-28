#!/usr/bin/env python3
"""判定一次 CI 变更集是否需要跑"重活"（9 平台矩阵 / 前端构建 / E2E …）。

为什么需要它：原先 `ci.yml` 用 `on.pull_request.paths` 白名单来做这件事。白名单的问题不是
"不工作"，而是**不匹配时整个 workflow 一条 check 都不产生** —— 于是"CI 全绿"和"CI 没跑"
在 API 上长得一模一样（`pending=0 && fail=0` 对两种情况都成立）。本会话里这个歧义至少骗了
我们一次（一个只改 CHANGELOG.md 的 PR 被当成"全绿"合掉了）。

把判断下沉到 job 级之后，workflow 对任何 PR 都会跑，`scope` 作业永远存在并明确说出
"这次是 docs-only，所以重活被跳过" —— 跳过是**看得见**的状态，不是缺席。

保守优先：拿不准（基线算不出来、变更集为空）就一律判 `True`（跑全量），绝不因为"没数据"
而静默少跑。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable

# 迁移前 `on.*.paths` 白名单的超集：原白名单漏掉了一批"CI 会读、会挂载"的部署工件，
# 2026-09-29 补齐（下注）。改这里就等于改触发面，必须带用例。
CODE_PREFIXES: tuple[str, ...] = (
    "py/",
    "config/",
    "dashboard/",
    "dashboard-enterprise/",
    ".github/workflows/",
    # 2026-09-29 补：部署工件是 CI 的输入，不是"配置旁支"。
    # deploy/ / alertmanager/ 有测试直接读：test_k8s_operator.py 从
    # deploy/k8s/operator 读 Helm chart，test_observability.py 读
    # deploy/otel-collector.yaml 与 grafana json，test_alertmanager_e2e.py 读
    # alertmanager.yml 与 alertmanager/templates —— 漏判会让"改了被测工件却不跑
    # 测试矩阵"成立，正是本分类器要防的静默少跑。
    # monitoring/ 与 nginx 两个 conf 是 compose 栈的挂载源（monitoring/tls
    # profile 的告警规则与 ingress），内容坏掉时栈起不来而非"文档不准"。
    "deploy/",
    "monitoring/",
    "alertmanager/",
    "docker-compose.yml",
    "docker-compose.prod.yml",
    ".dockerignore",
)

# README/ROADMAP 被 lint job 的 Doc↔Code reconcile 门禁显式核对，
# 所以它们算"会影响 CI 结论"的文件，不是 docs-only。
CODE_FILES: frozenset[str] = frozenset(
    {
        "README.md",
        "ROADMAP.md",
        "py/Dockerfile",
        "py/requirements.lock",
        "py/requirements.txt",
        # compose 直接挂载的根级文件（nginx = tls ingress，alertmanager.yml =
        # 告警出口模板，由 alertmanager/render-config.sh 在容器启动时渲染）。
        "nginx.conf",
        "nginx.prod.conf",
        "alertmanager.yml",
    }
)


def _normalize(paths: Iterable[str]) -> list[str]:
    """去掉仓库内 git 会给出的 `./` 前缀，但**不能**用 lstrip('./')：
    那会把 `.github/…` / `.dockerignore` 的前导点也吃掉，于是隐藏目录类模式全部失配。"""
    out = []
    for p in paths:
        if not p:
            continue
        p = p.strip()
        while p.startswith("./"):
            p = p[2:]
        if p:
            out.append(p)
    return out


def needs_heavy_ci(paths: Iterable[str]) -> bool:
    """变更集是否应触发重活。空集合按 True 处理（宁可多跑，不可漏判）。"""
    seen = _normalize(paths)
    if not seen:
        return True
    return any(p in CODE_FILES or p.startswith(CODE_PREFIXES) for p in seen)


def summarize(paths: Iterable[str]) -> str:
    """人读的一行说明，用于写进 job summary / 日志。"""
    seen = _normalize(paths)
    hits = sorted({p for p in seen if p in CODE_FILES or p.startswith(CODE_PREFIXES)})
    if not seen:
        return "变更集为空或基线不可用 → 按全量 CI 处理（保守）"
    if not hits:
        return f"docs-only（{len(seen)} 个文件，无一落在 CI 关注面内）→ 跳过重活"
    return f"{len(hits)}/{len(seen)} 个文件落在 CI 关注面 → 跑全量；命中样例: {', '.join(hits[:6])}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="classify a changeset as code vs docs-only")
    ap.add_argument("--files-from", help="read NUL- or newline-separated paths from a file ('-' = stdin)")
    ap.add_argument("--github-output", help="append code=true|false to this GITHUB_OUTPUT file")
    args = ap.parse_args(argv)

    if args.files_from and args.files_from != "-":
        with open(args.files_from, encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    else:
        raw = sys.stdin.read()
    paths = [p for p in raw.replace("\x00", "\n").splitlines() if p.strip()]

    heavy = needs_heavy_ci(paths)
    print(summarize(paths))
    print(f"code={'true' if heavy else 'false'}")
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as fh:
            fh.write(f"code={'true' if heavy else 'false'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
