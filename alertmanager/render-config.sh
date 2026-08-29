#!/bin/sh
# MAOP AlertManager config renderer
#
# Renders ${VAR:default} placeholders in alertmanager.yml template to
# actual values from environment variables. This is needed because
# alertmanager does not natively support env var substitution in its
# config file, and we must not hardcode credentials.
#
# Usage (called from docker-compose entrypoint):
#   render-config.sh <template> <output> [alertmanager args...]
#
# Default paths:
#   template: /etc/alertmanager/alertmanager.yml.tmpl
#   output:   /etc/alertmanager/alertmanager.yml

set -eu

TEMPLATE="${1:-/etc/alertmanager/alertmanager.yml.tmpl}"
OUTPUT="${2:-/etc/alertmanager/alertmanager.yml}"
shift 2 2>/dev/null || true

if [ ! -f "$TEMPLATE" ]; then
  echo "[render-config] ERROR: template not found: $TEMPLATE" >&2
  exit 1
fi

# Copy template to output as starting point
cp "$TEMPLATE" "$OUTPUT"

# ── Render function ────────────────────────────────────────────
# Replaces ${VAR:default} and ${VAR} placeholders with actual values.
# Uses env var if set and non-empty, else the default from the template.
render_var() {
  var_name="$1"
  # Extract default value from template (first ${VAR:default} occurrence)
  pattern="\$\{${var_name}:[^}]*\}"
  default=$(grep -oE "$pattern" "$TEMPLATE" 2>/dev/null | head -1 | sed -E "s/^\$\{${var_name}:(.*)\}\$/\1/" || true)

  # Get actual value: env var if set and non-empty, else default
  eval "env_val=\"\${${var_name}:-}\""
  if [ -n "$env_val" ]; then
    actual="$env_val"
  else
    actual="$default"
  fi

  # Escape sed replacement special characters: \ & /
  actual_escaped=$(printf '%s' "$actual" | sed 's/[&/\]/\\&/g')

  # Replace ${VAR:default} patterns in output
  sed -i "s|\$\{${var_name}:[^}]*\}|${actual_escaped}|g" "$OUTPUT"
  # Also replace bare ${VAR} patterns (no default)
  sed -i "s|\$\{${var_name}\}|${actual_escaped}|g" "$OUTPUT"
}

# ── Render all known variables ─────────────────────────────────
# Webhook
render_var WEBHOOK_URL

# Email
render_var ALERT_EMAIL_TO
render_var ALERT_EMAIL_FROM
render_var SMTP_HOST
render_var SMTP_USER
render_var SMTP_PASSWORD
render_var SMTP_AUTH_SECRET
render_var SMTP_AUTH_IDENTITY

# Slack
render_var SLACK_WEBHOOK_URL
render_var SLACK_CHANNEL

# ── Validate rendered YAML ─────────────────────────────────────
# Basic sanity check: ensure no unresolved ${...} placeholders remain
# (slack api_url may be empty, which is valid — alertmanager skips it)
if grep -nE '\$\{[A-Za-z_][A-Za-z0-9_]*' "$OUTPUT" 2>/dev/null; then
  echo "[render-config] WARNING: unresolved \${VAR} placeholders remain in config" >&2
fi

echo "[render-config] rendered config written to $OUTPUT"

# ── Hand off to alertmanager ───────────────────────────────────
exec /bin/alertmanager \
  --config.file="$OUTPUT" \
  --storage.path=/alertmanager \
  "$@"