"""Tests for MAOP.core.streaming — SubprocessStreamer + StreamRegistry."""

from __future__ import annotations

import asyncio

import pytest

from maop.concurrency import TokenStreamer
from maop.core.streaming import StreamRegistry, SubprocessStreamer, get_stream_registry


class TestSubprocessStreamer:
    def test_init_defaults(self):
        s = SubprocessStreamer(trace_id="t1")
        assert s._trace_id == "t1"
        assert s.stdout == ""
        assert s.stderr == ""
        assert s.duration_ms >= 0

    def test_stdout_stderr_accumulation(self):
        s = SubprocessStreamer(trace_id="t1")
        s._stdout_lines = ["line1", "line2"]
        s._stderr_lines = ["err1"]
        assert s.stdout == "line1\nline2"
        assert s.stderr == "err1"

    @pytest.mark.asyncio
    async def test_pipe_with_echo(self):
        s = SubprocessStreamer(trace_id="test-echo")
        proc = await asyncio.create_subprocess_exec(
            "python", "-c", "print('hello'); print('world')",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        exit_code = await s.pipe(proc, timeout=10)
        assert exit_code == 0
        assert "hello" in s.stdout
        assert "world" in s.stdout

    @pytest.mark.asyncio
    async def test_pipe_timeout(self):
        s = SubprocessStreamer(trace_id="test-timeout")
        proc = await asyncio.create_subprocess_exec(
            "python", "-c", "import time; time.sleep(60)",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        with pytest.raises(asyncio.TimeoutError):
            await s.pipe(proc, timeout=1)

    @pytest.mark.asyncio
    async def test_pipe_sse_events(self):
        s = SubprocessStreamer(trace_id="test-sse")
        proc = await asyncio.create_subprocess_exec(
            "python", "-c", "print('output')",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await s.pipe(proc, timeout=10)
        assert s.sse.event_count >= 2  # exec_start + stdout + exec_end

    @pytest.mark.asyncio
    async def test_pipe_with_token_streamer(self):
        tokens = TokenStreamer()
        s = SubprocessStreamer(trace_id="test-tokens", tokens=tokens)
        proc = await asyncio.create_subprocess_exec(
            "python", "-c", "print('token test')",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await s.pipe(proc, timeout=10)
        assert tokens.token_count >= 1
        assert tokens.is_ended

    @pytest.mark.asyncio
    async def test_pipe_with_line_callback(self):
        lines_received = []
        s = SubprocessStreamer(trace_id="test-cb", line_callback=lambda line: lines_received.append(line))
        proc = await asyncio.create_subprocess_exec(
            "python", "-c", "print('cb1'); print('cb2')",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await s.pipe(proc, timeout=10)
        assert len(lines_received) >= 2


class TestStreamRegistry:
    def test_register_and_get(self):
        reg = StreamRegistry()
        s = SubprocessStreamer(trace_id="r1")
        reg.register("r1", s)
        assert reg.get("r1") is s
        assert reg.get("nonexistent") is None

    def test_unregister(self):
        reg = StreamRegistry()
        s = SubprocessStreamer(trace_id="r2")
        reg.register("r2", s)
        reg.unregister("r2")
        assert reg.get("r2") is None

    def test_active(self):
        reg = StreamRegistry()
        reg.register("a1", SubprocessStreamer(trace_id="a1"))
        reg.register("a2", SubprocessStreamer(trace_id="a2"))
        active = reg.active()
        assert "a1" in active
        assert "a2" in active

    def test_get_stream_registry_singleton(self):
        r1 = get_stream_registry()
        r2 = get_stream_registry()
        assert r1 is r2
