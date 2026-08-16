"""Tests for MAOP Secrets Management — environment variable loading, JWT secret
priority, admin password, env-dict export, and validator rejection.

Covers:
  - MAOPSettings loads all fields from MAOP_-prefixed environment variables
  - load_jwt_secret() 3-tier priority (env > file > auto-generate)
  - MAOP_ADMIN_PASSWORD env var is read by the dashboard auth bootstrap
  - to_env_dict() exports correct MAOP_-prefixed key/value pairs
  - Invalid log_level is rejected by the field validator
  - Invalid tls_min_version is rejected by the field validator
"""

from __future__ import annotations

import os

import pytest

# ── MAOPSettings env-var loading ──────────────────────────────────

class TestSettingsFromEnv:
    """Verify every MAOPSettings field can be loaded from a MAOP_ env var."""

    def test_all_fields_from_env(self, monkeypatch, tmp_path):
        from maop.config.settings import MAOPSettings

        monkeypatch.setenv("MAOP_PROJECT_NAME", "TestProject")
        monkeypatch.setenv("MAOP_ROOT_DIR", str(tmp_path))
        monkeypatch.setenv("MAOP_DEBUG", "1")
        monkeypatch.setenv("MAOP_LOG_LEVEL", "WARNING")
        monkeypatch.setenv("MAOP_DASH_HOST", "0.0.0.0")
        monkeypatch.setenv("MAOP_DASH_PORT", "8080")
        monkeypatch.setenv("MAOP_DASH_WORKERS", "4")
        monkeypatch.setenv("MAOP_TLS_ENABLED", "1")
        monkeypatch.setenv("MAOP_TLS_CERT_FILE", "/tmp/cert.pem")
        monkeypatch.setenv("MAOP_TLS_KEY_FILE", "/tmp/key.pem")
        monkeypatch.setenv("MAOP_TLS_MIN_VERSION", "TLSv1_3")
        monkeypatch.setenv("MAOP_AUTH_ENABLED", "1")
        monkeypatch.setenv("MAOP_AUTH_DB_PATH", str(tmp_path / "auth.db"))
        monkeypatch.setenv("MAOP_RATE_LIMIT_ENABLED", "0")
        monkeypatch.setenv("MAOP_RATE_LIMIT_RPS", "100")
        monkeypatch.setenv("MAOP_RATE_LIMIT_BURST", "200")
        monkeypatch.setenv("MAOP_CORS_ORIGINS", "http://a.com,http://b.com")
        monkeypatch.setenv("MAOP_DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("MAOP_DB_PATH", str(tmp_path / "maop.db"))
        monkeypatch.setenv("MAOP_MEMORY_DB_PATH", str(tmp_path / "memory.db"))
        monkeypatch.setenv("MAOP_MEMORY_PRUNE_TTL_DAYS", "30")
        monkeypatch.setenv("MAOP_MEMORY_PRUNE_ON_STARTUP", "1")
        monkeypatch.setenv("MAOP_CB_FAILURE_THRESHOLD", "10")
        monkeypatch.setenv("MAOP_CB_RECOVERY_TIMEOUT_S", "60")
        monkeypatch.setenv("MAOP_WORKER_COUNT", "8")
        monkeypatch.setenv("MAOP_METRICS_ENABLED", "0")
        monkeypatch.setenv("MAOP_METRICS_PATH", "/custom/metrics")

        s = MAOPSettings()
        assert s.project_name == "TestProject"
        assert s.root_dir == str(tmp_path)
        assert s.debug is True
        assert s.log_level == "WARNING"
        assert s.dash_host == "0.0.0.0"
        assert s.dash_port == 8080
        assert s.dash_workers == 4
        assert s.tls_enabled is True
        assert s.tls_cert_file == "/tmp/cert.pem"
        assert s.tls_key_file == "/tmp/key.pem"
        assert s.tls_min_version == "TLSv1_3"
        assert s.auth_enabled is True
        assert s.auth_db_path == str(tmp_path / "auth.db")
        assert s.rate_limit_enabled is False
        assert s.rate_limit_rps == 100.0
        assert s.rate_limit_burst == 200
        assert s.cors_origins == "http://a.com,http://b.com"
        assert s.data_dir == str(tmp_path / "data")
        assert s.db_path == str(tmp_path / "maop.db")
        assert s.memory_db_path == str(tmp_path / "memory.db")
        assert s.memory_prune_ttl_days == 30
        assert s.memory_prune_on_startup is True
        assert s.cb_failure_threshold == 10
        assert s.cb_recovery_timeout_s == 60.0
        assert s.worker_count == 8
        assert s.metrics_enabled is False
        assert s.metrics_path == "/custom/metrics"


# ── JWT secret priority ──────────────────────────────────────────

class TestJwtSecretPriority:
    """load_jwt_secret() 3-tier: env var > file > auto-generate."""

    def test_env_var_takes_priority(self, monkeypatch, tmp_path):
        """MAOP_JWT_SECRET env var wins over file and auto-generation."""
        from maop.core.security.auth import load_jwt_secret

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        # Pre-existing file should be ignored when env var is set
        # P2 安全修复: 密钥强度校验要求 ≥32 字符，使用足够长的测试密钥
        (data_dir / "jwt_secret").write_text(
            "file_secret_value_at_least_32_chars_long", encoding="utf-8"
        )
        monkeypatch.setenv("MAOP_JWT_SECRET", "env_secret_value_at_least_32_chars_long")

        result = load_jwt_secret(data_dir)
        assert result == "env_secret_value_at_least_32_chars_long"

    def test_file_used_when_no_env(self, monkeypatch, tmp_path):
        """Fall back to data/jwt_secret file when env var is absent."""
        from maop.core.security.auth import load_jwt_secret

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        # P2 安全修复: 密钥强度校验要求 ≥32 字符
        (data_dir / "jwt_secret").write_text(
            "persisted_secret_123_at_least_32_chars_long", encoding="utf-8"
        )
        monkeypatch.delenv("MAOP_JWT_SECRET", raising=False)

        result = load_jwt_secret(data_dir)
        assert result == "persisted_secret_123_at_least_32_chars_long"

    def test_auto_generate_and_persist(self, monkeypatch, tmp_path):
        """Auto-generate a secret and persist to data/jwt_secret."""
        from maop.core.security.auth import load_jwt_secret

        data_dir = tmp_path / "data"
        monkeypatch.delenv("MAOP_JWT_SECRET", raising=False)

        result = load_jwt_secret(data_dir)
        # Should be a non-empty hex string
        assert len(result) == 64
        int(result, 16)  # valid hex
        # File should now exist with the same content
        jwt_file = data_dir / "jwt_secret"
        assert jwt_file.exists()
        assert jwt_file.read_text(encoding="utf-8") == result

    def test_env_var_empty_falls_through(self, monkeypatch, tmp_path):
        """Empty MAOP_JWT_SECRET string should fall through to file."""
        from maop.core.security.auth import load_jwt_secret

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        # P2 安全修复: 密钥强度校验要求 ≥32 字符
        (data_dir / "jwt_secret").write_text(
            "file_fallback_secret_at_least_32_chars_long", encoding="utf-8"
        )
        monkeypatch.setenv("MAOP_JWT_SECRET", "")

        result = load_jwt_secret(data_dir)
        assert result == "file_fallback_secret_at_least_32_chars_long"


# ── JWT secret strength validation (P2 安全修复) ──────────────────

class TestJwtSecretStrength:
    """_validate_jwt_secret_strength() rejects weak/short/empty secrets."""

    def test_empty_secret_rejected(self):
        from maop.core.security.auth import _validate_jwt_secret_strength

        with pytest.raises(RuntimeError, match="empty"):
            _validate_jwt_secret_strength("", "test")

    def test_short_secret_rejected(self):
        from maop.core.security.auth import _validate_jwt_secret_strength

        with pytest.raises(RuntimeError, match="too short"):
            _validate_jwt_secret_strength("short_key_only_20_chars", "test")

    def test_weak_secret_rejected(self):
        from maop.core.security.auth import _validate_jwt_secret_strength

        # 弱密钥黑名单中的值（弱密钥检查在长度检查之前，所以短弱密钥也会被拒绝）
        for weak in ("secret", "changeme", "password", "default", "maop"):
            with pytest.raises(RuntimeError, match="weak"):
                _validate_jwt_secret_strength(weak, "test")

    def test_strong_secret_accepted(self):
        from maop.core.security.auth import _validate_jwt_secret_strength

        # 64-char hex string (secrets.token_hex(32) output)
        strong = "a" * 64
        _validate_jwt_secret_strength(strong, "test")  # should not raise

    def test_env_var_weak_secret_rejected_at_load(self, monkeypatch, tmp_path):
        """load_jwt_secret() rejects weak MAOP_JWT_SECRET from env var."""
        from maop.core.security.auth import load_jwt_secret

        monkeypatch.setenv("MAOP_JWT_SECRET", "secret")
        with pytest.raises(RuntimeError, match="weak"):
            load_jwt_secret(tmp_path)

    def test_env_var_short_secret_rejected_at_load(self, monkeypatch, tmp_path):
        """load_jwt_secret() rejects short MAOP_JWT_SECRET from env var."""
        from maop.core.security.auth import load_jwt_secret

        monkeypatch.setenv("MAOP_JWT_SECRET", "too_short_only_18_chars")
        with pytest.raises(RuntimeError, match="too short"):
            load_jwt_secret(tmp_path)

    def test_file_weak_secret_rejected_at_load(self, monkeypatch, tmp_path):
        """load_jwt_secret() rejects weak secret from file."""
        from maop.core.security.auth import load_jwt_secret

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "jwt_secret").write_text("password", encoding="utf-8")
        monkeypatch.delenv("MAOP_JWT_SECRET", raising=False)
        with pytest.raises(RuntimeError, match="weak"):
            load_jwt_secret(data_dir)


# ── Admin password env var ───────────────────────────────────────

class TestAdminPasswordEnv:
    """MAOP_ADMIN_PASSWORD env var is honoured by the auth bootstrap."""

    def test_admin_password_env_read(self, monkeypatch):
        """The dashboard server reads MAOP_ADMIN_PASSWORD from the environment."""
        monkeypatch.setenv("MAOP_ADMIN_PASSWORD", "MySecurePass123")
        assert os.environ.get("MAOP_ADMIN_PASSWORD") == "MySecurePass123"

    def test_admin_password_absent(self, monkeypatch):
        """When MAOP_ADMIN_PASSWORD is unset, the env lookup returns empty."""
        monkeypatch.delenv("MAOP_ADMIN_PASSWORD", raising=False)
        assert os.environ.get("MAOP_ADMIN_PASSWORD", "") == ""


# ── to_env_dict() ────────────────────────────────────────────────

class TestToEnvDict:
    """to_env_dict() exports all fields with MAOP_ prefix and correct types."""

    def test_exports_all_fields(self):
        from maop.config.settings import MAOPSettings

        s = MAOPSettings()
        env = s.to_env_dict()
        # Every model field should be present
        for field_name in MAOPSettings.model_fields:
            assert f"MAOP_{field_name.upper()}" in env, f"Missing MAOP_{field_name.upper()}"

    def test_bool_serialised_as_0_or_1(self):
        from maop.config.settings import MAOPSettings

        s = MAOPSettings(debug=True, tls_enabled=False)
        env = s.to_env_dict()
        assert env["MAOP_DEBUG"] == "1"
        assert env["MAOP_TLS_ENABLED"] == "0"

    def test_int_and_float_serialised(self):
        from maop.config.settings import MAOPSettings

        s = MAOPSettings(dash_port=8443, rate_limit_rps=50.5)
        env = s.to_env_dict()
        assert env["MAOP_DASH_PORT"] == "8443"
        assert env["MAOP_RATE_LIMIT_RPS"] == "50.5"

    def test_str_serialised_as_is(self):
        from maop.config.settings import MAOPSettings

        s = MAOPSettings(project_name="MyMAOP", log_level="DEBUG")
        env = s.to_env_dict()
        assert env["MAOP_PROJECT_NAME"] == "MyMAOP"
        assert env["MAOP_LOG_LEVEL"] == "DEBUG"

    def test_round_trip(self, monkeypatch):
        """Export → re-import yields the same values."""
        from maop.config.settings import MAOPSettings

        original = MAOPSettings(debug=True, dash_port=9100, log_level="ERROR")
        env = original.to_env_dict()
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        reloaded = MAOPSettings()
        assert reloaded.debug is True
        assert reloaded.dash_port == 9100
        assert reloaded.log_level == "ERROR"


# ── Validator rejections ─────────────────────────────────────────

class TestValidatorRejection:
    """Invalid values are rejected by pydantic field validators."""

    def test_invalid_log_level_rejected(self):
        from maop.config.settings import MAOPSettings

        with pytest.raises(ValueError, match="log_level"):
            MAOPSettings(log_level="VERBOSE")

    def test_invalid_log_level_empty_rejected(self):
        from maop.config.settings import MAOPSettings

        with pytest.raises(ValueError, match="log_level"):
            MAOPSettings(log_level="")

    def test_invalid_tls_min_version_rejected(self):
        from maop.config.settings import MAOPSettings

        with pytest.raises(ValueError, match="tls_min_version"):
            MAOPSettings(tls_min_version="SSLv3")

    def test_invalid_tls_min_version_empty_rejected(self):
        from maop.config.settings import MAOPSettings

        with pytest.raises(ValueError, match="tls_min_version"):
            MAOPSettings(tls_min_version="")

    def test_valid_log_levels_accepted(self):
        from maop.config.settings import MAOPSettings

        for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            s = MAOPSettings(log_level=level)
            assert s.log_level == level

    def test_valid_tls_versions_accepted(self):
        from maop.config.settings import MAOPSettings

        for ver in ("TLSv1_2", "TLSv1_3"):
            s = MAOPSettings(tls_min_version=ver)
            assert s.tls_min_version == ver

    def test_insecure_tls_versions_rejected(self):
        import pydantic

        from maop.config.settings import MAOPSettings

        for ver in ("TLSv1", "TLSv1_1"):
            with pytest.raises(pydantic.ValidationError, match="insecure"):
                MAOPSettings(tls_min_version=ver)
