#!/bin/sh
# MAOP Docker Entrypoint
# Runs database migrations before starting the service

set -e

echo "[entrypoint] Starting MAOP container..."

# Run database migrations if alembic is available
if [ -f "alembic.ini" ] || [ -f "/app/alembic.ini" ]; then
    echo "[entrypoint] Running database migrations..."
    cd /app
    python -m alembic upgrade head 2>/dev/null || echo "[entrypoint] Migration skipped (alembic not configured)"
    cd -
else
    echo "[entrypoint] No alembic.ini found, skipping migrations"
fi

# Execute the main command
echo "[entrypoint] Starting service: $@"
exec "$@"
