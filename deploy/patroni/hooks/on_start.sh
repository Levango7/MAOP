#!/usr/bin/env bash
# Patroni on_start 回调 — 节点启动时调用
set -euo pipefail
source "$(dirname "$0")/common.sh"

log "Patroni node started: ${PATRONI_NAME:-} as ${PATRONI_ROLE:-}"
notify_app
alert "info" "Patroni node ${PATRONI_NAME:-} started as ${PATRONI_ROLE:-}"
exit 0