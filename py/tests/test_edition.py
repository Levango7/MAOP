"""Tests for MAOP edition system — config/edition.py registry + settings + backends integration."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from maop.config.edition import (
    Edition,
    FeatureFlag,
    FeatureNotAvailable,
    all_features,
    backend_defaults,
    clear_feature_overrides,
    degradation_log,
    edition_info,
    get_edition,
    has_feature,
    record_degradation,
    require_feature,
    reset_edition,
    set_edition,
    set_feature_override,
)


@pytest.fixture(autouse=True)
def _clean_edition():
    reset_edition()
    yield
    reset_edition()


# ═══════════════════════════════════════════════════════════════════════
# Edition enum
# ═══════════════════════════════════════════════════════════════════════

class TestEditionEnum:
    def test_values(self):
        assert Edition.PERSONAL.value == "personal"
        assert Edition.ENTERPRISE.value == "enterprise"

    def test_from_string(self):
        assert Edition("personal") is Edition.PERSONAL
        assert Edition("enterprise") is Edition.ENTERPRISE


# ═══════════════════════════════════════════════════════════════════════
# Feature flags
# ═══════════════════════════════════════════════════════════════════════

class TestFeatureFlag:
    def test_all_flags_are_strings(self):
        for f in FeatureFlag:
            assert isinstance(f.value, str)


# ═══════════════════════════════════════════════════════════════════════
# Edition detection
# ═══════════════════════════════════════════════════════════════════════

class TestDetectEdition:
    def test_default_is_personal(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch("maop.config.edition._is_enterprise_package_installed", return_value=False):
            reset_edition()
            assert get_edition() is Edition.PERSONAL

    def test_env_enterprise(self):
        with patch.dict(os.environ, {"MAOP_EDITION": "enterprise"}):
            reset_edition()
            assert get_edition() is Edition.ENTERPRISE

    def test_env_personal(self):
        with patch.dict(os.environ, {"MAOP_EDITION": "personal"}):
            reset_edition()
            assert get_edition() is Edition.PERSONAL

    def test_env_ent_alias(self):
        with patch.dict(os.environ, {"MAOP_EDITION": "ent"}):
            reset_edition()
            assert get_edition() is Edition.ENTERPRISE

    def test_env_community_alias(self):
        with patch.dict(os.environ, {"MAOP_EDITION": "community"}):
            reset_edition()
            assert get_edition() is Edition.PERSONAL

    def test_set_edition_overrides_env(self):
        set_edition(Edition.ENTERPRISE)
        with patch.dict(os.environ, {"MAOP_EDITION": "personal"}):
            assert get_edition() is Edition.ENTERPRISE

    def test_set_edition_from_string(self):
        set_edition("enterprise")
        assert get_edition() is Edition.ENTERPRISE


# ═══════════════════════════════════════════════════════════════════════
# has_feature / require_feature
# ═══════════════════════════════════════════════════════════════════════

class TestHasFeature:
    def test_personal_core_features(self):
        set_edition(Edition.PERSONAL)
        assert has_feature(FeatureFlag.COST_TRACKING) is True
        assert has_feature(FeatureFlag.CIRCUIT_BREAKER) is True
        assert has_feature(FeatureFlag.MEMORY_STORE) is True
        assert has_feature(FeatureFlag.HOOKS) is True

    def test_personal_no_enterprise_features(self):
        set_edition(Edition.PERSONAL)
        assert has_feature(FeatureFlag.RBAC) is False
        assert has_feature(FeatureFlag.MULTI_USER) is False
        assert has_feature(FeatureFlag.POSTGRESQL) is False
        assert has_feature(FeatureFlag.REDIS) is False

    def test_enterprise_has_all(self):
        set_edition(Edition.ENTERPRISE)
        assert has_feature(FeatureFlag.RBAC) is True
        assert has_feature(FeatureFlag.COST_TRACKING) is True
        assert has_feature(FeatureFlag.POSTGRESQL) is True

    def test_enterprise_planned_features_disabled(self):
        # RABBITMQ 和 ETCD 是 PLANNED 但尚未实现 —— 在对应 backend 模块
        # (backends_rabbitmq.py / backends_distributed.py) 实现之前，
        # ENTERPRISE 版不应启用这两个 flag。
        set_edition(Edition.ENTERPRISE)
        assert has_feature(FeatureFlag.RABBITMQ) is False
        assert has_feature(FeatureFlag.ETCD) is False

    def test_string_flag(self):
        set_edition(Edition.PERSONAL)
        assert has_feature("rbac") is False
        assert has_feature("cost_tracking") is True


class TestRequireFeature:
    def test_available_does_not_raise(self):
        set_edition(Edition.PERSONAL)
        require_feature(FeatureFlag.COST_TRACKING)

    def test_unavailable_raises(self):
        set_edition(Edition.PERSONAL)
        with pytest.raises(FeatureNotAvailable) as exc_info:
            require_feature(FeatureFlag.RBAC)
        assert "rbac" in str(exc_info.value)
        assert "personal" in str(exc_info.value)


# ═══════════════════════════════════════════════════════════════════════
# Feature overrides
# ═══════════════════════════════════════════════════════════════════════

class TestFeatureOverride:
    def test_override_enables_enterprise_feature(self):
        set_edition(Edition.PERSONAL)
        assert has_feature(FeatureFlag.RBAC) is False
        set_feature_override(FeatureFlag.RBAC, True)
        assert has_feature(FeatureFlag.RBAC) is True

    def test_override_disables_core_feature(self):
        set_edition(Edition.PERSONAL)
        assert has_feature(FeatureFlag.COST_TRACKING) is True
        set_feature_override(FeatureFlag.COST_TRACKING, False)
        assert has_feature(FeatureFlag.COST_TRACKING) is False

    def test_clear_overrides(self):
        set_edition(Edition.PERSONAL)
        set_feature_override(FeatureFlag.RBAC, True)
        clear_feature_overrides()
        assert has_feature(FeatureFlag.RBAC) is False


# ═══════════════════════════════════════════════════════════════════════
# Backend defaults
# ═══════════════════════════════════════════════════════════════════════

class TestBackendDefaults:
    def test_personal_defaults(self):
        set_edition(Edition.PERSONAL)
        defaults = backend_defaults()
        assert defaults["storage"] == "sqlite"
        assert defaults["cache"] == "memory"
        assert defaults["queue"] == "sqlite"
        assert defaults["kv"] == "sqlite"
        assert defaults["secret"] == "local"

    def test_enterprise_defaults(self):
        set_edition(Edition.ENTERPRISE)
        defaults = backend_defaults()
        assert defaults["storage"] == "postgresql"
        assert defaults["cache"] == "redis"
        assert defaults["queue"] == "redis"  # RabbitMQ backend PLANNED, not yet implemented
        assert defaults["kv"] == "sqlite"  # etcd backend PLANNED, not yet implemented
        assert defaults["secret"] == "vault"


# ═══════════════════════════════════════════════════════════════════════
# all_features / edition_info
# ═══════════════════════════════════════════════════════════════════════

class TestAllFeatures:
    def test_personal_features(self):
        set_edition(Edition.PERSONAL)
        features = all_features()
        assert features["cost_tracking"] is True
        assert features["rbac"] is False

    def test_enterprise_features(self):
        set_edition(Edition.ENTERPRISE)
        features = all_features()
        assert features["rbac"] is True
        assert features["cost_tracking"] is True


class TestEditionInfo:
    def test_personal_info(self):
        set_edition(Edition.PERSONAL)
        info = edition_info()
        assert info["edition"] == "personal"
        assert isinstance(info["features"], dict)
        assert isinstance(info["backends"], dict)
        assert isinstance(info["degradations"], list)

    def test_enterprise_info(self):
        set_edition(Edition.ENTERPRISE)
        info = edition_info()
        assert info["edition"] == "enterprise"
        assert info["features"]["rbac"] is True


# ═══════════════════════════════════════════════════════════════════════
# Degradation tracking
# ═══════════════════════════════════════════════════════════════════════

class TestDegradation:
    def test_record_and_retrieve(self):
        record_degradation("storage", "postgresql", "sqlite")
        log = degradation_log()
        assert len(log) == 1
        assert log[0]["backend"] == "storage"
        assert log[0]["requested"] == "postgresql"
        assert log[0]["fallback"] == "sqlite"

    def test_multiple_degradations(self):
        record_degradation("storage", "postgresql", "sqlite")
        record_degradation("cache", "redis", "memory")
        assert len(degradation_log()) == 2

    def test_degradation_in_edition_info(self):
        record_degradation("queue", "rabbitmq", "sqlite")
        info = edition_info()
        assert len(info["degradations"]) == 1

    def test_reset_clears_degradations(self):
        record_degradation("storage", "postgresql", "sqlite")
        reset_edition()
        assert len(degradation_log()) == 0


# ═══════════════════════════════════════════════════════════════════════
# Settings integration (edition_features / edition_defaults delegate)
# ═══════════════════════════════════════════════════════════════════════

class TestSettingsIntegration:
    def test_edition_features_delegates(self):
        from maop.config.settings import MAOPSettings
        set_edition(Edition.PERSONAL)
        s = MAOPSettings(edition="personal")
        features = s.edition_features()
        assert features["cost_tracking"] is True
        assert features["rbac"] is False

    def test_edition_defaults_delegates(self):
        from maop.config.settings import MAOPSettings
        set_edition(Edition.PERSONAL)
        s = MAOPSettings(edition="personal")
        defaults = s.edition_defaults()
        assert "MAOP_STORAGE_BACKEND" in defaults
        assert defaults["MAOP_STORAGE_BACKEND"] == "sqlite"

    def test_enterprise_edition_defaults_has_auth_tls(self):
        from maop.config.settings import MAOPSettings
        set_edition(Edition.ENTERPRISE)
        s = MAOPSettings(edition="enterprise")
        defaults = s.edition_defaults()
        assert defaults.get("MAOP_AUTH") == "1"
        assert defaults.get("MAOP_TLS") == "1"

    def test_personal_no_auth_tls_auto(self):
        from maop.config.settings import MAOPSettings
        set_edition(Edition.PERSONAL)
        s = MAOPSettings(edition="personal")
        defaults = s.edition_defaults()
        assert "MAOP_AUTH" not in defaults
        assert "MAOP_TLS" not in defaults

    def test_invalid_edition(self):
        from maop.config.settings import MAOPSettings
        with pytest.raises(ValueError, match="edition"):
            MAOPSettings(edition="ultimate")

    def test_env_override(self):
        from maop.config.settings import MAOPSettings
        with patch.dict(os.environ, {"MAOP_EDITION": "enterprise"}):
            s = MAOPSettings()
            assert s.is_enterprise is True


# ═══════════════════════════════════════════════════════════════════════
# Backends integration (_edition_defaults delegates)
# ═══════════════════════════════════════════════════════════════════════

class TestBackendSelection:
    def test_personal_storage_is_sqlite(self):
        from maop.core.backends import get_storage_backend, reset_backends
        reset_backends()
        set_edition(Edition.PERSONAL)
        from maop.core.backends import SQLiteStorageBackend
        backend = get_storage_backend(db_path=":memory:")
        assert isinstance(backend, SQLiteStorageBackend)
        reset_backends()

    def test_explicit_override_wins(self):
        from maop.core.backends import get_storage_backend, reset_backends
        reset_backends()
        set_edition(Edition.ENTERPRISE)
        with patch.dict(os.environ, {"MAOP_STORAGE_BACKEND": "sqlite"}, clear=False):
            from maop.core.backends import SQLiteStorageBackend
            backend = get_storage_backend(db_path=":memory:")
            assert isinstance(backend, SQLiteStorageBackend)
        reset_backends()
