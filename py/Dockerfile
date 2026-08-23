# ── MAOP Agent Orchestration Framework ──────────────────────────────
# Multi-stage build: frontend-builder → builder (deps) → runtime (slim)
# Supports: dashboard, agent-executor, queue-worker
# ──────────────────────────────────────────────────────────────────

# Stage 0: Frontend Builder (H6 fix)
# H6 fix: 原 Dockerfile 未包含前端构建步骤，fresh clone 或 CI 环境中 assets 缺失，
# 导致 Docker 部署后前端白页。现新增前端构建阶段，在镜像构建时执行 npm run build。
FROM node:20-alpine AS frontend-builder
WORKDIR /build
# 先复制 package*.json 以利用 Docker 层缓存
COPY dashboard-enterprise/package*.json ./
RUN npm ci --no-audit --no-fund
# 复制源码并构建（vite build 输出到 ../dashboard/dist-enterprise）
COPY dashboard-enterprise/ ./
RUN npm run build

# Stage 1: Builder
ARG PYTHON_IMAGE=python:3.13-slim
FROM ${PYTHON_IMAGE} AS builder

WORKDIR /build

# Install build dependencies
# 3.6 fix: 使用 requirements.lock 而非 requirements.txt，确保生产构建使用锁定版本，
# 与 CI audit（基于 requirements.lock）保持一致，避免版本漂移。
COPY py/requirements.lock .
RUN pip install --no-cache-dir --prefix=/install -r requirements.lock

# Stage 2: Runtime
# PYTHON_IMAGE ARG allows CI to pin digest: --build-arg PYTHON_IMAGE=python:3.13-slim@sha256:xxx
FROM ${PYTHON_IMAGE} AS runtime

# tini: proper PID 1 — reaps zombie processes, handles signals correctly
RUN apt-get update && apt-get install -y --no-install-recommends tini && rm -rf /var/lib/apt/lists/*

LABEL maintainer="MAOP Team"
LABEL description="MAOP Agent Orchestration Platform"
LABEL version="5.1.0"

# Create non-root user
RUN groupadd -r maop && useradd -r -g maop -d /app -s /sbin/nologin maop

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
# NOTE: Build context is the repo root (see CI: docker build -f py/Dockerfile . )
COPY py/maop/ maop/
COPY py/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
COPY config/ config/
COPY dashboard/ dashboard/
# H6 fix: 从 frontend-builder 阶段复制企业版前端构建产物。
# vite build 输出到 ../dashboard/dist-enterprise（相对于 /build），
# 即 frontend-builder 容器内的 /dashboard/dist-enterprise。
# 复制到 runtime 阶段的 /app/dashboard/dist-enterprise，供 dashboard 服务提供静态文件。
COPY --from=frontend-builder /dashboard/dist-enterprise/ dashboard/dist-enterprise/
# H9 fix: 复制 alembic.ini 与迁移脚本到镜像，确保 docker-entrypoint.sh 能执行数据库迁移。
# 原：未复制 alembic.ini，导致 entrypoint 检测不到而静默跳过迁移，PG schema 不初始化。
COPY alembic.ini ./
COPY py/maop/migrations/ migrations/

# Create data directory
RUN mkdir -p /app/data /app/logs && chown -R maop:maop /app

# Environment defaults
# M3 修复：同时设置 MAOP_ROOT_DIR（规范名）与 MAOP_ROOT（旧名，向后兼容）
# M2 修复：使用规范名 MAOP_TLS_ENABLED（保留 MAOP_TLS 向后兼容）
ENV MAOP_ROOT_DIR=/app \
    MAOP_ROOT=/app \
    MAOP_DATA_DIR=/app/data \
    MAOP_LOG_LEVEL=INFO \
    MAOP_DASH_HOST=0.0.0.0 \
    MAOP_DASH_PORT=9079 \
    MAOP_RATE_LIMIT_ENABLED=1 \
    MAOP_RATE_LIMIT_RPS=30 \
    MAOP_RATE_LIMIT_BURST=60 \
    MAOP_AUTH=1 \
    MAOP_TLS_ENABLED=0 \
    MAOP_TLS=0 \
    MAOP_CORS_ORIGINS=http://localhost:9079 \
    MAOP_STORAGE_BACKEND=sqlite \
    MAOP_CACHE_BACKEND=memory \
    MAOP_QUEUE_BACKEND=local \
    MAOP_KV_BACKEND=local \
    MAOP_SECRET_BACKEND=local

# Health check (dashboard only — other services use entrypoint)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9079/api/health')" || exit 1

# Expose dashboard port
EXPOSE 9079

# Switch to non-root user
USER maop

# tini as init (PID 1) — proper signal handling + zombie reaping
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/docker-entrypoint.sh"]

# Default: start dashboard. Override via docker-compose entrypoint.
CMD ["python", "-m", "maop.dashboard.server"]
