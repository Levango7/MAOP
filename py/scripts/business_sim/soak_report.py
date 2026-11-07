"""M-SIM M1 soak 证据报告生成器（把逐轮 JSONL 汇总成可审阅的 Markdown）。

用法（cwd = ``py/``）::

    python -m scripts.business_sim.soak_report \
        --progress .sim-soak/soak-progress.jsonl \
        --out ../docs/audits/business-sim-soak-evidence.md

汇总内容：轮次/成功率/按剧本分布/失败轮明细（含 error 与 stderr 尾巴）/
逐轮 token 对账（协议行 vs 成本表，match 率）/每小时轮次分布/最长相邻轮间隔
（机器休眠会在这里显形）/完整性检查结果/墙钟跨度与有效运行跨度。
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def load_records(progress: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not progress.is_file():
        return records
    for line in progress.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    rounds = [r for r in records if "round" in r]
    ok_rounds = [r for r in rounds if r.get("ok")]
    failures = [r for r in rounds if not r.get("ok")]
    by_scenario: dict[str, dict[str, int]] = {}
    for r in rounds:
        name = str(r.get("scenario", "?"))
        slot = by_scenario.setdefault(name, {"rounds": 0, "ok": 0})
        slot["rounds"] += 1
        slot["ok"] += 1 if r.get("ok") else 0
    recon_match = 0
    recon_total = 0
    protocol_tokens = 0
    cost_tokens = 0
    for r in rounds:
        recon = r.get("recon")
        if not isinstance(recon, dict):
            continue
        recon_total += 1
        protocol_tokens += int(recon.get("protocol_line_tokens", 0) or 0)
        cost_tokens += int(recon.get("cost_table_tokens", 0) or 0)
        if recon.get("match"):
            recon_match += 1
    stamps = [t for t in (_parse_ts(str(r.get("ts", ""))) for r in rounds) if t]
    wall_span_s = int((stamps[-1] - stamps[0]).total_seconds()) if len(stamps) >= 2 else 0
    gaps = [
        int((stamps[i + 1] - stamps[i]).total_seconds())
        for i in range(len(stamps) - 1)
    ]
    hourly: dict[str, int] = {}
    for t in stamps:
        hourly[t.strftime("%Y-%m-%d %H")] = hourly.get(t.strftime("%Y-%m-%d %H"), 0) + 1
    integrity = [r.get("integrity") for r in rounds if r.get("integrity")]
    return {
        "rounds": len(rounds),
        "ok": len(ok_rounds),
        "failed": len(failures),
        "success_rate": round(len(ok_rounds) / len(rounds), 4) if rounds else 0.0,
        "by_scenario": by_scenario,
        "recon_total": recon_total,
        "recon_match": recon_match,
        "protocol_tokens": protocol_tokens,
        "cost_tokens": cost_tokens,
        "first_ts": stamps[0].isoformat(sep=" ") if stamps else "",
        "last_ts": stamps[-1].isoformat(sep=" ") if stamps else "",
        "wall_span_hours": round(wall_span_s / 3600, 2),
        "max_gap_minutes": round(max(gaps) / 60, 1) if gaps else 0.0,
        "hourly": hourly,
        "integrity_checks": integrity,
        "failures": failures,
    }


def render_markdown(summary: dict[str, Any], progress: Path) -> str:
    lines = [
        "# M-SIM M1 48h soak 证据",
        "",
        "> 生成器：`py/scripts/business_sim/soak_report.py`；原始逐轮数据：",
        f"> `{progress}`（每轮一行 JSONL，断点续跑幂等）。",
        "",
        "## 总览",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| 总轮次 | {summary['rounds']} |",
        f"| 成功轮次 | {summary['ok']} |",
        f"| 失败轮次 | {summary['failed']} |",
        f"| 成功率 | {summary['success_rate'] * 100:.2f}% |",
        f"| 对账轮次（协议行 vs 成本表） | {summary['recon_match']}/{summary['recon_total']} match |",
        f"| 协议行 token 合计 | {summary['protocol_tokens']} |",
        f"| 成本表 token 合计 | {summary['cost_tokens']} |",
        f"| 首轮时间 | {summary['first_ts']} |",
        f"| 末轮时间 | {summary['last_ts']} |",
        f"| 墙钟跨度 | {summary['wall_span_hours']} h |",
        f"| 最长相邻轮间隔（机器休眠会显形） | {summary['max_gap_minutes']} min |",
        "",
        "## 按剧本分布",
        "",
        "| 剧本 | 轮次 | 成功 |",
        "|---|---|---|",
    ]
    for name, slot in sorted(summary["by_scenario"].items()):
        lines.append(f"| {name} | {slot['rounds']} | {slot['ok']} |")
    lines += ["", "## 每小时轮次分布", "", "| 小时 | 轮次 |", "|---|---|"]
    for hour, count in sorted(summary["hourly"].items()):
        lines.append(f"| {hour} | {count} |")
    failures = summary["failures"]
    lines += ["", f"## 失败轮明细（{len(failures)}）", ""]
    if not failures:
        lines.append("无失败轮。")
    else:
        lines += ["| 轮次 | 时间 | 剧本 | 失败数 | error | stderr 尾巴 |",
                  "|---|---|---|---|---|---|"]
        for r in failures:
            stderr = (r.get("first_fail_stderr") or "").replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {r.get('round')} | {r.get('ts')} | {r.get('scenario')} | "
                f"{r.get('failed')} | {(r.get('error') or '').replace('|', '\\|')[:120]} | {stderr[:160]} |"
            )
    integrity = summary["integrity_checks"]
    lines += ["", f"## DB 完整性检查（{len(integrity)} 次，每小时一轮）", ""]
    if not integrity:
        lines.append("本轮运行未采集完整性检查结果。")
    else:
        bad = []
        for check in integrity:
            for item in check or []:
                if str(item.get("integrity", "")).lower() != "ok":
                    bad.append(item)
        lines.append(f"检查轮次 {len(integrity)}，异常条目 {len(bad)}。")
        for item in bad:
            lines.append(f"- `{item.get('file')}`: {item.get('integrity')}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M-SIM soak evidence report")
    parser.add_argument("--progress", default=".sim-soak/soak-progress.jsonl")
    parser.add_argument("--out", default="../docs/audits/business-sim-soak-evidence.md")
    ns = parser.parse_args(argv)
    progress = Path(ns.progress)
    records = load_records(progress)
    summary = summarize(records)
    out = Path(ns.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_markdown(summary, progress), encoding="utf-8")
    print(
        f"[soak-report] rounds={summary['rounds']} ok={summary['ok']} "
        f"recon={summary['recon_match']}/{summary['recon_total']} -> {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
