"""Tests for EventHook async webhook delivery (asyncio.Queue + aiohttp)."""
import os
import sys
import threading
import time
from pathlib import Path

import pytest

# Ensure doc-pipeline is importable (configurable via env var, fallback to default)
DOC_PIPELINE_ROOT = Path(os.environ.get("DOC_PIPELINE_ROOT", r"F:\Nexus\Workflow\doc-pipeline"))
if DOC_PIPELINE_ROOT.is_dir() and str(DOC_PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(DOC_PIPELINE_ROOT))

# P2-8: 使用 skipif 替代 --ignore 机制，当 pipeline_core 不可用时跳过整个文件。
# 这样 `pytest tests/` 可以直接运行，无需 CI 传入 --ignore 参数。
try:
    import pipeline_core  # noqa: F401
    HAS_PIPELINE_CORE = True
except ImportError:
    HAS_PIPELINE_CORE = False

pytestmark = pytest.mark.skipif(
    not HAS_PIPELINE_CORE,
    reason="pipeline_core module not available (external dependency)",
)


class TestAsyncWebhookEngine:
    """Test that webhook delivery is non-blocking via asyncio + aiohttp."""

    def test_webhook_does_not_block_emit(self):
        """emit() should return immediately even with a slow webhook endpoint."""
        from pipeline_core.event_hook import get_hook_manager, shutdown_webhook
        mgr = get_hook_manager()
        mgr.clear()

        # Register a webhook pointing to a non-routable address (would timeout for 10s if sync)
        hook_id = mgr.register(event="test.*", url="http://10.255.255.1:9999/hook")

        start = time.time()
        count = mgr.emit("test.event", {"data": "value"})
        elapsed = time.time() - start

        # emit() should return in < 1s (non-blocking), not 10s (sync timeout)
        assert elapsed < 1.0, f"emit() took {elapsed:.2f}s - webhook is still synchronous!"
        assert count == 1  # hook was matched and queued

        mgr.unregister(hook_id)
        shutdown_webhook(timeout_s=1.0)

    def test_callback_still_synchronous(self):
        """Callback hooks should still work synchronously."""
        from pipeline_core.event_hook import get_hook_manager, shutdown_webhook
        mgr = get_hook_manager()
        mgr.clear()

        received = []
        def my_callback(event, payload):
            received.append((event, payload))

        hook_id = mgr.register(event="test.*", callback=my_callback)
        count = mgr.emit("test.sync", {"msg": "hello"})

        assert count == 1
        assert len(received) == 1
        assert received[0][0] == "test.sync"
        assert received[0][1] == {"msg": "hello"}

        mgr.unregister(hook_id)
        shutdown_webhook(timeout_s=1.0)

    def test_high_volume_does_not_crash(self):
        """When emitting many events, should not crash or block."""
        from pipeline_core.event_hook import get_hook_manager, shutdown_webhook
        mgr = get_hook_manager()
        mgr.clear()

        hook_id = mgr.register(event="flood.*", url="http://10.255.255.1:9999/hook")

        # Emit many events rapidly
        start = time.time()
        count = 0
        for i in range(500):
            c = mgr.emit("flood.event", {"i": i})
            count += c
        elapsed = time.time() - start

        # All emits should succeed and be fast (< 2s for 500 events)
        assert count == 500
        assert elapsed < 2.0, f"500 emits took {elapsed:.2f}s - too slow"

        mgr.unregister(hook_id)
        shutdown_webhook(timeout_s=1.0)

    def test_wildcard_matching(self):
        """Wildcard event matching should work with async webhooks."""
        from pipeline_core.event_hook import get_hook_manager, shutdown_webhook
        mgr = get_hook_manager()
        mgr.clear()

        hook_id = mgr.register(event="task.*", url="http://10.255.255.1:9999/hook")

        count = mgr.emit("task.completed", {"task_id": "t1"})
        assert count == 1

        count = mgr.emit("task.failed", {"task_id": "t2"})
        assert count == 1

        count = mgr.emit("agent.started", {"agent": "a1"})
        assert count == 0  # no match

        mgr.unregister(hook_id)
        shutdown_webhook(timeout_s=1.0)

    def test_shutdown_is_safe(self):
        """shutdown_webhook should be safe to call multiple times."""
        from pipeline_core.event_hook import shutdown_webhook
        shutdown_webhook(timeout_s=1.0)
        shutdown_webhook(timeout_s=1.0)
        shutdown_webhook(timeout_s=1.0)

    def test_actual_webhook_delivery(self):
        """Verify actual webhook delivery using a local HTTP server."""
        import json as _json
        from http.server import BaseHTTPRequestHandler, HTTPServer

        from pipeline_core.event_hook import get_hook_manager, shutdown_webhook

        received_bodies = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                received_bodies.append(_json.loads(body))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok": true}')

            def log_message(self, *args):
                pass  # suppress log noise

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        try:
            mgr = get_hook_manager()
            mgr.clear()

            hook_id = mgr.register(
                event="test.*", url=f"http://127.0.0.1:{port}/hook"
            )

            mgr.emit("test.delivery", {"msg": "hello"})
            mgr.emit("test.delivery2", {"msg": "world"})

            # Wait for async delivery (with retries)
            deadline = time.time() + 5.0
            while len(received_bodies) < 2 and time.time() < deadline:
                time.sleep(0.1)

            assert len(received_bodies) == 2, (
                f"Expected 2 webhook deliveries, got {len(received_bodies)}"
            )
            assert received_bodies[0]["event"] == "test.delivery"
            assert received_bodies[0]["payload"] == {"msg": "hello"}
            assert received_bodies[1]["event"] == "test.delivery2"
            assert received_bodies[1]["payload"] == {"msg": "world"}

            mgr.unregister(hook_id)
        finally:
            server.shutdown()
            shutdown_webhook(timeout_s=2.0)

    def test_concurrent_emits_thread_safety(self):
        """Multiple threads emitting simultaneously should be safe."""
        from pipeline_core.event_hook import get_hook_manager, shutdown_webhook
        mgr = get_hook_manager()
        mgr.clear()

        hook_id = mgr.register(event="concurrent.*", url="http://10.255.255.1:9999/hook")

        errors = []
        def worker():
            try:
                for i in range(50):
                    mgr.emit("concurrent.event", {"thread": threading.current_thread().name, "i": i})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, name=f"W{i}") for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Concurrent emit errors: {errors}"

        mgr.unregister(hook_id)
        shutdown_webhook(timeout_s=1.0)
