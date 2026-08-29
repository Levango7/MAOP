#!/usr/bin/env bash
# Patroni on_reload 回调 — PostgreSQL 配置重载时调用
set -euo pipefail
source "$(dirname "$0")/common.sh"

log "Patroni config reloaded on ${PATRONI_NAME:-}"
exit 0