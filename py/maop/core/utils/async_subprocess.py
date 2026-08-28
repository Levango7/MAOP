"""Async subprocess helpers shared across MAOP modules.

Historically three near-identical copies of the same
``asyncio.create_subprocess_exec`` + ``asyncio.wait_for(proc.communicate(), …)``
pattern lived in:

  * ``dashboard/services/upgrade_service.py``        — ``_run_subproc`` (returns bytes, raises on timeout)
  * ``dashboard/routers/system/_deps.py``            — ``_run_subprocess`` (returns str, swallows errors)
  * ``core/agent/lifecycle/agent_repair.py``         — ``AgentRepair._run_subprocess`` (returns str, swallows errors)

This module consolidates them into two public primitives:

  * :func:`run_subprocess_bytes` — raw bytes result, propagates ``TimeoutError``.
  * :func:`run_subprocess_safe`  — decoded str result, never raises; returns
    ``(-1, "", msg)`` on timeout / spawn failure.

The original call sites keep their private wrappers (signatures unchanged for
backward compatibility with tests that monkeypatch them) but now delegate here,
so the implementation exists in exactly one place.
"""

from __future__ import annotations

import asyncio


async def run_subprocess_bytes(
    args: list[str], timeout: float
) -> tuple[int | None, bytes, bytes]:
    """Run a subprocess and return ``(returncode, stdout, stderr)`` as bytes.

    Raises ``asyncio.TimeoutError`` after ``timeout`` seconds (the process is
    killed before re-raising).  Any other spawn-time exception propagates.
    """
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode, out, err


async def run_subprocess_safe(
    cmd: list[str],
    timeout: float = 10.0,
    *,
    timeout_msg: str = "timeout",
) -> tuple[int, str, str]:
    """Run a subprocess, never raising — return ``(returncode, stdout, stderr)`` as str.

    On timeout returns ``(-1, "", timeout_msg)`` (the process is killed first).
    On any spawn-time exception returns ``(-1, "", str(exc))``.
    ``returncode`` is normalised to ``int`` (``None`` → ``0`` for parity with
    the historical ``agent_repair`` wrapper).
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            return (
                proc.returncode if proc.returncode is not None else 0,
                stdout_b.decode("utf-8", errors="replace"),
                stderr_b.decode("utf-8", errors="replace"),
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return -1, "", timeout_msg
    except Exception as exc:  # public helper contract: never raise
        return -1, "", str(exc)