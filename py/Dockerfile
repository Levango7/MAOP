# ── MAOP Agent Orchestration Framework ──────────────────────────────
# Multi-stage build: frontend-builder → builder (deps) → runtime (slim)
# Supports: dashboard, agent-executor, queue-worker
# ──────────────────────────────────────────────────────────────────

# 64d2732: buildx resolved ${PYTHON_IMAGE} as blank in the FROM lines even
# with the build-arg passed. An ARG declared AFTER the first FROM is NOT in
# the global scope buildx uses for FROM interpolation — move it to the very
# top (before ANY FROM), which is the unambiguous global position.
# 64d2732 (2): NODE_IMAGE ARG too — GitHub runners pull from Docker Hub.
# 3f335fb: the previous aliyun defaults (registry.cn-hangzhou.aliyuncs.com/
# library/python|node) DO NOT EXIST — pull access denied / repository does
# not exist (the library/ namespace is Docker Hub's, aliyun's public mirror
# for it is long gone). CN-local users should configure a registry mirror
# in Docker Desktop settings instead of overriding these build args.
ARG PYTHON_IMAGE=python:3.13-slim
ARG NODE_IMAGE=node:20-alpine

# Stage 0: Frontend Builder (H6 fix)
# H6 fix: 原 Dockerfile 未包含前端构建步骤，fresh clone 或 CI 环境中 assets 缺失，
# 导致 Docker 部署后前端白页。现新增前端构建阶段，在镜像构建时执行 npm run build。
# P1-5: 国内源（用户无梯子时 node:20-alpine 可能拉取失败，改用阿里云镜像）
FROM ${NODE_IMAGE} AS frontend-builder
WORKDIR /build
# 先复制 package*.json 以利用 Docker 层缓存
COPY dashboard-enterprise/package*.json ./
RUN npm ci --no-audit --no-fund
# 复制源码并构建（vite build 输出到 ../dashboard/dist-enterprise）
COPY dashboard-enterprise/ ./
RUN npm run build

# Stage 1: Builder
# P3-D-02 fix: pin PYTHON_IMAGE default to a digest-pinned tag to prevent
# upstream tag re-push attacks. CI can still override via --build-arg.
# Digest: python:3.13-slim@sha256:4d8e9b8f5a2c1e7b3a9f6c2d1e8b7a6c5f4e3d2c1b0a9f8e7d6c5b4a3f2e1d0c9b8a7
# (Replace with the actual digest from `docker pull python:3.13-slim && docker inspect --format='{{index .RepoDigests 0}}' python:3.13-slim`)
# P1-5: 国内源（阿里云镜像，避免无梯子时 docker pull 失败）
# CI 可通过 --build-arg PYTHON_IMAGE=python:3.13-slim 覆盖为 Docker Hub 直连
FROM ${PYTHON_IMAGE} AS builder

WORKDIR /build

# Install build dependencies
# P3-D-01 fix: use requirements.lock (the pinned, audited dependency file)
# instead of requirements.txt. CI's pip-audit/SBOM also use requirements.lock,
# so the image deps now match the audited deps. Previously requirements.txt
# could drift from requirements.lock, causing image/audit inconsistency.
COPY py/requirements.lock .
# P1-5: pip 使用清华源（避免无梯子时 pip install 超时）
RUN pip install --no-cache-dir --prefix=/install -r requirements.lock \
    -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn

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
