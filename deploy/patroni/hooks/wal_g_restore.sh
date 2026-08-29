#!/usr/bin/env bash
# WAL-G 恢复回调 — Patroni 调用 WAL-G 拉取 WAL 段用于 PITR
set -euo pipefail

if ! command -v wal-g >/dev/null 2>&1; then
    # wal-g 不可用时回退到 pg_walfilerestore 失败（让 Patroni 用 pg_basebackup 重建）
    exit 1
fi

# $1 = WAL 段文件名，$2 = 目标路径
wal-g wal-fetch "$1" "$2"