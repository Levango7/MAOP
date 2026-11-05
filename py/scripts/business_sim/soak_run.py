"""M-SIM M1 —— 48h soak harness（长杆先行）。

复用 M0 的生产路径（``run_sim.run_scenario``：``maop.cli run`` 子进程 +
确定性 stub）做**长时串联**：持续运行三业务剧本、逐轮 JSONL 落盘、
周期性 DB 完整性检查、对账硬门禁逐轮把关。目的：抓「跑得久才暴露」的
集成缝隙——资源累积、句柄/WAL 增长、间歇性失败、对账漂移。

设计约束（M1 章程 + 本机长跑现实）：
- **持久 sim home**：跨轮不重建（累积效应才是 soak 的观察对象）；
- **进度即时落盘**：每轮一行 JSONL，进程/机器中断后按已有轮数**续跑**
  （部分结果可用，不等 48h 全程）；
- **优雅停**：``<out-dir>/soak-stop.marker`` 存在即停（人工止损通道）；
- **快速止损**：连续 N 轮失败（默认 3）即停——带病跑完 48h 没有价值；
- **零 CI 占用**：纯本地，不占账号 job 配额。

Usage (cwd = ``py/``)::

    python -m scripts.business_sim.soak_run --hours 48 --out-dir .sim-soak
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from scripts.business_sim import run_sim

#: 单轮之间的小睡（秒）。48h / 120s ≈ 1440 轮，远超「跑得久」判据所需。
DEFAULT_ROUND_INTERVAL_S = 120
DEFAULT_MAX_CONSECUTIVE_FAILURES = 3
PROGRESS_FILENAME = "soak-progress.jsonl"
STOP_MARKER = "soak-stop.marker"


def _load_completed_rounds(out_dir: Path) -> int:
    """续跑：已有 JSONL 的行数 = 已完成轮数（坏行容忍：计数可解析行）。"""
    progress = out_dir / PROGRESS_FILENAME
    if not progress.is_file():
        return 0
    done = 0
    for line in progress.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            json.loads(line)
            done += 1
        except json.JSONDecodeError:
            continue
    return done


def _append_progress(out_dir: Path, record: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / PROGRESS_FILENAME).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _db_integrity(home: Path) -> list[dict[str, str]]:
    """对 sim home 内全部 sqlite 文件做 PRAGMA integrity_check（周期健康）。"""
    results: list[dict[str, str]] = []
    for db in sorted(home.rglob("*.db")):
        try:
            with sqlite3.connect(str(db)) as conn:
                status = conn.execute("PRAGMA integrity_check").fetchone()[0]
            results.append({"file": db.name, "integrity": status})
        except sqlite3.Error as exc:
            results.append({"file": db.name, "integrity": "error: " + type(exc).__name__})
    return results


def run_soak(
    hours: float,
    out_dir: Path,
    round_interval_s: int = DEFAULT_ROUND_INTERVAL_S,
    max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
    round_id_offset: int | None = None,
) -> dict[str, Any]:
    """跑 soak 主循环直到时长到 / stop-file / 连续失败熔断。可测的纯编排层。"""
    # soak_run.py 位于 py/scripts/business_sim/ 下两层——py 目录是 parents[2]
    # （parents[1]=scripts 是 M0 实撞的地雷：子进程 cwd 落 scripts 后经
    # site-packages .pth 解析到主工作树旧 cli，报 unrecognized arguments）。
    py_dir = Path(__file__).resolve().parents[2]
    base = out_dir
    # build_sim_home 返回的是 base/sim-home（内层），M0 测试用的就是返回值；
    # 幂等（mkdir exist_ok + yaml 重写），data/ 累积不受影响。
    home = run_sim.build_sim_home(base)
    start_round = round_id_offset if round_id_offset is not None else _load_completed_rounds(base)
    deadline = time.monotonic() + hours * 3600
    scenario_names = list(run_sim.SCENARIOS)
    consecutive_failures = 0
    last_integrity_ts = 0.0
    rounds_ok = 0

    while time.monotonic() < deadline:
        if (base / STOP_MARKER).exists():
            break
        round_id = start_round + rounds_ok
        name = scenario_names[round_id % len(scenario_names)]
        t0 = time.monotonic()
        try:
            # 持久 home 的成本表跨轮累积——soak 的对账口径是**增量**：
            # 当轮 attribution 总量差值 == 当轮协议行总量（全量口径在
            # 第 2 轮起必然失配，非缺陷而是口径选择）。
            pre_cost = run_sim.collect_cost_evidence(home, py_dir)
            result = run_sim.run_scenario(home, py_dir, name)
            post_cost = run_sim.collect_cost_evidence(home, py_dir)
            protocol = 0
            for r in result.get("runs", []):
                if r.get("status") == "success" and r.get("usage"):
                    protocol += r["usage"].get("tokens", 0)

            def _attr_tokens(c: dict[str, Any]) -> int:
                total = c.get("attribution_total", {})
                return int(total.get("tokens", 0) or 0) if isinstance(total, dict) else 0

            delta = _attr_tokens(post_cost) - _attr_tokens(pre_cost)
            recon = {
                "protocol_line_tokens": protocol,
                "cost_table_tokens": delta,
                "match": protocol > 0 and protocol == delta,
            }
            ok = result["failed"] == 0 and recon["match"]
        except Exception as exc:  # noqa: BLE001 —— soak 轮级故障是证据，不是崩溃理由
            result = {"scenario": name, "failed": -1, "executions": 0,
                      "success": 0, "error": type(exc).__name__ + ": " + str(exc)[:200]}
            recon = {"match": False, "protocol_line_tokens": 0, "cost_table_tokens": 0}
            ok = False
        duration_ms = int((time.monotonic() - t0) * 1000)
        consecutive_failures = 0 if ok else consecutive_failures + 1
        if ok:
            rounds_ok += 1
        # 失败轮带首个失败节点的 stderr 尾巴（soak 诊断证据，M0 教训：
        # 只有摘要计数没有 stderr 的失败记录无法事后归因）。
        diag = ""
        if not ok:
            for r in result.get("runs", []):
                if r.get("status") != "success":
                    diag = (r.get("stderr_tail") or "")[-200:]
                    break
        record: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "round": round_id,
            "scenario": name,
            # 异常路径（failed=-1）的 error 文本一并落盘——2026-11-06 第 541 轮
            # （机器休眠超时）只有 failed=-1 无 error，事后只能靠时长反推。
            "error": result.get("error", ""),
            "ok": ok,
            "success": result.get("success", 0),
            "failed": result.get("failed", -1),
            "recon": recon,
            "duration_ms": duration_ms,
        }
        if diag:
            record["first_fail_stderr"] = diag
        # 每小时顺带做一次 DB 完整性检查（落在整点附近的轮上）
        if time.time() - last_integrity_ts >= 3600:
            record["integrity"] = _db_integrity(home)
            last_integrity_ts = time.time()
        _append_progress(base, record)
        if consecutive_failures >= max_consecutive_failures:
            _append_progress(base, {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "round": round_id, "scenario": name,
                "ok": False, "circuit_break": True,
                "consecutive_failures": consecutive_failures,
            })
            break
        time.sleep(round_interval_s)

    stopped_by = "deadline"
    if (base / STOP_MARKER).exists():
        stopped_by = "stop-marker"
    elif consecutive_failures >= max_consecutive_failures:
        stopped_by = "circuit-break"
    return {
        "completed_rounds": start_round + rounds_ok,
        "rounds_ok_this_run": rounds_ok,
        "stopped_by": stopped_by,
        "home": str(home),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M-SIM M1 soak harness")
    parser.add_argument("--hours", type=float, default=48.0)
    parser.add_argument("--out-dir", default=".sim-soak",
                        help="soak 工作目录（进度 JSONL + 持久 home）")
    parser.add_argument("--round-interval-s", type=int,
                        default=DEFAULT_ROUND_INTERVAL_S)
    parser.add_argument("--max-consecutive-failures", type=int,
                        default=DEFAULT_MAX_CONSECUTIVE_FAILURES)
    ns = parser.parse_args(argv)
    summary = run_soak(
        hours=ns.hours,
        out_dir=Path(ns.out_dir),
        round_interval_s=ns.round_interval_s,
        max_consecutive_failures=ns.max_consecutive_failures,
    )
    (Path(ns.out_dir) / "soak-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("[soak] stopped_by=" + summary["stopped_by"]
          + " rounds_ok=" + str(summary["rounds_ok_this_run"])
          + " total=" + str(summary["completed_rounds"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
