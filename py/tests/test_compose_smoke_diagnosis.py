"""compose_smoke.py 的"红要带病历"单测（2026-10-16，主干 run 38867035723 的零诊断红换来的）。

这里不需要 docker：被测的是**失败路径的取证质量**，全部用假的 subprocess/runner 喂进去。
钉住的三件事，一件比一件容易在重构里悄悄丢：

1. 超时不能抛出去就走 —— 必须折成 rc=124 并保住掐断前的部分输出（原来 `TimeoutExpired`
   直接冒出 `sh()`，`capture_output` 收到的 compose 输出整个丢掉，作业只剩一句 traceback）；
2. 诊断要覆盖**每个服务**并用 `ps -a`（只 dump dashboard 时，pg/redis 卡住等于没有病历；
   不带 `-a` 时已退出的容器根本不出现在清单里）；
3. 诊断命令自己必须有上限，且上限必须显著小于被诊断那步的上限 —— 否则"取病历"能把作业
   再拖死一遍，等于把一个看得见的红换成一个看不见的红。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "py" / "scripts" / "compose_smoke.py"

sys.path.insert(0, str(SCRIPT.parent))
import compose_smoke as cs


def _done(rc: int = 0, out: str = "", err: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["docker", "compose"], rc, stdout=out, stderr=err)


class _Runner:
    """假 compose 调用器：记录每次调用的参数与超时，按命令前缀给回预设输出。

    回复值可以是 `CompletedProcess`，也可以是 `args -> CompletedProcess`（让每个服务
    给出**不同**的日志 —— 全都一样的话，正好会被折叠逻辑吃掉，测不出"逐服务都打了"）。
    """

    def __init__(self, replies: dict[str, object]) -> None:
        self.replies = replies
        self.calls: list[tuple[list[str], int]] = []

    def __call__(self, args: list[str], timeout: int = cs.UP_TIMEOUT_S) -> subprocess.CompletedProcess:
        self.calls.append((args, timeout))
        for key, reply in self.replies.items():
            if args and args[0] == key:
                return reply(args) if callable(reply) else reply  # type: ignore[operator]
        return _done()


# ── 超时折成结论，而不是折成 traceback ───────────────────────────────


def test_sh_converts_timeout_to_rc124_and_keeps_partial_output(monkeypatch) -> None:
    """这条就是那个红本身：超时后必须拿到"rc=124 + 掐断前的部分输出"，而不是异常。"""

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd=["docker", "compose"], timeout=1800, output=b"pulling pg\n")

    monkeypatch.setattr(cs.subprocess, "run", boom)
    up = cs.sh(["up", "-d", "--build"])
    assert up.returncode == cs.RC_TIMEOUT
    assert "pulling pg" in up.stdout, "掐断前已捕获的输出必须保住，否则又变成零诊断红"


def test_sh_keeps_partial_stderr_on_timeout(monkeypatch) -> None:
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd=["x"], timeout=1, stderr=b"no space left on device")

    monkeypatch.setattr(cs.subprocess, "run", boom)
    up = cs.sh(["up"])
    assert "no space left on device" in up.stderr


def test_sh_passes_through_normal_results_and_coerces_none(monkeypatch) -> None:
    monkeypatch.setattr(cs.subprocess, "run", lambda *a, **k: _done(rc=7, out="ok"))
    res = cs.sh(["ps"])
    assert res.returncode == 7 and res.stdout == "ok"
    # subprocess 在 text=False 或被 kill 时可能给 None —— 下游 .splitlines() 不能崩
    assert cs._text(None) == "" and cs._text(b"bytes") == "bytes" and cs._text("s") == "s"


def test_diagnosis_timeout_is_much_smaller_than_the_up_timeout() -> None:
    """取病历的那几步不许用 1800s：否则一个红拖成两个红。"""
    assert cs.DIAG_TIMEOUT_S < cs.UP_TIMEOUT_S / 2


# ── 病历本身：每个服务、ps -a、空日志要明说 ──────────────────────────


def test_dump_compose_output_puts_error_lines_first_and_flags_truncation(capsys) -> None:
    up = _done(
        rc=cs.RC_TIMEOUT,
        out="Volume created\nNetwork created\nContainer maop-dash Started",
        err="Error: manifest unknown for image python:3.13-slimx\n",
    )
    cs.dump_compose_output(up)
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.startswith("::error::")]
    assert lines, "dump_compose_output 必须有产出"
    assert any(ln.startswith("::error::compose: E ") and "manifest unknown" in ln for ln in lines)
    assert any("部分输出" in ln for ln in lines), "超时要明说这只是掐断前的部分输出"
    assert lines[0].startswith("::error::compose: E "), "病因样式要先于进度噪音出现"
    assert any("折叠了" in ln for ln in lines), (
        "同一行既进 E 又进 T 时必须折叠：本机实跑就是这么把两条错误抄成四条的"
    )


def test_dump_compose_output_stays_quiet_about_truncation_on_plain_failure(capsys) -> None:
    """rc=1 的普通失败不该谎称"被上限掐断" —— 那是两种病因。"""
    cs.dump_compose_output(_done(rc=1, out="boom", err="Error: build failed"))
    out = capsys.readouterr().out
    assert "部分输出" not in out
    assert "build failed" in out


def test_service_names_uses_official_listing_and_reports_failure(capsys) -> None:
    runner = _Runner({"config": _done(out="dashboard\npostgres\nredis\nqueue-worker\n")})
    assert cs.service_names(runner) == ["dashboard", "postgres", "redis", "queue-worker"]

    broken = _Runner({"config": _done(rc=1, out="")})
    assert cs.service_names(broken) == [], "清单取不到要给空表，让调用方决定不 dump，而不是假装有个 dashboard"
    assert "拿不到服务清单" in capsys.readouterr().out, "降级要留下痕迹，不能静默少测"


def test_service_names_treats_empty_listing_as_failure(capsys) -> None:
    assert cs.service_names(_Runner({"config": _done(rc=0, out="\n\n")})) == []
    assert "拿不到服务清单" in capsys.readouterr().out


def test_dump_stack_state_covers_every_service_with_ps_a(capsys) -> None:
    runner = _Runner(
        {
            "ps": _done(out="maop-dash Up\nmaop-pg Exited (1)\n"),
            "config": _done(out="dashboard\npostgres\nredis\n"),
            "logs": lambda args: _done(out=f"log-for-{args[-1]}"),
        }
    )
    cs.dump_stack_state(runner)
    out = capsys.readouterr().out
    called = [c[0] for c in runner.calls]
    assert ["ps", "-a"] in called, "必须 ps -a：不带 -a 时已退出的容器不在清单里"
    services_dumped = [args[-1] for args in called if args[0] == "logs"]
    assert services_dumped == ["dashboard", "postgres", "redis"], "每个服务都要有日志，不能只看 dashboard"
    assert "LOG[postgres] log-for-postgres" in out and "LOG[redis] log-for-redis" in out
    assert "PS maop-pg Exited (1)" in out


def test_content_strips_diagnosis_tags_so_the_same_error_collapses() -> None:
    """判重的键必须是"病因本身"：`PS x` 与 `LOG[pg] x` 是两个不同的串，按整行判重折不掉。"""
    assert cs._content("E boom") == "boom"
    assert cs._content("T boom") == "boom"
    assert cs._content("PS boom") == "boom"
    assert cs._content("LOG[postgres] boom") == "boom"
    assert cs._content("logs dashboard 为空：没产出") == "logs dashboard 为空：没产出"


def test_same_file_level_error_is_reported_once_not_four_times(capsys) -> None:
    """本机实跑暴露的：compose 文件没过（或守护进程不在）时，`ps -a` 与每个服务的 `logs`
    都把同一条错误原样报一遍 —— 病历抄 N 遍会把唯一那条线索埋掉，正是要治的毛病。"""
    same = "error while interpolating services.postgres.environment.POSTGRES_PASSWORD"
    runner = _Runner(
        {
            "ps": _done(rc=1, out=same + "\n"),
            "config": _done(out="dashboard\npostgres\n"),
            "logs": _done(out=same + "\n"),
        }
    )
    cs.dump_stack_state(runner)
    out = capsys.readouterr().out
    assert out.count(same) == 1, f"同一条错误出现了 {out.count(same)} 次，重复没折叠"
    assert "折叠了" in out, "折叠过要明说了多少行，别让人以为只发生了一次"


def test_dump_stack_state_skips_per_service_logs_when_the_listing_fails(capsys) -> None:
    """清单取不到 = compose 文件本身没过 ⇒ 各服务日志必然同为那条错误，不再逐个 dump。"""
    runner = _Runner({"ps": _done(rc=1, out=""), "config": _done(rc=1, out="")})
    cs.dump_stack_state(runner)
    out = capsys.readouterr().out
    assert [c[0] for c in runner.calls if c[0][0] == "logs"] == [], "清单失败时不该再打 logs"
    assert "不逐个 dump 日志" in out


def test_dump_stack_state_says_so_when_a_service_produced_no_logs(capsys) -> None:
    """空日志本身就是线索（服务没起来/没进 entrypoint），不能打印成"看了但没内容"。"""
    runner = _Runner(
        {
            "ps": _done(out=""),
            "config": _done(out="redis\n"),
            "logs": _done(rc=0, out="", err=""),
        }
    )
    cs.dump_stack_state(runner)
    assert "logs redis 为空" in capsys.readouterr().out


def test_dump_stack_state_limits_every_probe_to_the_diagnosis_timeout() -> None:
    runner = _Runner(
        {"ps": _done(out="x"), "config": _done(out="dashboard\n"), "logs": _done(out="y")}
    )
    cs.dump_stack_state(runner)
    assert runner.calls, "诊断至少要打一次"
    for args, timeout in runner.calls:
        assert timeout == cs.DIAG_TIMEOUT_S, f"{args} 用了 {timeout}s，会重蹈 1800s 那一步"


def test_dump_stack_state_reports_probe_failure_rather_than_empty(capsys) -> None:
    runner = _Runner({"ps": _done(rc=124, out="", err="")})
    cs.dump_stack_state(runner)
    out = capsys.readouterr().out
    assert "ps -a rc=124" in out
    assert "config --services rc=" in out, "取不到服务清单也要说明，别让它看起来像没有服务"


# ── 入口：三条路都得走得通 ──────────────────────────────────────────


def test_main_up_timeout_path_exits_1_and_still_diagnoses(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cs, "sh", lambda args, timeout=cs.UP_TIMEOUT_S: _done(rc=cs.RC_TIMEOUT, out="partial", err="")
    )
    called: list[int] = []

    def fake_dump(runner=cs.sh) -> None:
        called.append(1)

    monkeypatch.setattr(cs, "dump_stack_state", fake_dump)
    assert cs.main() == 1
    assert called == [1], "超时这条路也必须走完整份诊断"
    out = capsys.readouterr().out
    assert "compose up 超时" in out
    assert "partial" in out, "掐断前的部分输出必须出现在病历里"


def test_main_plain_up_failure_says_rc_not_timeout(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cs, "sh", lambda args, timeout=cs.UP_TIMEOUT_S: _done(rc=3, out="build blew up", err="")
    )
    monkeypatch.setattr(cs, "dump_stack_state", lambda runner=cs.sh: None)
    assert cs.main() == 1
    out = capsys.readouterr().out
    assert "compose up rc=3" in out and "超时" not in out


def test_main_healthy_path_returns_zero(monkeypatch) -> None:
    """重构最容易弄丢的是"成功还走得通"：这里把 up 成功 + health 200 钉住。"""
    monkeypatch.setattr(cs, "sh", lambda args, timeout=cs.UP_TIMEOUT_S: _done(rc=0, out="up"))

    class _Resp:
        def read(self) -> bytes:
            return b'{"status":"ok","version":"5.4.0"}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(cs.urllib.request, "urlopen", lambda *a, **k: _Resp())
    assert cs.main() == 0


def test_main_health_timeout_also_gets_the_full_workup(monkeypatch, capsys) -> None:
    """health 超时这条路以前只 dump dashboard；现在和 up 失败共用一条诊断。"""
    monkeypatch.setattr(cs, "sh", lambda args, timeout=cs.UP_TIMEOUT_S: _done(rc=0, out="up"))
    monkeypatch.setattr(cs, "POLL_TOTAL_S", 0)

    def explode(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(cs.urllib.request, "urlopen", explode)
    seen: list[list[str]] = []

    def probe(args, timeout=cs.DIAG_TIMEOUT_S):
        seen.append(args)
        return _done(out="redis" if args[0] == "config" else "l")

    original_dump = cs.dump_stack_state
    monkeypatch.setattr(cs, "dump_stack_state", lambda runner=probe: original_dump(probe))
    assert cs.main() == 1
    assert [c for c in seen if c[0] == "logs"] == [["logs", "--tail", "15", "redis"]]


def test_smoke_script_is_the_only_compose_entrypoint_in_ci() -> None:
    """作业里那条 `python py/scripts/compose_smoke.py` 必须还在 —— 病历再好，没人调用等于没有。"""
    text = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "python py/scripts/compose_smoke.py" in text
    job = text.split("compose-smoke:", 1)[1]
    assert "github.ref == 'refs/heads/master'" in job.split("\n\n")[0], (
        "compose-smoke 仍只在主干 push 上跑：这条红不会挡 PR，但也意味着只有主干能验到它"
    )
