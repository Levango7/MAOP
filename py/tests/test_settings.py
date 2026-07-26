"""Tests for MAOP.config.settings - Pydantic Settings model."""

from __future__ import annotations

from pathlib import Path

import pytest


class TestMAOPSettings:
    def test_default_values(self, monkeypatch):
        # 显式清理影响默认值的环境变量，确保测试隔离。
        # 某些 e2e 测试（如 test_auth_enabled.py）在模块级
        # os.environ["MAOP_AUTH"] = "1" 不会自动清理，会污染后续测试。
        for var in (
            "MAOP_AUTH", "MAOP_AUTH_ENABLED", "MAOP_ENV",
            "MAOP_DEBUG", "MAOP_TLS_ENABLED",
        ):
            monkeypatch.delenv(var, raising=False)
        from maop.config.settings import MAOPSettings
        s = MAOPSettings()
        assert s.project_name == "MAOP"
        assert not s.debug
        assert s.log_level == "INFO"
        assert s.dash_port == 9079
        assert not s.tls_enabled
        assert not s.auth_enabled
        assert s.rate_limit_enabled
        assert s.rate_limit_rps == 30.0

    def test_env_override(self, monkeypatch):
        from maop.config.settings import MAOPSettings
        monkeypatch.setenv("MAOP_DEBUG", "1")
        monkeypatch.setenv("MAOP_DASH_PORT", "9090")
        monkeypatch.setenv("MAOP_LOG_LEVEL", "DEBUG")
        s = MAOPSettings()
        assert s.debug
        assert s.dash_port == 9090
        assert s.log_level == "DEBUG"

    def test_log_level_validation(self):
        from maop.config.settings import MAOPSettings
        with pytest.raises(ValueError, match="log_level"):
            MAOPSettings(log_level="INVALID")

    def test_tls_version_validation(self):
        from maop.config.settings import MAOPSettings
        with pytest.raises(ValueError, match="tls_min_version"):
            MAOPSettings(tls_min_version="SSLv3")

    def test_resolved_paths(self, tmp_path, monkeypatch):
        from maop.config.settings import MAOPSettings
        # Clear MAOP_DATA_DIR so MAOPSettings uses root_dir/data instead of conftest injection
        monkeypatch.delenv("MAOP_DATA_DIR", raising=False)
        test_dir = str(tmp_path / "MAOP-test")
        s = MAOPSettings(root_dir=test_dir)
        assert s.resolved_root_dir() == Path(test_dir).resolve()
        assert s.resolved_data_dir() == Path(test_dir).resolve() / "data"
        assert s.resolved_db_path() == Path(test_dir).resolve() / "data" / "maop.db"
        assert s.resolved_memory_db_path() == Path(test_dir).resolve() / "data" / "maop.db"  # Unified DB mode (ADR-011)

    def test_cors_origin_list(self):
        from maop.config.settings import MAOPSettings
        s = MAOPSettings(cors_origins="http://a.com,http://b.com")
        assert s.cors_origin_list() == ["http://a.com", "http://b.com"]

    def test_cors_origin_list_empty_entries(self):
        from maop.config.settings import MAOPSettings
        s = MAOPSettings(cors_origins="http://a.com,,http://b.com,")
        assert s.cors_origin_list() == ["http://a.com", "http://b.com"]

    def test_to_env_dict(self):
        from maop.config.settings import MAOPSettings
        s = MAOPSettings(debug=True, dash_port=9999)
        env = s.to_env_dict()
        assert env["MAOP_DEBUG"] == "1"
        assert env["MAOP_DASH_PORT"] == "9999"

    def test_port_validation(self):
        from maop.config.settings import MAOPSettings
        with pytest.raises(Exception):  # noqa: B017
            MAOPSettings(dash_port=0)
        with pytest.raises(Exception):  # noqa: B017
            MAOPSettings(dash_port=70000)


class TestSettingsSingleton:
    def test_get_settings(self):
        import maop.config.settings as mod
        from maop.config.settings import get_settings
        mod._settings = None  # Reset
        s = get_settings()
        assert s.project_name == "MAOP"

    def test_reload_settings(self, monkeypatch):
        import maop.config.settings as mod
        from maop.config.settings import reload_settings
        mod._settings = None
        monkeypatch.setenv("MAOP_DEBUG", "1")
        s = reload_settings()
        assert s.debug
