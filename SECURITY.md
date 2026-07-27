# MAOP Security Documentation

## Overview

MAOP (Plan-Execute-Verify) is a multi-agent orchestration framework with built-in
security controls at every layer. This document describes the security architecture,
threat model, and operational guidelines.

## Architecture

### Authentication & Authorization
- **API Key / JWT auth** — `maop/core/auth.py` + `maop/core/middleware.py`
- Auth can be enabled via `MAOP_AUTH=1`
- Admin password auto-generated on first startup, saved to `data/auth.db`
- Custom password via `MAOP_ADMIN_PASSWORD` env var

### TLS / Transport Security
- TLS 1.2+ enforced via `maop/core/tls.py`
- Configurable via `MAOP_TLS=1`, `MAOP_TLS_CERT`, `MAOP_TLS_KEY`
- Minimum version: `MAOP_TLS_MIN_VERSION=TLSv1_2`

### Rate Limiting
- Per-IP and per-API-key rate limiting via `maop/core/rate_limiter.py`
- Configurable: `MAOP_RATE_LIMIT_RPS=30`, `MAOP_RATE_LIMIT_BURST=60`

### Command Injection Prevention
- **Zero `shell=True`** — all subprocess calls use list-form arguments
- **Safe eval** — `maop/engine.py:safe_eval()` uses AST traversal, no `eval()`/`exec()`
- **Blocked attributes** — private members (`_x`), `__class__`, `format`, `__import__` etc.
- **Null byte stripping** — `maop/delegate/dispatcher.py` strips `\x00` from all inputs
- **Template injection** — `{{safePrompt}}` template substitution prevents prompt injection

### Sandboxing
- `maop/core/sandbox.py` — Sandboxed execution with `shlex.split()` (no shell)
- `maop/core/runtime.py` — Local + isolated runtimes, all list-form subprocess

### Content Safety
- `maop/maop_verify.py` — Verify phase checks for leaked secrets in agent output
- Patterns: API keys, private keys, GitHub PATs, AWS access keys

### CORS
- Configurable origins via `MAOP_CORS_ORIGINS`
- Default: `http://localhost:9079,http://127.0.0.1:9079`

### Backend Configuration (Distributed Backends)
- Distributed backends are **optional** — local SQLite/Memory backends are used by default
- Selection env vars: `MAOP_STORAGE_BACKEND`, `MAOP_CACHE_BACKEND`, `MAOP_QUEUE_BACKEND`, `MAOP_KV_BACKEND`, `MAOP_SECRET_BACKEND`
- RabbitMQ queue backend (requires `pika`):
  - `MAOP_RABBITMQ_URL` — AMQP connection URL, e.g. `amqp://user:pass@host:5672/vhost`
  - **Security**: credentials in URL must be protected via secrets backend; broker should enforce TLS
- etcd KV backend (requires `etcd3`):
  - `MAOP_ETCD_HOST` — etcd host (default `localhost`)
  - `MAOP_ETCD_PORT` — etcd port (default `2379`)
  - `MAOP_ETCD_NAMESPACE` — key namespace prefix (default `maop`)
  - **Security**: enable etcd TLS (`--cert-file`/`--key-file`) and mTLS auth in production
- If an optional backend is unavailable (dependency missing or connection failed), MAOP
  automatically degrades to the local default and records the event via `record_degradation()`

### License CRL (Certificate Revocation List)
- Enterprise license validation supports online revocation checks via CRL
- Implemented in `maop/enterprise/crl.py` + integrated into `maop/enterprise/license.py`
- CRL is fetched via HTTP, cached locally to avoid per-validation network requests
- **Fail-safe offline degradation**: when CRL service is unreachable, cached CRL is used;
  if no cache exists, behavior depends on strict mode
- Environment variables:
  - `MAOP_CRL_URL` — CRL service URL (e.g. `https://crl.maop.example.com/list.json`).
    When unset, CRL checking is disabled (license validation relies on signature + expiry only).
  - `MAOP_CRL_CACHE_TTL_S` — Cache time-to-live in seconds (default `3600`). Controls how
    often the client refetches the CRL. Set lower for faster revocation propagation.
  - `MAOP_CRL_STRICT` — Strict mode flag (`1`/`0`, default `0`).
    - `0` (lax, default): if no CRL is available (fetch failed + no cache), licenses are
      allowed (rely on signature + expiry checks only).
    - `1` (strict): if no CRL is available, all license validations raise `CRLError` and
      are rejected. Use this in high-security deployments where revocation must be enforced.
- Cache file: `data/crl_cache.json` (atomic write via temp file + rename)
- CRL JSON format: `{ "version": 1, "updated_at": "...", "expires_at": "...", "revoked": [...] }`

## Threat Model

| Threat | Mitigation |
|--------|-----------|
| Command injection | List-form subprocess, no `shell=True`, `shlex.split()` |
| Code injection | AST-based `safe_eval()`, blocked dangerous attributes |
| Prompt injection | `{{safePrompt}}` template, null byte stripping |
| Secret leakage | Content safety gates in Verify phase |
| Brute force | Rate limiting (per-IP + per-key) |
| MITM | TLS 1.2+ enforcement |
| Unauthorized access | API Key / JWT auth middleware |
| License tampering | Ed25519 signature verification |
| License revocation bypass | CRL online check + strict mode fail-safe |

## Security Audit

Last audit: 2026-07-17

- `shell=True` residual: **0** ✅
- `eval()`/`exec()` usage: **0** ✅
- Hardcoded secrets: **0** ✅
- `print()` in production code: **0** (all converted to `logging`) ✅
- PS fallback: disabled by default, env-gated (`MAOP_FALLBACK_TO_PS=0`) ✅

## Reporting

Report security vulnerabilities to the project maintainer.
Do not file public issues for security bugs.
