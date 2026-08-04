#!/usr/bin/env bash
# MAOP — Multi-Agent Orchestration Platform
# Universal startup script for Linux/macOS

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export MAOP_ROOT="${MAOP_ROOT:-$SCRIPT_DIR}"
export MAOP_ENV="${MAOP_ENV:-development}"
export PYTHONPATH="${MAOP_ROOT}/py:${PYTHONPATH:-}"

# Default values
PORT="${MAOP_PORT:-9079}"
HOST="${MAOP_HOST:-127.0.0.1}"

if [ "$HOST" = "0.0.0.0" ] && [ "${MAOP_ENV:-development}" = "development" ]; then
    echo "WARN: Binding to 0.0.0.0 in development mode is not recommended!"
fi

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found"
    exit 1
fi

# Check if running in venv
if [ -z "${VIRTUAL_ENV:-}" ] && [ -d "${MAOP_ROOT}/.venv" ]; then
    echo "INFO: Activating virtual environment..."
    source "${MAOP_ROOT}/.venv/bin/activate"
fi

# Install if needed
if ! python3 -c "import maop" &>/dev/null; then
    echo "ERROR: MAOP package not installed."
    echo "Please run: cd ${MAOP_ROOT}/py && pip install -e ."
    echo "Auto-installation is disabled for security."
    exit 1
fi

# Run database migrations
echo "INFO: Running database migrations..."
MAOP_ROOT="${MAOP_ROOT}" python3 -c "
import os
from maop.core.migrations import run_migrations
run_migrations(os.environ['MAOP_ROOT'])
" 2>/dev/null || echo "WARN: Migration runner not available, skipping"

# Start server
echo "INFO: Starting MAOP Dashboard on ${HOST}:${PORT} (env=${MAOP_ENV})"
exec python3 -m maop.dashboard.server --host "${HOST}" --port "${PORT}"