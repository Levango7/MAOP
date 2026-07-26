"""Tests for JSON structured logging (P2-2).

Covers:
  * ``JsonLogFormatter`` produces valid JSON with the expected schema.
  * ``setup_json_logging`` configures the root logger correctly.
  * Environment variable ``MAOP_JSON_LOG`` toggles JSON logging in CLI.
  * ``extra`` fields are transparently passed through into the JSON output.
"""

from __future__ import annotations

import json
import logging
import os
from unittest import mock

from maop.core.monitoring import JsonLogFormatter, setup_json_logging

# ── Helpers ────────────────────────────────────────────────────────

def _make_record(
    msg: str = "hello",
    level: int = logging.INFO,
    name: str = "maop.test",
    **extra,
) -> logging.LogRecord:
    """Build a minimal LogRecord for formatter tests."""
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=42,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for k, v in extra.items():
        setattr(record, k, v)
    return record


def _capture_root_stderr(capfd) -> list[dict]:
    """Read captured stderr and parse each non-empty line as JSON."""
    captured = capfd.readouterr()
    results = []
    for line in captured.err.splitlines():
        line = line.strip()
        if line:
            results.append(json.loads(line))
    return results


# ── JsonLogFormatter ───────────────────────────────────────────────

class TestJsonLogFormatter:
    """Validate the formatter output schema and JSON validity."""

    def test_produces_valid_json(self):
        fmt = JsonLogFormatter()
        record = _make_record("test message")
        output = fmt.format(record)
        # Must be a single JSON line.
        assert "\n" not in output
        data = json.loads(output)
        assert isinstance(data, dict)

    def test_schema_fields(self):
        fmt = JsonLogFormatter()
        record = _make_record("schema check", name="maop.core.demo")
        data = json.loads(fmt.format(record))
        assert set({"ts", "level", "logger", "msg", "module", "func", "line"}).issubset(data)
        assert data["level"] == "INFO"
        assert data["logger"] == "maop.core.demo"
        assert data["msg"] == "schema check"
        assert data["line"] == 42
        assert isinstance(data["ts"], str)
        # ISO8601 should contain a 'T' separator.
        assert "T" in data["ts"]

    def test_level_mapping(self):
        fmt = JsonLogFormatter()
        for py_level, expected in [
            (logging.DEBUG, "DEBUG"),
            (logging.WARNING, "WARNING"),
            (logging.ERROR, "ERROR"),
            (logging.CRITICAL, "CRITICAL"),
        ]:
            record = _make_record("lvl", level=py_level)
            data = json.loads(fmt.format(record))
            assert data["level"] == expected

    def test_trace_id_from_extra(self):
        fmt = JsonLogFormatter()
        record = _make_record("traced", trace_id="abc-123")
        data = json.loads(fmt.format(record))
        assert data["trace_id"] == "abc-123"

    def test_no_trace_id_key_when_absent(self):
        fmt = JsonLogFormatter()
        record = _make_record("no trace")
        data = json.loads(fmt.format(record))
        assert "trace_id" not in data

    def test_exception_info(self):
        fmt = JsonLogFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys as _sys
            record = _make_record("exc", exc_info=_sys.exc_info())
        data = json.loads(fmt.format(record))
        assert "exc" in data
        assert "ValueError" in data["exc"]
        assert "boom" in data["exc"]


# ── extra field pass-through ──────────────────────────────────────

class TestExtraPassthrough:
    """Extra fields supplied via ``logger.info(..., extra=...)`` must appear
    in the JSON payload."""

    def test_custom_extra_field(self):
        fmt = JsonLogFormatter()
        record = _make_record("with extra", request_id="req-001", user="alice")
        data = json.loads(fmt.format(record))
        assert data["request_id"] == "req-001"
        assert data["user"] == "alice"

    def test_reserved_keys_not_overwritten(self):
        """Standard LogRecord attributes (process, thread, etc.) must not
        leak into the JSON output, and our schema keys always come from
        the formatter — not from record.__dict__ extras."""
        fmt = JsonLogFormatter()
        record = _make_record("real msg")
        data = json.loads(fmt.format(record))
        # Schema keys are set by the formatter.
        assert data["msg"] == "real msg"
        assert data["level"] == "INFO"
        # Internal LogRecord bookkeeping must not appear in output.
        for leaky in ("process", "threadName", "processName", "args",
                       "relativeCreated", "pathname", "filename"):
            assert leaky not in data, f"{leaky} should not leak into JSON"

    def test_extra_via_real_logger(self, capfd):
        """End-to-end: emit through a real logger configured by
        setup_json_logging and verify extras appear in stderr JSON."""
        setup_json_logging(level="DEBUG")
        log = logging.getLogger("maop.test.extra")
        log.info("e2e extra", extra={"correlation_id": "corr-42"})
        logging.getLogger().handlers[0].flush()
        entries = _capture_root_stderr(capfd)
        matching = [e for e in entries if e.get("msg") == "e2e extra"]
        assert matching, f"expected JSON log entry for 'e2e extra', got {entries}"
        assert matching[0]["correlation_id"] == "corr-42"


# ── setup_json_logging ─────────────────────────────────────────────

class TestSetupJsonLogging:
    """Verify root logger configuration."""

    def test_root_has_json_formatter(self):
        root = setup_json_logging(level="INFO")
        assert root.level == logging.INFO
        assert len(root.handlers) >= 1
        for handler in root.handlers:
            assert isinstance(handler.formatter, JsonLogFormatter)

    def test_maop_namespace_level(self):
        setup_json_logging(level="DEBUG")
        MAOP_logger = logging.getLogger("MAOP")
        assert MAOP_logger.level == logging.DEBUG

    def test_log_file_handler(self, tmp_path):
        log_file = tmp_path / "json.log"
        setup_json_logging(level="INFO", log_file=log_file)
        log = logging.getLogger("maop.test.file")
        log.info("file message")
        for h in logging.getLogger().handlers:
            h.flush()
        assert log_file.exists()
        content = log_file.read_text(encoding="utf-8").strip()
        assert content, "log file should not be empty"
        data = json.loads(content.splitlines()[-1])
        assert data["msg"] == "file message"

    def test_idempotent_no_duplicate_handlers(self):
        setup_json_logging(level="INFO")
        first_count = len(logging.getLogger().handlers)
        setup_json_logging(level="DEBUG")
        second_count = len(logging.getLogger().handlers)
        assert second_count == first_count, (
            "calling setup_json_logging twice should not duplicate handlers"
        )

    def test_stderr_json_output(self, capfd):
        setup_json_logging(level="INFO")
        log = logging.getLogger("maop.test.stderr")
        log.info("stderr hello")
        for h in logging.getLogger().handlers:
            h.flush()
        entries = _capture_root_stderr(capfd)
        matching = [e for e in entries if e.get("msg") == "stderr hello"]
        assert matching, f"expected JSON log on stderr, got {entries}"
        assert matching[0]["logger"] == "maop.test.stderr"


# ── Environment variable toggle (CLI integration) ─────────────────

class TestEnvVarToggle:
    """MAOP_JSON_LOG=1 should enable JSON logging in cli.main()."""

    def test_env_off_no_json(self, capfd):
        """When MAOP_JSON_LOG is unset / 0, cli.main should not install
        JsonLogFormatter on the root logger."""
        from maop.cli import main
        # Reset root logger to a clean state so prior tests don't interfere.
        root = logging.getLogger()
        original_handlers = list(root.handlers)
        root.handlers = []  # clean slate
        try:
            with mock.patch.dict(os.environ, {"MAOP_JSON_LOG": "0"}, clear=False):
                with mock.patch("sys.argv", ["MAOP", "status"]):
                    with mock.patch("maop.cli.cmd_status", lambda: None):
                        main()
            # No JsonLogFormatter should have been installed.
            for h in root.handlers:
                assert not isinstance(h.formatter, JsonLogFormatter), (
                    "JSON formatter should not be installed when MAOP_JSON_LOG=0"
                )
        finally:
            root.handlers = original_handlers

    def test_env_on_installs_json(self, capfd):
        """When MAOP_JSON_LOG=1, cli.main should install JsonLogFormatter."""
        from maop.cli import main
        root = logging.getLogger()
        original_handlers = list(root.handlers)
        try:
            with mock.patch.dict(os.environ, {"MAOP_JSON_LOG": "1"}, clear=False):
                with mock.patch("sys.argv", ["MAOP", "status"]):
                    with mock.patch("maop.cli.cmd_status", lambda: None):
                        main()
            # At least one handler should have JsonLogFormatter.
            assert any(
                isinstance(h.formatter, JsonLogFormatter) for h in root.handlers
            ), "JSON formatter should be installed when MAOP_JSON_LOG=1"
        finally:
            root.handlers = original_handlers

    def test_env_on_with_log_file(self, tmp_path):
        """MAOP_JSON_LOG=1 + MAOP_JSON_LOG_FILE should create a file handler."""
        from maop.cli import main
        root = logging.getLogger()
        original_handlers = list(root.handlers)
        log_file = tmp_path / "env.log"
        try:
            env = {"MAOP_JSON_LOG": "1", "MAOP_JSON_LOG_FILE": str(log_file)}
            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch("sys.argv", ["MAOP", "status"]):
                    with mock.patch("maop.cli.cmd_status", lambda: None):
                        main()
            file_handlers = [
                h for h in root.handlers if isinstance(h, logging.FileHandler)
            ]
            assert file_handlers, "expected a FileHandler when MAOP_JSON_LOG_FILE is set"
        finally:
            root.handlers = original_handlers


# ── Settings field ─────────────────────────────────────────────────

class TestSettingsJsonLogField:
    """The ``json_log`` field should exist on MAOPSettings with default False."""

    def test_default_false(self):
        from maop.config.settings import MAOPSettings
        settings = MAOPSettings()
        assert settings.json_log is False

    def test_env_override(self):
        from maop.config.settings import MAOPSettings
        with mock.patch.dict(os.environ, {"MAOP_JSON_LOG": "1"}, clear=False):
            settings = MAOPSettings()
            assert settings.json_log is True


class TestSensitiveDataRedaction:
    """H3 回归测试：JsonLogFormatter 必须对敏感数据脱敏。

    覆盖 OpenAI key / AWS key / api_key / password / secret / token / bearer
    等常见密钥格式，确保日志中不泄露敏感信息。
    """

    def _format_msg(self, msg: str) -> str:
        rec = _make_record(msg=msg)
        return JsonLogFormatter().format(rec)

    def test_openai_key_redacted(self):
        out = self._format_msg("calling LLM with sk-abcdefghijklmnopqrstuvwxyz")
        assert "sk-abcdef" not in out
        assert "[REDACTED:openai_key]" in out

    def test_aws_key_redacted(self):
        out = self._format_msg("aws credentials: AKIAIOSFODNN7EXAMPLE")
        assert "AKIAIOSFODNN7EXAMPLE" not in out
        assert "[REDACTED:aws_key]" in out

    def test_password_redacted(self):
        out = self._format_msg("login with password=mySecretPass123")
        assert "mySecretPass123" not in out
        assert "[REDACTED:password]" in out

    def test_api_key_redacted(self):
        out = self._format_msg('config: api_key="ak_live_1234567890abcdef"')
        assert "ak_live_1234567890abcdef" not in out
        assert "[REDACTED:api_key]" in out

    def test_bearer_token_redacted(self):
        out = self._format_msg("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.test.signature")
        assert "eyJhbGciOiJIUzI1NiJ9" not in out
        assert "[REDACTED:bearer_token]" in out

    def test_non_sensitive_msg_unchanged(self):
        msg = "task=fix bug in main.py line 42"
        out = self._format_msg(msg)
        assert "task=fix bug in main.py line 42" in out

    def test_empty_msg_not_redacted(self):
        out = self._format_msg("")
        assert '"msg": ""' in out

    def test_extras_string_values_redacted(self):
        rec = _make_record(msg="normal")
        # 使用纯 api_key 格式（非 sk- 前缀），避免被 openai_key 模式先匹配
        rec.user_input = 'api_key="ak_live_1234567890abcdef"'
        out = JsonLogFormatter().format(rec)
        assert "ak_live_1234567890abcdef" not in out
        assert "[REDACTED:api_key]" in out

    def test_extras_non_string_values_preserved(self):
        rec = _make_record(msg="normal")
        rec.count = 42
        rec.enabled = True
        out = JsonLogFormatter().format(rec)
        payload = json.loads(out)
        assert payload["count"] == 42
        assert payload["enabled"] is True
