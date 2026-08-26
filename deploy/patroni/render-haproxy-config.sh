#!/bin/sh
# MAOP HAProxy config renderer
#
# Renders ${VAR} placeholders in haproxy.cfg template to actual values from
# environment variables. This is needed because HAProxy does not natively
# support env var substitution in its config file, and we must not hardcode
# the stats password hash.
#
# Usage (called from docker-compose entrypoint):
#   render-haproxy-config.sh <template> <output> [haproxy args...]
#
# Default paths:
#   template: /usr/local/etc/haproxy/haproxy.cfg.tmpl
#   output:   /usr/local/etc/haproxy/haproxy.cfg
#
# Required env vars:
#   HAPROXY_STATS_USER          — stats endpoint username (default: admin)
#   HAPROXY_STATS_PASSWORD_HASH — sha-256 hash from `mkpasswd -m sha-256`
#                                  (REQUIRED — no default; fails fast if unset)

set -eu

TEMPLATE="${1:-/usr/local/etc/haproxy/haproxy.cfg.tmpl}"
OUTPUT="${2:-/usr/local/etc/haproxy/haproxy.cfg}"
shift 2 2>/dev/null || true

if [ ! -f "$TEMPLATE" ]; then
  echo "[render-haproxy] ERROR: template not found: $TEMPLATE" >&2
  exit 1
fi

# ── Validate required env vars ─────────────────────────────────
# P3-P-02 fix: fail fast if the stats password hash is not provided.
# This prevents HAProxy from starting with the placeholder value
# "<replace-with-sha256-hash>" which would either fail auth or
# silently allow access with a known hash.
HAPROXY_STATS_USER="${HAPROXY_STATS_USER:-admin}"
if [ -z "${HAPROXY_STATS_PASSWORD_HASH:-}" ]; then
  echo "[render-haproxy] ERROR: HAPROXY_STATS_PASSWORD_HASH is not set." >&2
  echo "[render-haproxy] Generate one with: mkpasswd -m sha-256 'your-password'" >&2
  exit 1
fi

# ── Render template ────────────────────────────────────────────
# Use envsubst to replace ${VAR} placeholders. Only substitute the
# known variables to avoid accidentally replacing HAProxy's own
# ${...} syntax (e.g. ${req.hdr(x-forwarded-for)}).
envsubst '${HAPROXY_STATS_USER} ${HAPROXY_STATS_PASSWORD_HASH}' \
  < "$TEMPLATE" > "$OUTPUT"

# ── Validate rendered config ───────────────────────────────────
# Basic sanity check: ensure no unresolved ${...} placeholders remain
# for the known variables.
if grep -nE '\$\{HAPROXY_STATS_(USER|PASSWORD_HASH)\}' "$OUTPUT" 2>/dev/null; then
  echo "[render-haproxy] ERROR: unresolved \${VAR} placeholders remain in config" >&2
  exit 1
fi

echo "[render-haproxy] rendered config written to $OUTPUT"

# ── Hand off to haproxy ────────────────────────────────────────
exec /usr/local/sbin/haproxy -f "$OUTPUT" "$@"