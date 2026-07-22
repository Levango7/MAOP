"""MAOP Delegate Drivers — Subprocess driver implementations for each agent type.

Extracted from dispatcher.py for single-responsibility separation.
Drivers: cli, wrapper (PowerShell .ps1), powershell (inline), cmd, python.

All drivers accept an optional ``streamer`` kwarg (SubprocessStreamer).
When provided, stdout is streamed line-by-line in real-time via the
streamer's SSE/Token outputs.  When absent, the original batch
``communicate()`` path is used (backward-compatible).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shlex
import shutil
import time
from pathlib import Path
from typing import Any

from maop.core.error_schema import MaopResult, new_result
from maop.delegate.models import AgentConfig, _escape_for_cmd, _escape_for_ps_command

logger = logging.getLogger(__name__)


async def _run_cli(config: AgentConfig, prompt: str, timeout: int,
                   workdir: str, trace_id: str, *,
                   streamer: Any = None) -> MaopResult:
    """Execute via CLI driver — direct subprocess, no cmd.exe intermediary."""
    cli = config.cli
    cli_parts = cli.split()
    base_cmd = cli_parts[0]
    pre_args = cli_parts[1:]
    resolved = shutil.which(base_cmd)
    if resolved:
        base_cmd = resolved
    args_template = config.cli_args or "-p '{task}'"
    task_placeholder = None
    for ph in ("'{task}'", "{task}", "{{safePrompt}}"):
        if ph in args_template:
            task_placeholder = ph
            break

    if task_placeholder:
        # Replace the task placeholder with an unambiguous token, split the
        # whole template safely, then restore the prompt as a single argument.
        # This avoids shlex errors when the template contains unmatched quotes
        # around {task} (e.g. --message "{task}").
        placeholder = "\x00MAOP_TASK_PLACEHOLDER\x00"
        safe_template = args_template.replace(task_placeholder, placeholder)
        try:
            split_args = shlex.split(safe_template)
        except ValueError as exc:
            return new_result(
                agent=config.name, task=prompt,
                exit_code=-1, error=f"Invalid cli_args template: {exc}",
                duration_ms=0,
            )
        cli_args = [prompt if arg == placeholder else arg for arg in split_args]
    else:
        try:
            cli_args = shlex.split(args_template)
        except ValueError as exc:
            return new_result(
                agent=config.name, task=prompt,
                exit_code=-1, error=f"Invalid cli_args template: {exc}",
                duration_ms=0,
            )

    start = time.monotonic()
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            base_cmd, *pre_args, *cli_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir or None,
        )

        if streamer is not None:
            from maop.core.streaming import SubprocessStreamer
            if isinstance(streamer, SubprocessStreamer):
                try:
                    exit_code = await streamer.pipe(proc, timeout=timeout)
                except asyncio.TimeoutError:
                    return new_result(
                        agent=config.name, task=prompt,
                        exit_code=-1, error=f"TIMEOUT after {timeout}s",
                        duration_ms=int((time.monotonic() - start) * 1000),
                        trace_id=trace_id, driver="cli", model=config.model,
                    )
                duration_ms = streamer.duration_ms
                return new_result(
                    agent=config.name, task=prompt,
                    exit_code=exit_code,
                    stdout=streamer.stdout, stderr=streamer.stderr,
                    duration_ms=duration_ms, trace_id=trace_id,
                    driver="cli", model=config.model,
                )

        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        duration_ms = int((time.monotonic() - start) * 1000)
        ansi_re = re.compile(r"\x1b\[[0-9;]*m")
        out = ansi_re.sub("", stdout.decode("utf-8", errors="replace")).strip()
        err = ansi_re.sub("", stderr.decode("utf-8", errors="replace")).strip()
        return new_result(
            agent=config.name, task=prompt,
            exit_code=proc.returncode or 0,
            stdout=out, stderr=err,
            duration_ms=duration_ms, trace_id=trace_id,
            driver="cli", model=config.model,
        )
    except asyncio.TimeoutError:
        if proc:
            proc.kill()
            await proc.wait()
        return new_result(
            agent=config.name, task=prompt,
            exit_code=-1, error=f"TIMEOUT after {timeout}s",
            duration_ms=int((time.monotonic() - start) * 1000),
            trace_id=trace_id, driver="cli", model=config.model,
        )
    except FileNotFoundError as exc:
        return new_result(
            agent=config.name, task=prompt,
            exit_code=-2, error=f"Command not found: {base_cmd} ({exc})",
            duration_ms=int((time.monotonic() - start) * 1000),
            trace_id=trace_id, driver="cli", model=config.model,
        )
    except Exception as exc:
        if proc:
            proc.kill()
            await proc.wait()
        return new_result(
            agent=config.name, task=prompt,
            exit_code=-2, error=f"Execution error: {exc}",
            duration_ms=int((time.monotonic() - start) * 1000),
            trace_id=trace_id, driver="cli", model=config.model,
        )


async def _run_wrapper(config: AgentConfig, prompt: str, timeout: int,
                       workdir: str, trace_id: str) -> MaopResult:
    """Execute via wrapper driver — PowerShell .ps1 script."""
    wrapper = config.wrapper
    if not Path(wrapper).exists():
        name = wrapper if wrapper.endswith(".ps1") else f"{wrapper}.ps1"
        # Security: prevent path traversal — only allow filenames, not paths
        safe_name = Path(name).name  # strips any directory components
        wrapper = str(Path(__file__).resolve().parent.parent.parent / "src" / safe_name)

    safe_prompt = prompt.replace("'", "''")
    model_args = config.cli_args or ""

    start = time.monotonic()
    proc = None
    try:
        args = [
            "powershell", "-NoProfile", "-File", wrapper,
            "-Prompt", safe_prompt,
            "-TimeoutSeconds", str(timeout),
            "-AgentName", config.name,
            "-TraceID", trace_id,
        ] + (model_args.split() if model_args else [])

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir or None,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout + 10)
        duration_ms = int((time.monotonic() - start) * 1000)

        ansi_re = re.compile(r"\x1b\[[0-9;]*m")
        raw = ansi_re.sub("", stdout.decode("utf-8", errors="replace")).strip()

        # Try to parse unified JSON schema from wrapper output
        try:
            wrapper_result = json.loads(raw)
            if "ok" in wrapper_result:
                return new_result(
                    agent=config.name, task=prompt,
                    exit_code=wrapper_result.get("exit_code", 0),
                    stdout=wrapper_result.get("stdout", ""),
                    stderr=wrapper_result.get("stderr", ""),
                    error=wrapper_result.get("error"),
                    duration_ms=wrapper_result.get("duration_ms", duration_ms),
                    trace_id=trace_id, driver="wrapper", model=config.model,
                )
        except (json.JSONDecodeError, ValueError):
            pass

        # Fallback: raw output
        return new_result(
            agent=config.name, task=prompt,
            exit_code=proc.returncode or 0,
            stdout=raw,
            duration_ms=duration_ms, trace_id=trace_id,
            driver="wrapper", model=config.model,
        )
    except asyncio.TimeoutError:
        if proc:
            proc.kill()
            await proc.wait()
        return new_result(
            agent=config.name, task=prompt,
            exit_code=-1, error=f"TIMEOUT after {timeout}s",
            duration_ms=int((time.monotonic() - start) * 1000),
            trace_id=trace_id, driver="wrapper", model=config.model,
        )
    except FileNotFoundError as exc:
        return new_result(
            agent=config.name, task=prompt,
            exit_code=-2, error=f"PowerShell not found: {exc}",
            duration_ms=int((time.monotonic() - start) * 1000),
            trace_id=trace_id, driver="wrapper", model=config.model,
        )
    except Exception as exc:
        if proc:
            proc.kill()
            await proc.wait()
        return new_result(
            agent=config.name, task=prompt,
            exit_code=-2, error=f"Execution error: {exc}",
            duration_ms=int((time.monotonic() - start) * 1000),
            trace_id=trace_id, driver="wrapper", model=config.model,
        )


async def _run_powershell(config: AgentConfig, prompt: str, timeout: int,
                          workdir: str, trace_id: str) -> MaopResult:
    """Execute via inline PowerShell command.

    Supports cli_args template like the CLI driver:
      cli_args: -Task {task}   ->  powershell -Command "agent -Task 'prompt'"
    Falls back to appending the prompt directly when cli_args is empty.
    """
    import re as _re
    _SAFE_CLI_ARGS = _re.compile(r'^[a-zA-Z0-9_\-./\s{}]+$')
    command = config.command or config.cli
    escaped = _escape_for_ps_command(prompt)

    if config.cli_args:
        if not _SAFE_CLI_ARGS.match(config.cli_args):
            logger.warning("[dispatcher] Rejected unsafe cli_args for agent %s", config.name)
            return new_result(
                agent=config.name, task=prompt,
                exit_code=-1, error="Rejected: cli_args contains unsafe characters",
                duration_ms=0, trace_id=trace_id, driver="powershell", model=config.model,
            )
        arg_line = config.cli_args.replace("'{task}'", f"'{escaped}'")
        arg_line = arg_line.replace("{task}", escaped)
        arg_line = arg_line.replace("{{safePrompt}}", escaped)
        full_command = f"{command} {arg_line}"
    else:
        full_command = f"{command} {escaped}"

    start = time.monotonic()
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "powershell", "-NoProfile", "-Command",
            full_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir or None,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        duration_ms = int((time.monotonic() - start) * 1000)
        ansi_re = re.compile(r"\x1b\[[0-9;]*m")
        out = ansi_re.sub("", stdout.decode("utf-8", errors="replace")).strip()
        err = ansi_re.sub("", stderr.decode("utf-8", errors="replace")).strip()
        return new_result(
            agent=config.name, task=prompt,
            exit_code=proc.returncode or 0,
            stdout=out, stderr=err,
            duration_ms=duration_ms, trace_id=trace_id,
            driver="powershell", model=config.model,
        )
    except asyncio.TimeoutError:
        if proc:
            proc.kill()
            await proc.wait()
        return new_result(
            agent=config.name, task=prompt,
            exit_code=-1, error=f"TIMEOUT after {timeout}s",
            duration_ms=int((time.monotonic() - start) * 1000),
            trace_id=trace_id, driver="powershell", model=config.model,
        )
    except FileNotFoundError as exc:
        return new_result(
            agent=config.name, task=prompt,
            exit_code=-2, error=f"PowerShell not found: {exc}",
            duration_ms=int((time.monotonic() - start) * 1000),
            trace_id=trace_id, driver="powershell", model=config.model,
        )
    except Exception as exc:
        if proc:
            proc.kill()
            await proc.wait()
        return new_result(
            agent=config.name, task=prompt,
            exit_code=-2, error=f"Execution error: {exc}",
            duration_ms=int((time.monotonic() - start) * 1000),
            trace_id=trace_id, driver="powershell", model=config.model,
        )


async def _run_cmd(config: AgentConfig, prompt: str, timeout: int,
                   workdir: str, trace_id: str) -> MaopResult:
    """Execute via cmd.exe driver."""
    cli = config.cli
    escaped = _escape_for_cmd(prompt)
    args_template = config.cli_args or "{task}"
    arg_line = args_template.replace("{task}", escaped)

    start = time.monotonic()
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "cmd", "/c", cli, arg_line,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir or None,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        duration_ms = int((time.monotonic() - start) * 1000)
        ansi_re = re.compile(r"\x1b\[[0-9;]*m")
        out = ansi_re.sub("", stdout.decode("utf-8", errors="replace")).strip()
        err = ansi_re.sub("", stderr.decode("utf-8", errors="replace")).strip()
        return new_result(
            agent=config.name, task=prompt,
            exit_code=proc.returncode or 0,
            stdout=out, stderr=err,
            duration_ms=duration_ms, trace_id=trace_id,
            driver="cmd", model=config.model,
        )
    except asyncio.TimeoutError:
        if proc:
            proc.kill()
            await proc.wait()
        return new_result(
            agent=config.name, task=prompt,
            exit_code=-1, error=f"TIMEOUT after {timeout}s",
            duration_ms=int((time.monotonic() - start) * 1000),
            trace_id=trace_id, driver="cmd", model=config.model,
        )
    except FileNotFoundError as exc:
        return new_result(
            agent=config.name, task=prompt,
            exit_code=-2, error=f"Command not found: {cli} ({exc})",
            duration_ms=int((time.monotonic() - start) * 1000),
            trace_id=trace_id, driver="cmd", model=config.model,
        )
    except Exception as exc:
        if proc:
            proc.kill()
            await proc.wait()
        return new_result(
            agent=config.name, task=prompt,
            exit_code=-2, error=f"Execution error: {exc}",
            duration_ms=int((time.monotonic() - start) * 1000),
            trace_id=trace_id, driver="cmd", model=config.model,
        )


async def _run_python(config: AgentConfig, prompt: str, timeout: int,
                     workdir: str, trace_id: str) -> MaopResult:
    """Execute via Python module driver (e.g. doc-pipeline adapter)."""
    import sys
    cli = config.cli
    args_template = config.cli_args or "{task}"
    task_args = args_template.replace("{task}", prompt)

    start = time.monotonic()
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", cli, task_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir or None,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        duration_ms = int((time.monotonic() - start) * 1000)
        ansi_re = re.compile(r"\x1b\[[0-9;]*m")
        out = ansi_re.sub("", stdout.decode("utf-8", errors="replace")).strip()
        err = ansi_re.sub("", stderr.decode("utf-8", errors="replace")).strip()
        return new_result(
            agent=config.name, task=prompt,
            exit_code=proc.returncode or 0,
            stdout=out, stderr=err,
            duration_ms=duration_ms, trace_id=trace_id,
            driver="python", model=config.model,
        )
    except asyncio.TimeoutError:
        if proc:
            proc.kill()
            await proc.wait()
        return new_result(
            agent=config.name, task=prompt,
            exit_code=-1, error=f"TIMEOUT after {timeout}s",
            duration_ms=int((time.monotonic() - start) * 1000),
            trace_id=trace_id, driver="python", model=config.model,
        )
    except FileNotFoundError as exc:
        return new_result(
            agent=config.name, task=prompt,
            exit_code=-2, error=f"Python module not found: {cli} ({exc})",
            duration_ms=int((time.monotonic() - start) * 1000),
            trace_id=trace_id, driver="python", model=config.model,
        )
    except Exception as exc:
        if proc:
            proc.kill()
            await proc.wait()
        return new_result(
            agent=config.name, task=prompt,
            exit_code=-2, error=f"Execution error: {exc}",
            duration_ms=int((time.monotonic() - start) * 1000),
            trace_id=trace_id, driver="python", model=config.model,
        )


# ── Driver dispatch table ────────────────────────────────────

DRIVERS = {
    "cli": _run_cli,
    "wrapper": _run_wrapper,
    "powershell": _run_powershell,
    "cmd": _run_cmd,
    "python": _run_python,
}
