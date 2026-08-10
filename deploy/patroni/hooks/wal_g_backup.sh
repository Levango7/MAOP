#!/usr/bin/env bash
# WAL-G 备份回调 — Patroni 调用 WAL-G 创建全量备份
# WAL-G 配置通过环境变量：
#   - WALE_S3_PREFIX：S3 路径（如 s3://maop-pg-backup/cluster1）
#   - AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY：S3 凭证
#   - AWS_REGION：S3 区域
set -euo pipefail

LOG_PREFIX="[wal-g-backup]"

log() {
    echo "{\"level\":\"info\",\"msg\":\"$1\",\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}"
}

if ! command -v wal-g >/dev/null 2>&1; then
    log "wal-g not installed, skipping backup"
    exit 0
fi

log "Starting WAL-G backup push"
wal-g backup-push "${PGDATA:-/var/lib/postgresql/data}" --permanent

log "WAL-G backup completed"
exit 0