#!/usr/bin/env python3
"""required checks 可满足性金丝雀：在 PR 上核对"清单里的 required 上下文是否真的都上报了"。

动机（`docs/ci-gates.md` §7.7）：`test_ci_required_checks.py`（仓库内三方对账）与 nightly 的
`check_required_checks_drift.py`（补第四处，但需 admin PAT）都够不着"PR 那一刻平台到底在拦什么"。
本金丝雀用**默认 `GITHUB_TOKEN`**（不需要 PAT、不需要 admin）读 PR 的可合并性实况，把三种
结构性问题变成 PR 上看得见的一条 check：

1. **清单里的 required 上下文从未上报**（作业被改名 / 删除，或清单与 GitHub 已脱节）——
   §7.1 的 B 轮语义：平台把它当「永不满足」，**所有 PR 永久停在 blocked**。
2. **清单里的 required 全绿、平台却仍判 `BLOCKED`** —— 存在清单**未列出**的 required 上下文
   （有人手工加了一条，或某条 required 的实际名字与清单不符）。此时真集比清单大。
3. 读不到实况 → `UNVERIFIED`，**绝不判绿**（本仓最鄙视的那种「永远绿的门禁」，比没有更坏）。

**方向性局限（必须知道）**：非 admin 只能读「平台认为能不能合」，读不到「平台要求什么」。
所以本金丝雀只能证明「平台要求得比清单**多**」，**无法**证明「平台要求得比清单少」
（保护被放松 / 删除）。那个危险方向由带外定时对账兜底（§7.7）—— 两处互补，缺一不可。

用法（由 `.github/workflows/ci.yml` 的 canary 作业调用）：:

    python scripts/check_required_checks_satisfiability.py --repo Levango7/MAOP --pr 99
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / ".github" / "ci-required-checks.json"
GRAPHQL = "https://api.github.com/graphql"

EXIT_MATCH = 0
EXIT_DRIFT = 1
EXIT_UNVERIFIED = 3

# 一条 check 达到"平台认为它满足 required"的两个结论：成功，或（docs-only 时被 `if:` 跳过的）
# 跳过。与 ci_merge_gate.py 的白名单语义一致 —— 其余一切结论（failure / cancelled / pending /
# timed_out / neutral / action_required / 空串）都不算满足。
GREEN = frozenset({"SUCCESS", "SKIPPED"})
# mergeStateStatus 里表示"平台认为这 PR 能合"的值（strict:false 下 BEHIND 也能合）。
MERGEABLE_STATES = frozenset({"CLEAN", "UNSTABLE", "HAS_HOOKS", "BEHIND"})

_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      number
      isDraft
      mergeStateStatus
      statusCheckRollup {
        contexts(first: 100) {
          totalCount
          nodes {
            __typename
            ... on CheckRun { name status conclusion }
            ... on StatusContext { context state }
          }
        }
      }
    }
  }
}
"""


def read_manifest(path: Path = MANIFEST) -> list[str]:
    """读权威清单里的 required 上下文名（保持文件内顺序，便于逐条对照）。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    return [entry["name"] for entry in data["contexts"]]


def normalize_rollup(pr: dict) -> dict[str, str]:
    """把 PR 的 check 汇总折成 {check 名: 规范化结论}。

    CheckRun 用 `conclusion`（未完成时用 status 折成 PENDING）；StatusContext 用 `state`。
    同名以最后一个为准（GitHub 同一次汇总里同名只会有一条有效记录）。
    """
    observed: dict[str, str] = {}
    nodes = (((pr.get("statusCheckRollup") or {}).get("contexts") or {}).get("nodes")) or []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if node.get("__typename") == "CheckRun":
            name = node.get("name")
            status = node.get("status")
            state = (
                "PENDING"
                if status and status != "COMPLETED"
                else (node.get("conclusion") or "PENDING")
            )
        else:
            name = node.get("context")
            state = node.get("state") or "PENDING"
        if isinstance(name, str):
            observed[name] = str(state)
    return observed


def evaluate(
    manifest: list[str], observed: dict[str, str], merge_state: str, is_draft: bool
) -> tuple[str, list[str]]:
    """纯判定：返回 (verdict, reasons)。reasons 为空表示无需注解。

    `merge_state` 由调用方保证是已读到的实况（`UNKNOWN` 走 UNVERIFIED，不落到这里）。
    本函数不读网络也不打印，便于被用例逐条钉住。
    """
    if is_draft:
        return "NOT-APPLICABLE", ["PR 是草稿（draft）：分支保护不参与判定，金丝雀不表态。"]

    absent = [n for n in manifest if n not in observed]
    unsatisfied = [n for n in manifest if observed.get(n) not in GREEN]

    if absent:
        # 清单里的 required 上下文在 PR 上**一条 check 都没产出**。金丝雀的 needs 覆盖了
        # 这些作业，所以"缺席"不是时序问题，是作业被改名/删除、或清单已与 GitHub 脱节。
        if merge_state == "BLOCKED":
            return "DRIFT", [
                f"清单里的 required 上下文 [{n}] 在本 PR 的 check 汇总里根本没出现；"
                f"平台会把它当作永不满足，所有 PR 永久停在 blocked（docs/ci-gates.md §7.1 B 轮语义）。"
                f"作业可能被改名/删除，或清单与 GitHub 分支保护已脱节。"
                for n in absent
            ]
        # 还没上报、平台却说能合 —— 信息自相矛盾，不猜。
        return "UNVERIFIED", [
            f"清单里的 [{n}] 未出现在汇总里，平台却判 mergeStateStatus={merge_state}"
            f"（自相矛盾，不当作通过）。"
            for n in absent
        ]

    if unsatisfied:
        # 清单内某条自己就没过：PR 本来就被我们自己的门禁挡着，金丝雀无从区分"是它挡的"
        # 还是"清单外还有一条挡着"。这不是通过，但也不该冒充红点 —— 真正的红点已由那条给出。
        return "NOT-APPLICABLE", [
            f"清单内 required 上下文 [{n}] 当前为 {observed.get(n)}（未通过/未完成）："
            f"PR 已被它挡着，金丝雀无法区分『是它』还是『清单外还有一条』，不表态。"
            for n in unsatisfied
        ]

    if merge_state == "BLOCKED":
        # 清单内全绿却被拦 ⇒ 平台在等一条清单里没有的 required。
        return "DRIFT", [
            (
                "清单里的 required 全部 success/skipped，平台却仍判 mergeStateStatus=BLOCKED ⇒ "
                "存在清单未列出的 required 上下文（或某条 required 的实际名字与清单不符）。"
                "请按 docs/ci-gates.md §7.4 读 /branches/<branch>/protection 对账。"
            )
        ]

    if merge_state in MERGEABLE_STATES:
        return "MATCH", []

    return "UNVERIFIED", [
        f"mergeStateStatus={merge_state!r} 既非可合并也非 BLOCKED，无法判定（不猜）。"
    ]


def fetch_pr(repo: str, number: int, token: str | None, timeout: int = 20) -> dict:
    """读 PR 的汇总实况。读不到就抛异常 —— 由调用方归入 UNVERIFIED，绝不返回空壳冒充。"""
    if not token:
        raise RuntimeError("没有可用 token（GITHUB_TOKEN 未注入）")
    owner, _, name = repo.partition("/")
    if not owner or not name:
        raise RuntimeError(f"--repo 形状不对：{repo!r}（应为 owner/name）")
    payload = json.dumps(
        {"query": _QUERY, "variables": {"owner": owner, "name": name, "number": number}}
    ).encode("utf-8")
    req = urllib.request.Request(
        GRAPHQL,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "maop-required-checks-canary",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.load(resp)
    if body.get("errors"):
        raise RuntimeError(
            "GraphQL 返回错误：" + json.dumps(body["errors"], ensure_ascii=False)[:300]
        )
    pr = ((body.get("data") or {}).get("repository") or {}).get("pullRequest")
    if not isinstance(pr, dict):
        raise TypeError(f"拿不到 PR #{number} 的汇总（repo={repo}）")
    return pr


def poll_until_settled(
    repo: str, number: int, token: str | None, attempts: int = 4, interval: float = 5.0
) -> dict:
    """`mergeStateStatus` 偶发 `UNKNOWN`（平台还在算可合并性）。有界轮询直到不再是 UNKNOWN。

    只在 UNKNOWN 时等；其余状态立刻返回（含 BLOCKED —— 那是实况，不是"没算完"）。
    """
    pr = fetch_pr(repo, number, token)
    for _ in range(attempts - 1):
        if str(pr.get("mergeStateStatus") or "UNKNOWN") != "UNKNOWN":
            break
        time.sleep(interval)
        pr = fetch_pr(repo, number, token)
    return pr


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="required checks 可满足性金丝雀")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "Levango7/MAOP"))
    ap.add_argument("--pr", type=int, default=None, help="PR 号；默认取 $PR_NUMBER")
    ap.add_argument("--manifest", default=str(MANIFEST))
    ap.add_argument("--token", default=None, help="默认取 $GITHUB_TOKEN")
    args = ap.parse_args(argv)

    number = args.pr
    if number is None:
        raw = os.environ.get("PR_NUMBER", "").strip()
        number = int(raw) if raw.isdigit() else None
    if number is None:
        print(
            "::warning title=required checks canary::没有 PR 号（非 pull_request 事件？），跳过。"
        )
        return EXIT_MATCH

    try:
        manifest = read_manifest(Path(args.manifest))
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"::error title=required checks canary::读清单失败：{exc}")
        return EXIT_DRIFT

    token = args.token or os.environ.get("GITHUB_TOKEN")
    try:
        pr = poll_until_settled(args.repo, number, token)
    except urllib.error.HTTPError as exc:
        return _unverified(f"PR #{number} 汇总读取失败：HTTP {exc.code}", exc)
    except (urllib.error.URLError, RuntimeError, TypeError, TimeoutError, OSError) as exc:
        return _unverified(f"PR #{number} 汇总读取失败：{exc}", exc)

    observed = normalize_rollup(pr)
    merge_state = str(pr.get("mergeStateStatus") or "UNKNOWN")
    is_draft = bool(pr.get("isDraft"))

    print(f"required checks canary：{args.repo} PR #{number}")
    print(f"  清单（{len(manifest)} 条）：{manifest}")
    print(f"  mergeStateStatus={merge_state} isDraft={is_draft}")
    summary = ", ".join(f"{name}={observed.get(name, '<缺席>')}" for name in manifest)
    print(f"  汇总结论：{{{summary}}}")

    verdict, reasons = evaluate(manifest, observed, merge_state, is_draft)
    for reason in reasons:
        print(f"::notice title=required checks canary::{reason}")
    if verdict == "DRIFT":
        for reason in reasons:
            print(f"::error title=required checks canary::{reason}")
        print(f"DRIFT: 平台侧的 required 与仓库清单不符（{len(reasons)} 处）")
        return EXIT_DRIFT
    if verdict == "UNVERIFIED":
        for reason in reasons:
            print(f"::warning title=required checks canary::{reason}")
        print("UNVERIFIED: 本次没能读到可信实况，**不等于通过**。")
        return EXIT_UNVERIFIED
    if verdict == "NOT-APPLICABLE":
        print("NOT-APPLICABLE: 本 PR 由清单内的门禁本身挡着，金丝雀不表态（不是通过）。")
        return EXIT_MATCH
    print("OK: 清单里的 required 全部上报且满足，平台未发现清单外的 required。")
    return EXIT_MATCH


def _unverified(detail: str, exc: object) -> int:
    print(f"::warning title=required checks canary::{detail}")
    print(f"  （{exc.__class__.__name__}: {exc}）")
    print("UNVERIFIED: 本次没能读到实况，**不等于一致**。")
    print("  金丝雀当前没有在守卫 —— 需要能读 PR 汇总的默认 GITHUB_TOKEN。")
    return EXIT_UNVERIFIED


if __name__ == "__main__":
    # 非 UTF-8 控制台（en-US runner 的 cp1252）下中文摘要会抛 UnicodeEncodeError，把判定崩成
    # 非零 —— 本仓在 Windows 腿上栽过一次（见 ci_merge_gate.py / check_required_checks_drift.py 同款处理）。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass
    raise SystemExit(main())
