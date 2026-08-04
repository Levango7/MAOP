# MAOP Configuration Directory

This directory contains configuration loading and edition management for MAOP.

## Files

| File | Responsibility |
|------|---------------|
| `loader.py` | YAML config loading, `MaopConfig` Pydantic model, `RouteEntry` routing config |
| `edition.py` | Edition detection (Personal/Enterprise), FeatureFlag gates, degradation tracking |
| `__init__.py` | Package exports |
| `hot_reload.py` | File watcher for config hot-reload |
| `schema.py` | Config schema validation |

## Configuration Files (project root)

| File | Purpose |
|------|---------|
| `config/agents.yaml` | Agent definitions, routing rules, model assignments |
| `config/maop.yaml` | System config (ports, paths, edition settings) |
| `.env` | Environment variables (secrets, overrides) |
| `.env.example` | Template with all documented env vars |

## Edition Detection

MAOP supports two editions via runtime detection (not compile-time branching):

- **Personal**: SQLite, memory cache, local files — zero external dependencies
- **Enterprise**: PostgreSQL, Redis, Vault, RabbitMQ — production-grade backends

Edition is detected by `edition.py` using:
1. `MAOP_EDITION` env var (explicit override)
2. License file check (`MAOP_LICENSE_KEY`)
3. Feature availability probe (PostgreSQL/Redis connectivity)

## Feature Flags

Use `require_feature()` for compile-time safety:

```python
from maop.config.edition import require_feature

require_feature("distributed_cache")  # Raises RuntimeError in Personal edition
```

Use `has_feature()` for optional paths:

```python
from maop.config.edition import has_feature

if has_feature("rbac"):
    apply_rbac_rules(request)
```

## Hot Reload

Config changes are watched by `hot_reload.py`. On file change:
1. Config is re-parsed
2. Validation runs
3. If valid, config is atomically swapped
4. Subscribers notified via event bus

## Environment Variables

See `.env.example` for the complete list. Key variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `MAOP_EDITION` | auto | `personal` / `enterprise` / `auto` |
| `MAOP_ROOT` | cwd | Project root directory |
| `MAOP_JWT_SECRET` | auto-gen | JWT signing secret (set in production!) |
| `MAOP_CORS_ORIGINS` | localhost | Comma-separated allowed origins |
| `MAOP_DB_PATH` | data/maop.db | Primary database path |