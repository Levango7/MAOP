#!/usr/bin/env python3
"""CI merge gate：把"上游作业有没有真的跑过并通过"收敛成一条**名字稳定**的 check。

动机见 `docs/ci-gates.md` §7.3：矩阵作业 `pytest (...)` 在 docs-only 时上报的 check 名是
**未展开的模板字面量**，与代码 PR 上 9 条展开名都不同 ⇒ 无法按名字设成 required；而 push-only
的作业在 PR 上恒为 skipped ⇒ 设成 required 等于"永远空满足"。两种情况下 required 都拦不住
最重要的那套测试。本脚本把一组上游结果折成单一上下文 `CI merge gate`，让平台来拦。

判定是**白名单式**的（只接受明确允许的结论），并且拿不到/解析不了输入时 **fail closed**：
"没数据"永远不等于"通过"。

用法（由 `.github/workflows/ci.yml` 的 gate 作业调用）：

    python scripts/ci_merge_gate.py --needs-json /tmp/needs.json --code true

`--needs-json` 收 `${{ toJSON(needs) }}` 的结果；`--code` 是 scope 作业的 `outputs.code`。
"""

from __future__ import annotations

import argparse
import json
import sys

# 只有这些结论算"跑过且通过"。其余（failure / cancelled / timed_out /
# action_required / neutral / 空串 / 任何新出现的状态）一律算不通过。
PASSING_RESULTS = frozenset({"success"})
# docs-only（scope 判 code=false）时，重活被 `if:` 跳过是设计如此，算满足。
SKIPPABLE_RESULTS = frozenset({"skipped"})


def _is_hard_failure(result: object) -> bool:
    """非空、且既不是 success 也不是 skipped —— 即"真的挂了"。"""
    return (
        isinstance(result, str)
        and bool(result)
        and result not in PASSING_RESULTS
        and result not in SKIPPABLE_RESULTS
    )


def evaluate(needs: dict, code: str) -> list[str]:
    """返回不通过原因列表；空列表表示放行。

    `needs` 形如 `{"lint": {"result": "success"}, "test": {"result": "skipped"}, ...}`。
    本函数不读环境变量也不打印，纯粹便于被用例逐条钉住。

    **级联跳过不背锅**（2026-10-04 修）：作业挂在 `needs:` 链上时，上游一挂它就
    是 `skipped`。run 476 实测：`test` 失败 ⇒ `needs: test` 的 `audit`/`sbom` 被
    连带跳过，旧实现把它们也报成"该跑的作业没跑"——判定没错但**归错了因**，看
    注解的人会去查 sbom 而放过真正的红点。现在：只要这轮里存在硬失败，strict 面
    下的 skipped 就不再单独列为不通过原因（红点已经由那个失败给出），改由
    `cascade_skips()` 作为提示说明。
    """
    if not isinstance(needs, dict) or not needs:
        return ["needs 输入为空或形状不对 —— 无法判断上游是否跑过（fail closed）"]

    strict = code.strip().lower() != "false"  # 只有明确的 false 才允许 skipped
    hard_failure = any(
        _is_hard_failure((m or {}).get("result") if isinstance(m, dict) else None)
        for m in needs.values()
    )
    offenders: list[str] = []
    for job_id, meta in sorted(needs.items()):
        result = meta.get("result") if isinstance(meta, dict) else None
        if not isinstance(result, str) or not result:
            offenders.append(f"{job_id}: 没有 result 字段（拿不到结论不算通过）")
            continue
        if result in PASSING_RESULTS:
            continue
        if not strict and result in SKIPPABLE_RESULTS:
            continue
        if strict and result in SKIPPABLE_RESULTS:
            if hard_failure:
                # 上游有硬失败 ⇒ 它是被连带的；真正的红点由那个失败报出。
                continue
            offenders.append(
                f"{job_id}: 被判成代码变更（code={code}）却处于 skipped —— "
                f"该跑的作业没跑，等于没有门禁"
            )
            continue
        offenders.append(f"{job_id}: result={result}")
    return offenders


def cascade_skips(needs: dict, code: str) -> list[str]:
    """strict 面下"因上游硬失败而没执行"的作业，**只用于提示、不参与判定**。

    与 `evaluate()` 的分工：`evaluate()` 决定红绿，本函数只解释"为什么这些作业
    没出现在失败清单里"。分开是为了让"判定"与"措辞"各自可被单独钉住——把两者
    混在一个返回值里，正是上一版把级联跳过错报成"该跑没跑"的原因。
    """
    if code.strip().lower() == "false":
        return []
    if not isinstance(needs, dict) or not needs:
        return []
    if not any(
        _is_hard_failure((m or {}).get("result") if isinstance(m, dict) else None)
        for m in needs.values()
    ):
        return []
    return [
        f"{job_id}: 未执行（上游失败导致级联跳过，不计入失败）"
        for job_id, meta in sorted(needs.items())
        if isinstance(meta, dict) and meta.get("result") in SKIPPABLE_RESULTS
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--needs-json", required=True, help="${{ toJSON(needs) }} 落地的文件路径")
    ap.add_argument("--code", default="", help="scope 作业的 outputs.code（true/false）")
    args = ap.parse_args(argv)

    try:
        with open(args.needs_json, encoding="utf-8") as fh:
            needs = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"::error title=CI merge gate::读取 needs 失败：{exc}")
        print("FAIL: 拿不到上游结论，按不通过处理（fail closed）")
        return 1

    offenders = evaluate(needs, args.code)
    results = {k: (v.get("result") if isinstance(v, dict) else None) for k, v in needs.items()}
    print(f"CI merge gate: code={args.code or 'n/a'} 上游结论={results}")
    for note in cascade_skips(needs, args.code):
        print(f"::notice title=CI merge gate::{note}")
    if offenders:
        for reason in offenders:
            print(f"::error title=CI merge gate::{reason}")
        print(f"FAIL: {len(offenders)} 个上游作业未达门槛")
        return 1
    print("OK: 全部上游作业通过（或按 docs-only 规则合法跳过）")
    return 0


if __name__ == "__main__":
    # 中文摘要在非 UTF-8 控制台（en-US runner 的 cp1252）会直接抛 UnicodeEncodeError，
    # 把判定崩成 exit=1 —— 本仓在 Windows 腿上真栽过一次（见 ci_path_scope.py 同款处理）。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass  # 极端环境（stdout 已被换成无 reconfigure 的对象）下不因此改变判定
    raise SystemExit(main())
