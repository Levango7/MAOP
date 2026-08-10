#!/usr/bin/env bash
# Patroni on_stop 回调 — 节点停止时调用
set -euo pipefail
source "$(dirname "$0")/common.sh"

log "Patroni node stopping: ${PATRONI_NAME:-}"
alert "warning" "Patroni node ${PATRONI_NAME:-} stopping"
exit 0