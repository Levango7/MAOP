"""M-SIM M0 business-scenario rehearsal harness (ROADMAP charter 2026-11-04).

Runs three business-shaped scenarios through the REAL production path
(``python -m maop.cli run`` subprocess, same invocation shape as the
operator / Temporal executor) with a deterministic stub driver (no external
LLM, no network), then collects ops evidence:

- per-node ``[USAGE] turns/tokens/source`` protocol line (M3.1/M3.3 contract);
- cost_tracker persistence + M-X3 ``attribution_summary`` alignment;
- alert_ingest record / read-back / count;
- db_backup backup + restore openability (integrity_check).

The rehearsal exists to FIND GAPS: exposed integration gaps are recorded
honestly in the report's GAPS section.

Usage (cwd = ``py/``)::

    python -m scripts.business_sim.run_sim --out <absolute path inside repo>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

USAGE_RE = re.compile(r"\[USAGE\] turns=(\d+) tokens=(\d+) source=(\w+)")

SCENARIOS: dict[str, dict[str, Any]] = {
    "ticket_triage": {
        "desc": "ticket triage pipeline: triage -> resolve -> receipt",
        "nodes": [
            {"id": "triage", "agent": "sim-triage",
             "task": "工单 1042 分诊：登录令牌失效", "depends_on": []},
            {"id": "resolve", "agent": "sim-worker",
             "task": "工单 1042 处置：重置会话并校验刷新链", "depends_on": ["triage"]},
            {"id": "receipt", "agent": "sim-worker",
             "task": "工单 1042 回执：发送处置结果摘要", "depends_on": ["resolve"]},
        ],
    },
    "daily_report": {
        "desc": "daily report pipeline: collect -> aggregate -> draft -> verify",
        "nodes": [
            {"id": "collect", "agent": "sim-worker",
             "task": "日报采集：昨日委派与成本原始记录", "depends_on": []},
            {"id": "aggregate", "agent": "sim-worker",
             "task": "日报聚合：按 agent/phase/model 汇总", "depends_on": ["collect"]},
            {"id": "draft", "agent": "sim-reporter",
             "task": "日报撰写：管理层摘要段落", "depends_on": ["aggregate"]},
            {"id": "verify", "agent": "sim-reviewer",
             "task": "日报校验：与原始记录逐条对齐", "depends_on": ["draft"]},
        ],
    },
    "code_review": {
        "desc": "code review pipeline with fan-in: lint -> {scan, review} -> summarize",
        "nodes": [
            {"id": "lint", "agent": "sim-worker",
             "task": "审查前置：静态检查与格式门", "depends_on": []},
            {"id": "scan", "agent": "sim-worker",
             "task": "安全扫描：密钥与注入面复核", "depends_on": ["lint"]},
            {"id": "review", "agent": "sim-reviewer",
             "task": "审查批注：diff 逐段批注", "depends_on": ["lint"]},
            {"id": "summarize", "agent": "sim-reporter",
             "task": "审查汇总：合并扫描与批注", "depends_on": ["scan", "review"]},
        ],
    },
}

STUB = Path(__file__).with_name("stub_business_agent.py")

# 当前演练 home（main() 里赋值）；alert 采集按它钉 MAOP_ROOT_DIR。
_CURRENT_HOME = ""


def build_sim_home(base: Path) -> Path:
    """Create an isolated MAOP home (config/agents.yaml + data/)."""
    home = (base / "sim-home").resolve()
    (home / "config").mkdir(parents=True, exist_ok=True)
    (home / "data").mkdir(parents=True, exist_ok=True)
    stub = str(STUB).replace("\\", "/")
    py = str(sys.executable).replace("\\", "/")
    blocks: list[str] = []
    for name, role in (
        ("sim-triage", "triage"),
        ("sim-worker", "worker"),
        ("sim-reporter", "reporter"),
        ("sim-reviewer", "reviewer"),
    ):
        # 注意：YAML 标量不能以引号开头再接裸文本（``"p" --x`` 非法）；
        # 本 stub/解释器路径均无空格，裸写即可（仓内 agents.yaml 同款风格）。
        block = (
            "  " + name + ":\n"
            "    capabilities: [chat, planning]\n"
            "    cli: " + py + "\n"
            "    cli_args: " + stub + " --role " + role + ' --task "{task}"\n'
            "    description: M-SIM deterministic stub (" + role + ")\n"
            "    driver: cli\n"
            "    model: auto\n"
            "    timeout_s: 120\n"
        )
        blocks.append(block)
    (home / "config" / "agents.yaml").write_text(
        "agents:\n" + "\n".join(blocks), encoding="utf-8"
    )
    return home


def child_env(home: Path, py_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["MAOP_ROOT_DIR"] = str(home)
    env["PYTHONPATH"] = str(py_dir)
    return env


def run_node(home: Path, py_dir: Path, agent: str, task: str, timeout_s: int = 180) -> dict:
    """Execute one node through the production path: maop.cli run.

    Invocation note: launched via ``python -c "import runpy; runpy.run_module(
    'maop.cli', run_name='__main__')"`` + argv tail — semantically identical
    to ``python -m maop.cli`` (same module, same ``__main__``). On this dev
    machine the bare ``-m`` shape intermittently resolved an argparse without
    the ``--agent`` flag (see evidence report, environment-anomaly gap);
    the runpy shape is deterministic, so the harness uses it.
    """
    wrapper = "import sys, runpy; runpy.run_module('maop.cli', run_name='__main__')"
    t0 = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-c", wrapper, "run", "--agent", agent, "--task", task],
        cwd=str(py_dir),
        env=child_env(home, py_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
        check=False,  # PLW1510: 返回码是证据的一部分，由调用方判定
    )
    duration_ms = int((time.monotonic() - t0) * 1000)
    usage = None
    matches = USAGE_RE.findall(proc.stdout or "")
    if matches:
        # 子进程 stdout 上有两条 [USAGE]：loop 层先行一条（tokens=0，provider
        # 无计费时）+ stub 协议行（真实计量的来源）。取最后一条 = 终态权威值。
        turns, tokens, source = matches[-1]
        usage = {"turns": int(turns), "tokens": int(tokens), "source": source}
    return {
        "agent": agent,
        "task": task,
        "exit_code": proc.returncode,
        "status": "success" if proc.returncode == 0 else "failed",
        "usage": usage,
        "duration_ms": duration_ms,
        "stdout_tail": (proc.stdout or "")[-300:],
        "stderr_tail": (proc.stderr or "")[-300:],
    }


def topo_order(nodes):
    """Wave-wise Kahn ordering (harness-side for M0; cross-backend scheduling is M1/E2-b)."""
    done = set()
    waves = []
    pending = list(nodes)
    while pending:
        wave = [n for n in pending if all(d in done for d in n["depends_on"])]
        if not wave:
            raise ValueError("dependency cycle in scenario nodes")
        waves.append(wave)
        for n in wave:
            done.add(n["id"])
        pending = [n for n in pending if n not in wave]
    return waves


def run_scenario(home, py_dir, name, rounds=1):
    sc = SCENARIOS[name]
    waves = topo_order(sc["nodes"])
    runs = []
    for r in range(rounds):
        for wave in waves:
            for node in wave:
                rec = run_node(home, py_dir, node["agent"], node["task"])
                rec["scenario"] = name
                rec["node"] = node["id"]
                rec["round"] = r + 1
                runs.append(rec)
    ok = sum(1 for r in runs if r["status"] == "success")
    return {
        "scenario": name,
        "desc": sc["desc"],
        "nodes": len(sc["nodes"]),
        "rounds": rounds,
        "executions": len(runs),
        "success": ok,
        "failed": len(runs) - ok,
        "runs": runs,
    }


def collect_cost_evidence(home, py_dir):
    """cost_tracker persistence + M-X3 attribution alignment, scoped to the sim home."""
    old_root = os.environ.get("MAOP_ROOT_DIR")
    os.environ["MAOP_ROOT_DIR"] = str(home)
    info = {}
    try:
        sys.path.insert(0, str(py_dir))
        from maop.core.backends.db_utils import get_db_path

        db = get_db_path("cost_tracker")
        info["db_path"] = str(db)
        info["exists"] = db.exists()
        if db.exists():
            with sqlite3.connect(str(db)) as conn:
                tables = [
                    r[0]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                ]
            info["tables"] = tables
            from maop.core.cost_attribution import attribution_summary
            from maop.core.cost_tracker import CostTracker

            summary = attribution_summary(tracker=CostTracker())
            info["attribution_total"] = summary.get("total", {})
        return info
    except Exception as exc:
        info["error"] = type(exc).__name__ + ": " + str(exc)
        return info
    finally:
        if str(py_dir) in sys.path:
            sys.path.remove(str(py_dir))
        if old_root is None:
            os.environ.pop("MAOP_ROOT_DIR", None)
        else:
            os.environ["MAOP_ROOT_DIR"] = old_root


def collect_alert_evidence():
    """alert_ingest: write one synthetic budget alert, read it back, count it.

    与 cost 采集同款：先按 sim home 钉 MAOP_ROOT_DIR 再调模块 API——
    alert 表经 ``get_db_path("alert_ingest", legacy_fallback="maop")``
    落在 root 的 data/maop.db 内，不钉就会写到调用方自己的 home。
    """
    old_root = os.environ.get("MAOP_ROOT_DIR")
    os.environ["MAOP_ROOT_DIR"] = str(_CURRENT_HOME)
    try:
        from maop.core.alert_ingest import count_alerts, list_alerts, record_alerts

        before = count_alerts()
        record_alerts(
            [
                {
                    "name": "BusinessSimCostBudget",
                    "severity": "warning",
                    "summary": "M-SIM synthetic cost budget alert",
                    "labels": {"source": "business-sim"},
                }
            ],
            receiver="business-sim",
        )
        rows = list_alerts(limit=5)
        return {
            "count_before": before,
            "count_after": count_alerts(),
            "persisted": any("business-sim" in str(r) for r in rows),
        }
    except Exception as exc:
        return {"error": type(exc).__name__ + ": " + str(exc)}
    finally:
        if old_root is None:
            os.environ.pop("MAOP_ROOT_DIR", None)
        else:
            os.environ["MAOP_ROOT_DIR"] = old_root


def collect_backup_evidence(home):
    """db_backup: run a backup, then integrity_check the newest .bak files."""
    try:
        from maop.core.backends.db_backup import DbBackup

        bm = DbBackup(home)
        entries = bm.run()
        names = [getattr(e, "name", str(e)) for e in entries]
        backups_dir = home / "data" / "backups"
        files = sorted(backups_dir.glob("*.bak")) if backups_dir.exists() else []
        checks = []
        for f in files[-3:]:
            with sqlite3.connect(str(f)) as conn:
                result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            checks.append({"file": f.name, "integrity": result})
        return {"backups": names, "integrity_checks": checks}
    except Exception as exc:
        return {"error": type(exc).__name__ + ": " + str(exc)}


def gaps_from_evidence(scenarios, cost):
    """Extract gaps from evidence (GAP-1 is code-confirmed before any run)."""
    gaps = [
        (
            "GAP-1 (code-confirmed): wiring.run_workflow legacy backend lands in "
            "maop_plan.execute_workflow which ONLY plans and never executes any agent "
            "(maop_plan.py:429 docstring admits the name is a misnomer). Real execution "
            "exists only via Dispatcher.dispatch / maop run / Temporal backend; the "
            "cross-backend contract gap must be closed in E2-b."
        ),
    ]
    tokens_from_usage = 0
    for sc in scenarios:
        for r in sc["runs"]:
            if r["status"] == "success" and r.get("usage"):
                tokens_from_usage += r["usage"].get("tokens", 0)
    attr = cost.get("attribution_total", {})
    if isinstance(attr, dict) and attr:
        attr_tokens = int(attr.get("tokens", 0) or 0)
        if attr_tokens != tokens_from_usage:
            gaps.append(
                "GAP-2 (measured): M-X3 attribution tokens=" + str(attr_tokens)
                + " != protocol-line tokens=" + str(tokens_from_usage)
                + " -- cost attribution vs execution-path metering mismatch."
            )
    else:
        gaps.append(
            "GAP-2 (measured): cost_tracker persistence/aggregation unavailable or "
            "empty (see cost evidence section) -- CostTracker wiring on the plain "
            "maop-run path needs confirmation."
        )
    return gaps + [
        (
            "GAP-3 (environment, tooling): 子进程解析 maop 的两颗地雷——"
            "(a) py/scripts/business_sim/run_sim.py 曾用 parents[1] 取 py 目录"
            "（差一层到 scripts/），(b) 本机 site-packages 存在把 F:\\Nexus\\MAOP\\py"
            "（主工作树旧分支）注入 sys.path 的 .pth。两者叠加时子进程解析到"
            "旧分支 cli（无 --agent），报 unrecognized arguments。已在 harness 修为 "
            "parents[2] 并以 runpy 包装确定性启动；**任何按 repo 约定从脚本目录"
            "spawn maop 子进程的工具都有同款风险**。"
        ),
    ]


def write_report(out: Path, evidence: dict) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# M-SIM business-scenario rehearsal evidence (M0)",
        "",
        "- generated: " + evidence["meta"]["generated_at"],
        "- exec path: python -m maop.cli run (production entry, same shape as operator/Temporal executor)",
        "- driver: deterministic stub, no external LLM/network, MAOP_ROOT_DIR=" + evidence["meta"]["sim_home"],
        "",
        "## scenario results",
        "",
        "| scenario | nodes | rounds | exec | success | failed |",
        "|---|---|---|---|---|---|",
    ]
    for sc in evidence["scenarios"]:
        lines.append(
            "| " + sc["scenario"] + " | " + str(sc["nodes"]) + " | " + str(sc["rounds"])
            + " | " + str(sc["executions"]) + " | " + str(sc["success"]) + " | " + str(sc["failed"]) + " |"
        )
    lines += ["", "## node details", "",
              "| scenario | node | round | agent | status | turns | tokens | source | ms |",
              "|---|---|---|---|---|---|---|---|---|"]
    for sc in evidence["scenarios"]:
        for r in sc["runs"]:
            u = r.get("usage") or {}
            lines.append(
                "| " + r["scenario"] + " | " + r["node"] + " | " + str(r["round"]) + " | "
                + r["agent"] + " | " + r["status"] + " | "
                + str(u.get("turns", "-")) + " | " + str(u.get("tokens", "-")) + " | "
                + str(u.get("source", "-")) + " | " + str(r["duration_ms"]) + " |"
            )
    for key, title in (
        ("cost", "cost reconciliation (M-X3)"),
        ("alerts", "alert channel (alert_ingest)"),
        ("backup", "backup and restore (db_backup)"),
    ):
        lines += [
            "",
            "## " + title,
            "",
            "```json",
            json.dumps(evidence[key], ensure_ascii=False, indent=2)[:2000],
            "```",
            "",
        ]
    lines += ["## exposed gaps", ""]
    for g in evidence["gaps"]:
        lines.append("- " + g)
    lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="M-SIM business scenario rehearsal")
    parser.add_argument("--out", required=True, help="report path (JSON written alongside)")
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--workdir", default="", help="base dir for the isolated sim home")
    ns = parser.parse_args(argv)

    # run_sim.py 位于 py/scripts/business_sim/ —— 仓库 py 目录是其上两级
    # （parents[1] 是 scripts/，会让子进程掉进 site-packages 的 .pth 指向的
    # 旧工作树 maop —— 本次演练实测踩中，见证据报告环境异常段）。
    py_dir = Path(__file__).resolve().parents[2]
    base = Path(ns.workdir) if ns.workdir else py_dir / ".sim"
    if base == py_dir / ".sim" and base.exists():
        shutil.rmtree(base, ignore_errors=True)
    home = build_sim_home(base)
    global _CURRENT_HOME
    _CURRENT_HOME = str(home)

    scenarios = [run_scenario(home, py_dir, name, rounds=ns.rounds) for name in SCENARIOS]
    evidence = {
        "meta": {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "sim_home": str(home),
            "backend": "production dispatch via maop run (stub driver)",
        },
        "scenarios": scenarios,
        "cost": collect_cost_evidence(home, py_dir),
        "alerts": collect_alert_evidence(),
        "backup": collect_backup_evidence(home),
    }
    evidence["gaps"] = gaps_from_evidence(scenarios, evidence["cost"])

    out = Path(ns.out)
    write_report(out, evidence)
    out.with_suffix(".json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    total_ok = sum(s["success"] for s in scenarios)
    total = sum(s["executions"] for s in scenarios)
    print("[business-sim] " + str(total_ok) + "/" + str(total) + " node-executions success; report -> " + str(out))
    return 0 if total_ok == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
