"""MAOP Settings - Pydantic Settings model for strong-typed configuration.

Loads from:
  1. Environment variables (MAOP_ prefix)
  2. .env file in project root
  3. config/settings.yaml (optional)

All settings have sensible defaults for development.
Production overrides via environment variables.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings


def _default_auth_enabled() -> bool:
    """生产环境默认启用认证（与 server.py 历史行为保持一致）。

    当 MAOP_ENV=production 且未显式设置 MAOP_AUTH / MAOP_AUTH_ENABLED 时，
    默认启用认证以保护写接口。非生产环境默认禁用便于本地开发。
    显式设置 MAOP_AUTH=0 仍可禁用（会触发 server.py 中的安全告警）。
    """
    return os.environ.get("MAOP_ENV", "").strip().lower() == "production"


class MAOPSettings(BaseSettings):
    """MAOP framework configuration with strong typing and validation."""

    # ── Core ───────────────────────────────────────────────────────
    project_name: str = Field(default="MAOP", description="Project name")
    root_dir: str = Field(default="", description="Project root directory (auto-detected if empty)")
    debug: bool = Field(default=False, description="Enable debug mode")
    log_level: str = Field(default="INFO", description="Logging level")
    edition: str = Field(
        default="personal",
        description="Edition profile: 'personal' (lightweight, single-user) or 'enterprise' (multi-user, RBAC, audit)",
    )

    # ── Dashboard ─────────────────────────────────────────────────
    dash_host: str = Field(default="127.0.0.1", description="Dashboard listen host")
    dash_port: int = Field(default=9079, description="Dashboard listen port", ge=1, le=65535)
    dash_workers: int = Field(default=1, description="Uvicorn worker count", ge=1)

    # ── TLS ───────────────────────────────────────────────────────
    tls_enabled: bool = Field(default=False, description="Enable TLS/HTTPS")
    tls_cert_file: str = Field(default="", description="Path to TLS certificate PEM")
    tls_key_file: str = Field(default="", description="Path to TLS private key PEM")
    tls_min_version: str = Field(default="TLSv1_2", description="Minimum TLS version")

    # ── Auth ──────────────────────────────────────────────────────
    # 支持 MAOP_AUTH_ENABLED（规范名）和 MAOP_AUTH（向后兼容，.env.example 使用）
    # 两个环境变量；AliasChoices 按声明顺序匹配，先 MAOP_AUTH_ENABLED 再 MAOP_AUTH。
    # default_factory 保留生产环境默认启用的历史行为（见 _default_auth_enabled）。
    auth_enabled: bool = Field(
        default_factory=_default_auth_enabled,
        description="Enable API auth middleware (env: MAOP_AUTH_ENABLED or MAOP_AUTH)",
        validation_alias=AliasChoices("MAOP_AUTH_ENABLED", "MAOP_AUTH"),
    )
    auth_db_path: str = Field(default="", description="Auth database path (default: data/auth.db)")

    # ── Rate Limit ────────────────────────────────────────────────
    rate_limit_enabled: bool = Field(default=True, description="Enable rate limiting")
    rate_limit_rps: float = Field(default=30.0, description="Requests per second", gt=0)
    rate_limit_burst: int = Field(default=60, description="Max burst size", gt=0)

    # ── CORS ──────────────────────────────────────────────────────
    cors_origins: str = Field(
        default="http://localhost:9079,http://127.0.0.1:9079",
        description="Comma-separated allowed CORS origins",
    )

    # ── Data ──────────────────────────────────────────────────────
    data_dir: str = Field(default="", description="Data directory (default: root_dir/data)")
    db_path: str = Field(default="", description="Main DB path (default: data_dir/maop.db)")
    memory_db_path: str = Field(default="", description="Memory DB path (default: data_dir/memory.db)")

    # ── Memory ────────────────────────────────────────────────────
    memory_prune_ttl_days: int = Field(default=90, description="Memory entry TTL for pruning", gt=0)
    memory_prune_on_startup: bool = Field(default=False, description="Run prune on startup")

    # ── Circuit Breaker ───────────────────────────────────────────
    cb_failure_threshold: int = Field(default=5, description="Failures before opening breaker", gt=0)
    cb_recovery_timeout_s: float = Field(default=30.0, description="Recovery timeout in seconds", gt=0)

    # ── Worker Pool ───────────────────────────────────────────────
    worker_count: int = Field(default=4, description="Worker pool size", ge=1, le=64)

    # ── Monitoring ────────────────────────────────────────────────
    metrics_enabled: bool = Field(default=True, description="Enable Prometheus metrics")
    metrics_path: str = Field(default="/api/metrics", description="Metrics endpoint path")
    json_log: bool = Field(default=False, description="Enable JSON structured logging")

    model_config = {
        "env_prefix": "MAOP_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",
    }

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid:
            raise ValueError(f"log_level must be one of {valid}, got {v}")
        return upper

    @field_validator("edition")
    @classmethod
    def validate_edition(cls, v: str) -> str:
        valid = {"personal", "enterprise"}
        lower = v.lower()
        if lower not in valid:
            raise ValueError(f"edition must be one of {valid}, got {v}")
        return lower

    @field_validator("tls_min_version")
    @classmethod
    def validate_tls_version(cls, v: str) -> str:
        valid = {"TLSv1_2", "TLSv1_3"}
        if v not in valid:
            raise ValueError(f"tls_min_version must be one of {valid} (TLSv1/TLSv1_1 are insecure and rejected), got {v}")
        return v

    def resolved_root_dir(self) -> Path:
        """Get resolved root directory."""
        if self.root_dir:
            return Path(self.root_dir).resolve()
        return Path(__file__).resolve().parent.parent

    def resolved_data_dir(self) -> Path:
        """Get resolved data directory."""
        if self.data_dir:
            return Path(self.data_dir).resolve()
        return self.resolved_root_dir() / "data"

    def resolved_db_path(self) -> Path:
        """Get resolved main DB path."""
        if self.db_path:
            return Path(self.db_path).resolve()
        return self.resolved_data_dir() / "maop.db"

    def resolved_memory_db_path(self) -> Path:
        """Get resolved memory DB path."""
        if self.memory_db_path:
            return Path(self.memory_db_path).resolve()
        return self.resolved_db_path()  # Unified: same as main DB (ADR-011)

    def resolved_auth_db_path(self) -> Path:
        """Get resolved auth DB path."""
        if self.auth_db_path:
            return Path(self.auth_db_path).resolve()
        return self.resolved_data_dir() / "auth.db"

    def cors_origin_list(self) -> list[str]:
        """Parse CORS origins into a list."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_enterprise(self) -> bool:
        """Check if running in enterprise edition."""
        return self.edition == "enterprise"

    @property
    def is_personal(self) -> bool:
        """Check if running in personal edition."""
        return self.edition == "personal"

    def edition_features(self) -> dict[str, bool]:
        """Return feature flags based on edition.

        Delegates to ``config.edition.all_features()`` — the single source
        of truth.  Kept on settings for backward compatibility.
        """
        from maop.config.edition import all_features
        return all_features()

    def edition_defaults(self) -> dict[str, str]:
        """Return default env var values for the current edition.

        Delegates to ``config.edition.backend_defaults()`` and maps
        to MAOP_*_BACKEND env var keys.
        """
        from maop.config.edition import backend_defaults, has_feature, FeatureFlag
        defaults = backend_defaults()
        result = {f"MAOP_{k.upper()}_BACKEND": v for k, v in defaults.items()}
        if has_feature(FeatureFlag.AUTH_AUTO):
            result["MAOP_AUTH"] = "1"
        if has_feature(FeatureFlag.TLS_AUTO):
            result["MAOP_TLS"] = "1"
        return result

    def to_env_dict(self) -> dict[str, str]:
        """Export settings as environment variable dict (MAOP_ prefix)."""
        result = {}
        for field_name, field_info in self.__class__.model_fields.items():
            value = getattr(self, field_name)
            env_key = f"MAOP_{field_name.upper()}"
            if isinstance(value, bool):
                result[env_key] = "1" if value else "0"
            elif isinstance(value, (int, float)):
                result[env_key] = str(value)
            elif isinstance(value, str):
                result[env_key] = value
        return result


# ── Singleton ─────────────────────────────────────────────────────

_settings: MAOPSettings | None = None


def get_settings() -> MAOPSettings:
    """Get or create the global settings singleton."""
    global _settings
    if _settings is None:
        _settings = MAOPSettings()
    return _settings


def reload_settings() -> MAOPSettings:
    """Force-reload settings (e.g., after .env change)."""
    global _settings
    _settings = MAOPSettings()
    return _settings
