# MAOP Security Documentation

## Secrets Management

MAOP uses a layered approach to secret management, balancing developer convenience
with production safety.  All sensitive values are loaded from **environment
variables** with a `MAOP_` prefix; no secrets are ever hard-coded in source.

### Sensitive Environment Variables

| Variable | Purpose | Required? |
|---|---|---|
| `MAOP_JWT_SECRET` | HMAC-SHA256 signing secret for JWT tokens | No — auto-generated if absent |
| `MAOP_ADMIN_PASSWORD` | Initial admin password for the dashboard | No — auto-generated if absent |
| `MAOP_TLS_CERT_FILE` | Path to TLS certificate PEM file | Only when `MAOP_TLS_ENABLED=1` |
| `MAOP_TLS_KEY_FILE` | Path to TLS private key PEM file | Only when `MAOP_TLS_ENABLED=1` |

> **Never commit `.env` to version control.**  The `.gitignore` already
> excludes `.env`.  Only `.env.example` (with empty secret values) is tracked.

---

### Development Environment

1. **Copy the template:**
   ```bash
   cp .env.example .env
   ```
2. **Fill in values** — leave `MAOP_JWT_SECRET` and `MAOP_ADMIN_PASSWORD` empty
   to let MAOP auto-generate them on first run.
3. **Auto-generated artifacts** (written to `data/`):
   - `data/jwt_secret` — JWT signing secret (mode `0600` on POSIX)
   - `data/.admin-password` — auto-generated admin password (mode `0600`)

The `.env` file is loaded by `pydantic-settings` (`env_file=".env"` in
`maop/config/settings.py`).  Environment variables always take precedence over
`.env` values.

---

### Production Environment

**Recommended: environment variable injection**

Inject secrets directly into the process environment via your orchestration
platform.  This avoids any secret material on disk.

```bash
# Example: systemd unit
Environment=MAOP_JWT_SECRET=<64-char-hex>
Environment=MAOP_ADMIN_PASSWORD=<strong-password>
Environment=MAOP_TLS_ENABLED=1
Environment=MAOP_TLS_CERT_FILE=/etc/maop/tls/cert.pem
Environment=MAOP_TLS_KEY_FILE=/etc/maop/tls/key.pem

# Example: Docker
docker run -e MAOP_JWT_SECRET=... -e MAOP_ADMIN_PASSWORD=... maop

# Example: Kubernetes
env:
  - name: MAOP_JWT_SECRET
    valueFrom:
      secretKeyRef: { name: maop-secrets, key: jwt-secret }
  - name: MAOP_ADMIN_PASSWORD
    valueFrom:
      secretKeyRef: { name: maop-secrets, key: admin-password }
```

**Alternative: vault / secret manager**

For teams using HashiCorp Vault, AWS Secrets Manager, or similar:

1. Store secrets in the vault.
2. Retrieve at deploy time and export as environment variables before starting
   the MAOP process.
3. Never write secrets to `.env` or any file on the production host.

---

### JWT Secret Auto-Generation Mechanism

The JWT signing secret is loaded with a **3-tier priority** (implemented in
`maop/core/auth.py::load_jwt_secret`):

| Priority | Source | Use case |
|---|---|---|
| 1 (highest) | `MAOP_JWT_SECRET` environment variable | Production — explicit control |
| 2 | `data/jwt_secret` file | Development — persists across restarts |
| 3 (lowest) | Auto-generate `secrets.token_hex(32)` and persist | First run — zero-config bootstrap |

**Behaviour:**
- When `MAOP_JWT_SECRET` is set (non-empty), it is used directly.  No file is
  read or written.
- When the env var is absent or empty, MAOP checks `data/jwt_secret`.  If the
  file exists and is non-empty, its content is used.
- If neither the env var nor the file provides a secret, a cryptographically
  random 64-character hex string is generated via `secrets.token_hex(32)` and
  written to `data/jwt_secret` with mode `0600` (POSIX) so that subsequent
  restarts reuse the same secret and existing JWT tokens remain valid.

> **Production note:** Always set `MAOP_JWT_SECRET` explicitly in production.
> Relying on the auto-generated file means the secret lives on disk — if the
> disk is compromised, all JWTs can be forged.

---

### Admin Password Auto-Generation

When `MAOP_ADMIN_PASSWORD` is not set, MAOP generates a random password via
`secrets.token_urlsafe(16)` on first run and persists it to
`data/.admin-password` (mode `0600` on POSIX).  A log message indicates the
file path.  **Always set `MAOP_ADMIN_PASSWORD` in production.**

---

### TLS Certificate Files

`MAOP_TLS_CERT_FILE` and `MAOP_TLS_KEY_FILE` are paths (not secret values
themselves), but the files they point to contain sensitive key material.
Ensure:
- Certificate files are stored outside the application directory.
- File permissions are `0600` (key) and `0644` (cert) on POSIX.
- The `data/` directory is not served by any static-file route.
