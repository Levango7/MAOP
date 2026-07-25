# ── MAOP Agent Orchestration Framework ──────────────────────────────
# Multi-stage build: builder (deps) → runtime (slim)
# Supports: dashboard, agent-executor, queue-worker
# ──────────────────────────────────────────────────────────────────

# Stage 1: Builder
FROM python:3.13-slim AS builder

WORKDIR /build

# Install build dependencies
COPY py/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: Runtime
FROM python:3.13-slim AS runtime

LABEL maintainer="MAOP Team"
LABEL description="MAOP Agent Orchestration Platform"
LABEL version="4.0.0"

# Create non-root user
RUN groupadd -r maop && useradd -r -g maop -d /app -s /sbin/nologin maop

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
# NOTE: Build context is the repo root (see CI: docker build -f py/Dockerfile . )
COPY py/maop/ maop/
COPY config/ config/
COPY dashboard/ dashboard/

# Create data directory
RUN mkdir -p /app/data /app/logs && chown -R maop:maop /app

# Environment defaults
ENV MAOP_ROOT=/app \
    MAOP_DATA_DIR=/app/data \
    MAOP_LOG_LEVEL=INFO \
    MAOP_DASH_HOST=0.0.0.0 \
    MAOP_DASH_PORT=9079 \
    MAOP_RATE_LIMIT_ENABLED=1 \
    MAOP_RATE_LIMIT_RPS=30 \
    MAOP_RATE_LIMIT_BURST=60 \
    MAOP_AUTH=0 \
    MAOP_TLS=0 \
    MAOP_CORS_ORIGINS=http://localhost:9079 \
    MAOP_BACKEND_STORAGE=sqlite \
    MAOP_BACKEND_CACHE=local \
    MAOP_BACKEND_QUEUE=local \
    MAOP_BACKEND_KV=local \
    MAOP_BACKEND_SECRET=local

# Health check (dashboard only — other services use entrypoint)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9079/api/health')" || exit 1

# Expose dashboard port
EXPOSE 9079

# Switch to non-root user
USER maop

# Default: start dashboard. Override via docker-compose entrypoint.
CMD ["python", "-m", "maop.dashboard.server"]
