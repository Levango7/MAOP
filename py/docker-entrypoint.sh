#!/bin/sh
# MAOP Docker Entrypoint
# Runs database migrations before starting the service

set -e

echo "[entrypoint] Starting MAOP container..."

# H9 fix: 强化迁移步骤 —— 检测 alembic.ini 不存在时报错退出（而非静默跳过）。
# 原：alembic.ini 不存在时仅打印日志并跳过，导致 PG schema 不初始化、应用报错。
# 现：要求 alembic.ini 必须存在（由 Dockerfile COPY 保证），缺失则明确报错。
if [ -f "alembic.ini" ] || [ -f "/app/alembic.ini" ]; then
    echo "[entrypoint] Running database migrations..."
    cd /app
    # 3f335fb: /app/data is a named volume at runtime and shadows the image
    # layer — seed the migration SQL into it on first boot so alembic's
    # 001_init (which reads data/migrations/001_init.sql) can find the files.
    if [ -d /app/seed/migrations ] && [ ! -f /app/data/migrations/001_init.sql ]; then
        echo "[entrypoint] Seeding migration SQL into data volume..."
        mkdir -p /app/data/migrations
        cp /app/seed/migrations/*.sql /app/data/migrations/ 2>/dev/null || true
    fi
    # H9 fix: 迁移失败时明确报错退出，而非静默跳过（2>/dev/null 掩盖了真实错误）。
    if ! python -m alembic upgrade head; then
        echo "[entrypoint] FATAL: Database migration failed (alembic upgrade head)."
        echo "[entrypoint] Check database connectivity and migration scripts."
        exit 1
    fi
    cd -
else
    echo "[entrypoint] FATAL: alembic.ini not found. Database migrations cannot run."
    echo "[entrypoint] Ensure Dockerfile copies alembic.ini and migrations/ to the image."
    exit 1
fi

# Execute the main command
echo "[entrypoint] Starting service: $@"
exec "$@"
