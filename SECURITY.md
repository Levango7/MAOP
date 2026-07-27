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
