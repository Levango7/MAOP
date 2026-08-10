# Patroni 回调钩子脚本
#
# 在 Patroni 角色变更（on_start / on_stop / on_role_change / on_reload）时调用。
# 用途：
#   - 通知 MAOP 应用层连接池刷新（避免连接到旧 primary）
#   - 通知 HAProxy/PgBouncer 重载
#   - 发送告警（Prometheus Alertmanager / Slack / 邮件）
#
# 环境变量（Patroni 注入）：
#   - PATRONI_SCOPE：集群名（maop-cluster）
#   - PATRONI_ROLE：新角色（master / replica / demoted）
#   - PATRONI_CLUSTER: 集群名
#   - PATRONI_NAME: 节点名
#   - PATRONI_CONN_URL: 该节点的连接 URL
#
# 退出码：0 = 成功，非 0 = 失败（Patroni 记录日志但不阻断）

#!/usr/bin/env bash
set -euo pipefail

LOG_PREFIX="[patroni-hook]"

log() {
    # JSON 格式日志，与 MAOP MAOP_JSON_LOG=1 风格一致
    echo "{\"level\":\"info\",\"msg\":\"$1\",\"scope\":\"${PATRONI_SCOPE:-}\",\"role\":\"${PATRONI_ROLE:-}\",\"node\":\"${PATRONI_NAME:-}\",\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}"
}

alert() {
    # 发送告警到 Alertmanager（若配置）
    local severity="${1:-info}"
    local message="${2:-}"
    if [[ -n "${ALERTMANAGER_URL:-}" ]]; then
        curl -sf -X POST "${ALERTMANAGER_URL}/api/v2/alerts" \
            -H "Content-Type: application/json" \
            -d "[{\"labels\":{\"alertname\":\"PatroniRoleChange\",\"scope\":\"${PATRONI_SCOPE:-}\",\"node\":\"${PATRONI_NAME:-}\",\"severity\":\"${severity}\"},\"annotations\":{\"description\":\"${message}\"}}]" \
            || true  # 告警失败不阻断主流程
    fi
}

notify_app() {
    # 通知 MAOP 应用层连接池刷新
    # 通过 Redis pub/sub 广播角色变更事件，应用监听后刷新连接池
    if [[ -n "${REDIS_HOST:-}" ]]; then
        redis-cli -h "${REDIS_HOST}" -p "${REDIS_PORT:-6379}" \
            ${REDIS_PASSWORD:+-a "${REDIS_PASSWORD}"} --no-auth-warning \
            PUBLISH "maop:pg:role-change" \
            "{\"node\":\"${PATRONI_NAME:-}\",\"role\":\"${PATRONI_ROLE:-}\",\"url\":\"${PATRONI_CONN_URL:-}\",\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" \
            || true
    fi
}