#!/usr/bin/env bash
# Patroni on_role_change 回调 — 角色变更（master↔replica）时调用
# 这是最关键的回调：故障切换时触发，需通知应用层刷新连接池
set -euo pipefail
source "$(dirname "$0")/common.sh"

ROLE="${PATRONI_ROLE:-unknown}"
NODE="${PATRONI_NAME:-unknown}"

log "Patroni role change: ${NODE} → ${ROLE}"

# 通知应用层连接池刷新（关键：避免写入旧 primary 导致数据丢失）
notify_app

# 根据新角色发送不同级别告警
case "${ROLE}" in
    master)
        alert "critical" "Patroni failover: ${NODE} promoted to primary"
        # 可选：触发 webhook 通知 on-call 工程师
        if [[ -n "${ONCALL_WEBHOOK_URL:-}" ]]; then
            curl -sf -X POST "${ONCALL_WEBHOOK_URL}" \
                -H "Content-Type: application/json" \
                -d "{\"text\":\"PG failover: ${NODE} is now primary\",\"severity\":\"critical\"}" \
                || true
        fi
        ;;
    replica)
        alert "info" "Patroni role change: ${NODE} now replica"
        ;;
    demoted)
        alert "critical" "Patroni node ${NODE} demoted (was primary)"
        ;;
    *)
        alert "warning" "Patroni role change: ${NODE} → unknown role ${ROLE}"
        ;;
esac

exit 0