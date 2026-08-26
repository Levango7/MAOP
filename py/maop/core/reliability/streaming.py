"""MAOP Streaming — Subprocess output streaming to SSE/Token streams.

Bridges the gap between asyncio subprocess stdout and the existing
SSEStreamer / TokenStreamer so that agent output appears in real-time
on the Dashboard instead of only after the subprocess finishes.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable

from maop.concurrency import SSEStreamer, TokenStreamer

logger = logging.getLogger(__name__)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class SubprocessStreamer:
    """Stream subprocess stdout/stderr line-by-line to SSE and Token streams.

    Usage::

        streamer = SubprocessStreamer(trace_id="abc123")
        proc = await asyncio.create_subprocess_exec(
            ..., stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await streamer.pipe(proc, timeout=120)
        # streamer.sse / streamer.tokens are now populated
        result = streamer.build_result(agent="claude", task="...", model="yi-large")
    """

    def __init__(
        self,
        trace_id: str = "",
        sse: SSEStreamer | None = None,
        tokens: TokenStreamer | None = None,
        line_callback: Callable[[str], None] | None = None,
    ) -> None:
        self._trace_id = trace_id
        self.sse = sse or SSEStreamer()
        self.tokens = tokens
        self._line_callback = line_callback
        self._stdout_lines: list[str] = []
        self._stderr_lines: list[str] = []
        self._started_at: float = 0.0
        self._ended_at: float = 0.0

    async def pipe(
        self,
        proc: asyncio.subprocess.Process,
        timeout: int = 120,
    ) -> int:
        """Read stdout/stderr from *proc* in real-time, streaming each line.

        Returns the process return code.  Raises ``asyncio.TimeoutError``
        if the process does not exit within *timeout* seconds.
        """
        self._started_at = time.monotonic()

        self.sse.send_json(
            event="exec_start",
            trace_id=self._trace_id,
            ts=time.time(),
        )

        try:
            await asyncio.wait_for(
                self._read_streams(proc),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            self.sse.send_json(
                event="exec_timeout",
                trace_id=self._trace_id,
                timeout=timeout,
            )
            raise
        finally:
            self._ended_at = time.monotonic()
            self.sse.send_json(
                event="exec_end",
                trace_id=self._trace_id,
                exit_code=proc.returncode,
                duration_ms=self.duration_ms,
            )
            self.sse.close()
            if self.tokens:
                self.tokens.end()

        return proc.returncode or 0

    async def _read_streams(self, proc: asyncio.subprocess.Process) -> None:
        """Concurrently read stdout and stderr until the process exits."""
        tasks = [
            asyncio.ensure_future(self._read_stdout(proc)),
            asyncio.ensure_future(self._read_stderr(proc)),
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()

    async def _read_stdout(self, proc: asyncio.subprocess.Process) -> None:
        if proc.stdout is None:
            return
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = _ANSI_RE.sub("", raw.decode("utf-8", errors="replace")).rstrip("\n\r")
            if not line:
                continue
            self._stdout_lines.append(line)
            self.sse.send_json(
                event="stdout",
                trace_id=self._trace_id,
                line=line,
            )
            if self.tokens:
                self.tokens.push_token(line + "\n")
            if self._line_callback:
                self._line_callback(line)

    async def _read_stderr(self, proc: asyncio.subprocess.Process) -> None:
        if proc.stderr is None:
            return
        while True:
            raw = await proc.stderr.readline()
            if not raw:
                break
            line = _ANSI_RE.sub("", raw.decode("utf-8", errors="replace")).rstrip("\n\r")
            if not line:
                continue
            self._stderr_lines.append(line)
            self.sse.send_json(
                event="stderr",
                trace_id=self._trace_id,
                line=line,
            )

    @property
    def stdout(self) -> str:
        return "\n".join(self._stdout_lines)

    @property
    def stderr(self) -> str:
        return "\n".join(self._stderr_lines)

    @property
    def duration_ms(self) -> int:
        if self._ended_at == 0:
            return int((time.monotonic() - self._started_at) * 1000)
        return int((self._ended_at - self._started_at) * 1000)


class StreamRegistry:
    """Global registry mapping trace_id → active SubprocessStreamer.

    Allows the Dashboard SSE endpoint to look up a running stream
    by trace_id and subscribe to its events.
    """

    def __init__(self) -> None:
        self._streams: dict[str, SubprocessStreamer] = {}

    def register(self, trace_id: str, streamer: SubprocessStreamer) -> None:
        self._streams[trace_id] = streamer

    def unregister(self, trace_id: str) -> None:
        self._streams.pop(trace_id, None)

    def get(self, trace_id: str) -> SubprocessStreamer | None:
        return self._streams.get(trace_id)

    def active(self) -> list[str]:
        return list(self._streams.keys())


_registry: StreamRegistry | None = None


def get_stream_registry() -> StreamRegistry:
    """Return the global StreamRegistry singleton, creating it on first call."""
    global _registry
    if _registry is None:
        _registry = StreamRegistry()
    return _registry
