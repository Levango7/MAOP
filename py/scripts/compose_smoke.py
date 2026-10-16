"""Local compose smoke: build, up, poll /api/health, diagnose on failure.

Runs INSIDE the compose-smoke CI job (single `python` invocation — no bash
loops/command substitution, which never executed on the runner). Prints
::error:: annotations line-by-line when the stack does not become healthy.

Exit codes: 0 healthy; 1 build/up failure or health timeout.

**超时也要有病历**（2026-10-16，主干 run 38867035723 换来的）：`docker compose up -d --build`
撞到 1800s 上限时，`subprocess.run` 抛 `TimeoutExpired`，把 `capture_output` 已经收到的 compose
输出整个丢掉，于是作业红得只剩一句 traceback —— 看不见是哪个服务起不来。现在超时折成 rc=124、
保留掐断前的部分输出，并且无论 up 失败还是 health 超时都走同一条诊断：`ps -a`（含已退出的容器）
+ `config --services` 列出的**每个**服务的日志尾部（只 dump dashboard 的话，pg/redis 卡住时
病因不在场）。诊断命令自己带 60s 上限 —— 否则"取病历"这件事能把作业再拖死一遍。
"""

from __future__ import annotations

import subprocess
import sys
import time
import urllib.request

COMPOSE_FILE = "docker-compose.yml"
HEALTH_URL = "http://localhost:9079/api/health"
POLL_TOTAL_S = 120
POLL_INTERVAL_S = 2
UP_TIMEOUT_S = 1800
DIAG_TIMEOUT_S = 60
TAIL_LINES = 25
LOG_LINES = 15
MAX_SERVICES = 12
RC_TIMEOUT = 124

ERROR_KEYS = (
    "error",
    "failed",
    "denied",
    "exit",
    "not found",
    "invalid",
    "unhealthy",
    "refused",
    "no space",
)


def err(msg: object) -> None:
    safe = str(msg).replace("::", "%3A%3A")[:230]
    print(f"::error::compose: {safe}")


def _text(value: object) -> str:
    """stdout/stderr 归一成 str：超时带来 bytes、None、已解码 str 三种形态都要能 splitlines。"""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value if isinstance(value, str) else ""


def sh(args: list[str], timeout: int = UP_TIMEOUT_S) -> subprocess.CompletedProcess:
    """跑一条 compose 命令。**超时不往外抛** —— 折成 rc=124，并保留掐断前的部分输出。"""
    cmd = ["docker", "compose", "-f", COMPOSE_FILE, *args]
    try:
        done = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout if exc.stdout is not None else exc.output
        return subprocess.CompletedProcess(
            cmd, RC_TIMEOUT, stdout=_text(partial), stderr=_text(exc.stderr)
        )
    return subprocess.CompletedProcess(
        cmd, done.returncode, stdout=_text(done.stdout), stderr=_text(done.stderr)
    )


def _nonblank(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip()]


_TAG_PREFIXES = ("E ", "T ", "PS ")


def _content(line: str) -> str:
    """剥掉诊断标签，只留病因本身 —— 同一条错误在 `E`/`T`/`PS`/`LOG[svc]` 里是同一件事。"""
    body = line
    if body.startswith("LOG["):
        _, sep, rest = body.partition("] ")
        if sep:
            body = rest
    for tag in _TAG_PREFIXES:
        if body.startswith(tag):
            return body[len(tag) :]
    return body


def _emit_unique(lines: list[str], seen: set[str]) -> int:
    """只打没见过的那几行（按病因本身判重），返回被折叠掉的重复行数。

    本机跑入口时暴露的：compose 文件解析不过（或守护进程不在）时，`up`、`ps -a`、
    `config --services`、每个服务的 `logs` 都会把**同一条错误**原样报一遍 —— 病历抄四遍，
    真正唯一的线索反而被埋掉。判重若用"标签+内容"就折叠不掉（`PS x` 与 `LOG[pg] x` 不同串）。
    """
    dupes = 0
    for line in lines:
        key = _content(line)
        if key in seen:
            dupes += 1
            continue
        seen.add(key)
        err(line)
    return dupes


def dump_compose_output(up: subprocess.CompletedProcess) -> None:
    """compose 自己的输出：像病因的行排前面，尾部若干行随后。"""
    # ef14bee: progress spam (Volume/Network/Created lines) buried the actual
    # error — 所以错误样式优先，尾部其次。
    seen: set[str] = set()
    lines = _nonblank(f"{up.stderr}\n{up.stdout}")
    prioritized = [f"E {ln}" for ln in lines if any(k in ln.lower() for k in ERROR_KEYS)][:15]
    prioritized += [f"T {ln}" for ln in lines[-TAIL_LINES:]]
    dupes = _emit_unique(prioritized, seen)
    if dupes:
        err(f"(折叠了 {dupes} 行与上面重复的 compose 输出)")
    if up.returncode == RC_TIMEOUT:
        err(
            f"compose 被 {UP_TIMEOUT_S}s 上限掐断（rc={RC_TIMEOUT}）：以上是掐断前的部分输出，"
            f"真正的卡点可能还没打出来 —— 下面按服务补一份实况。"
        )


def service_names(runner=sh) -> list[str]:
    """服务清单取 `compose config --services`（官方口径），不从 ps 的输出里猜列。

    拿不到就返回空表并由调用方决定怎么办 —— 这一步失败意味着 **compose 文件本身没解析过**，
    那时逐个 dump 服务日志只会把同一条文件级错误再抄 N 遍。
    """
    res = runner(["config", "--services"], DIAG_TIMEOUT_S)
    names = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
    if res.returncode != 0 or not names:
        err(f"config --services rc={res.returncode} 拿不到服务清单（compose 文件本身就没过）")
        return []
    return names[:MAX_SERVICES]


def dump_stack_state(runner=sh) -> None:
    """栈的实况：`ps -a`（含已退出的容器）+ 每个服务的日志尾部，重复行只报一次。"""
    seen: set[str] = set()
    ps = runner(["ps", "-a"], DIAG_TIMEOUT_S)
    if ps.returncode != 0:
        err(f"ps -a rc={ps.returncode}：容器清单取不到")
    lines = [f"PS {ln}" for ln in _nonblank(ps.stdout)][:TAIL_LINES]
    names = service_names(runner)
    if names:
        for name in names:
            logs = runner(["logs", "--tail", str(LOG_LINES), name], DIAG_TIMEOUT_S)
            text = logs.stdout or logs.stderr
            if logs.returncode != 0:
                lines.append(f"logs {name} rc={logs.returncode}（下面仍是它能取到的部分）")
            if not text.strip():
                lines.append(f"logs {name} 为空：这个服务一条日志都没产出（多半没起来/没进 entrypoint）")
            lines += [f"LOG[{name}] {ln}" for ln in _nonblank(text)[-TAIL_LINES:]]
    else:
        lines.append("服务清单为空 ⇒ 不逐个 dump 日志：文件级错误只会把同一条抄 N 遍")
    dupes = _emit_unique(lines, seen)
    if dupes:
        err(f"(折叠了 {dupes} 行与上面重复的诊断)")


def main() -> int:
    print("compose up -d --build ...")
    up = sh(["up", "-d", "--build"])
    if up.returncode != 0:
        kind = "超时" if up.returncode == RC_TIMEOUT else f"rc={up.returncode}"
        err(f"compose up {kind}")
        dump_compose_output(up)
        dump_stack_state()
        return 1

    deadline = time.time() + POLL_TOTAL_S
    healthy = False
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=3) as resp:
                body = resp.read().decode(errors="replace")[:200]
            print(f"healthy: {body}")
            healthy = True
            break
        except Exception:
            time.sleep(POLL_INTERVAL_S)

    if healthy:
        return 0

    err(f"/api/health not healthy after {POLL_TOTAL_S}s")
    dump_stack_state()
    return 1


if __name__ == "__main__":
    # 非 UTF-8 控制台（本机 Windows 腿）下中文会抛 UnicodeEncodeError，把诊断崩成非零 ——
    # 同仓 check_required_checks_*.py 栽过，这里一次性防住。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass
    sys.exit(main())
