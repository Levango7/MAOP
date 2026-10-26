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
# P1 supply-chain fix (2026-09-17): digest pinning prevents tag re-push attacks.
# To obtain digest: docker pull python:3.13-slim && docker inspect --format='{{index .RepoDigests 0}}' python:3.13-slim
# CI should inject --build-arg PYTHON_IMAGE=python:3.13-slim@sha256:<digest> for reproducible builds.
#
# pip index. Default is official PyPI (same 64d2732 rule: ARG must precede ANY FROM).
#
# 2026-10-21：这里原先写死清华源，而 frozen 锁的是 certifi==2026.10.19。实测清华源
# 当时最新只到 2026.7.22 —— 于是镜像装出 certifi@2026.6.17，trivy 报
# GHSA-6q48-6c7w-9gw2 (CRITICAL)，而同一个 commit 的 pip-audit 是绿的。
# 写死单一镜像源还有一个更一般的代价：**安全补丁的到达时间被源的同步节奏绑定**，
# 锁文件升了级、镜像却装不到。
#
# GitHub-hosted runner 在海外，官方源既有最新版也更快，所以默认官方。
# CN-local 无梯子构建时覆盖它：
#   docker build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple .
# 覆盖源的可用版本可能滞后于 frozen —— 装不到 pin 会让构建红，那是要的信号。
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PYTHON_IMAGE=python:3.13-slim
# 2026-09-29: node:20 → node:24，与 CI 的 env.NODE_VERSION 对齐。jsdom@30.1.1
# （lockfile 实装）engines = ^22.22.2 || ^24.15.0 || >=26.0.0，node 20 不在区间；
# 镜内 frontend-builder 与 CI frontend job 跑同一个 lockfile，两边必须同大版本。
ARG NODE_IMAGE=node:24-alpine

# Stage 0: Frontend Builder (H6 fix)
# H6 fix: 原 Dockerfile 未包含前端构建步骤，fresh clone 或 CI 环境中 assets 缺失，
# 导致 Docker 部署后前端白页。现新增前端构建阶段，在镜像构建时执行 npm run build。
# P1-5: 国内源（用户无梯子时 node 镜像可能拉取失败，改用阿里云镜像）
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

# ARG 作用域：文件顶部（第一个 FROM 之前）声明的 PIP_INDEX_URL 只对 FROM 插值可见，
# **stage 内的 RUN 用不到它** —— 必须在 stage 内重新声明一次，不带值即继承全局默认。
# 2026-10-21 实测踩过：少了这一行，`-i ${PIP_INDEX_URL}` 被 shell 展开成空字符串，
# pip 直接报 "-i option requires 1 argument"，退避重试 3 次后 Docker build 失败，
# 连带 Container Scan (trivy) 被跳过。
ARG PIP_INDEX_URL

WORKDIR /build

# Install build dependencies
#
# T2.2 (2026-09-30) 之后，真锁是 requirements.frozen.txt（uv 全量解析、精确 ==）；
# pip-audit / SBOM / CI 缓存键都已改以 frozen 为唯一输入。**唯独这一处漏改**，
# 仍在用已退为「直依赖镜像参考」的 requirements.lock —— 那个文件按 T2.2 的设计
# 只镜像 pyproject 的直接依赖，且是窄范围（>=, <），不含精确的传递依赖 pin。
#
# 实测后果（master 9bf5e38，2026-10-21）：
#   按范围约束装 ⇒ 传递依赖由 pip 现场解析，镜像里装出的是 certifi@2026.6.17；
#   而 pip-audit 那条腿已随 frozen 升到 certifi==2026.10.19。
#   ⇒ `Container Scan (trivy)` 报 GHSA-6q48-6c7w-9gw2（CRITICAL），master 转红，
#     而同一个 commit 的 pip-audit 是绿的 —— 正是"两个事实源"分裂的症状。
#
# 换成精确 pin 后，镜像依赖与审计依赖读同一份文件：pip-audit / SBOM / 镜像。
COPY py/requirements.frozen.txt .
# P1-5 的初衷（无梯子时 pip install 超时）改由文件顶部的 PIP_INDEX_URL 承担：
# 默认官方 PyPI，CN-local 构建时用 --build-arg 覆盖。仍是单一 index，不多信一个源。
# 2026-10-21：原先写死清华源是这次 trivy 红的另一半原因 —— frozen 锁 certifi==2026.10.19，
# 而清华源当时最新只到 2026.7.22，只改锁文件不改成官方源，构建会直接找不到版本。
# --trusted-host 一并去掉：默认源与清华源都是 https，该选项只对 http 索引有意义。
# 2026-09-28: 外面套一层最多 3 次的退避重试。起因是 master `cd4099e5` 上
# `Container Scan (trivy)` 红、而同一个 commit 的 `Docker build` / `Compose Smoke` 都绿 ——
# 日志是镜像偶发返回空候选集：
#   ERROR: Could not find a version that satisfies the requirement pyyaml<7.0.0,>=6.0.2 (from versions: none)
# "from versions: none" 是索引没给东西，不是版本区间无解，属于瞬时故障（重跑即恢复）。
# 有意**不**加第二个 index 主机：多信一个源就是扩大供应链面。3 次仍失败则让构建照常红
# —— 这不是把失败藏起来，只是不被一次抖动骗过。
RUN for attempt in 1 2 3; do \
      pip install --no-cache-dir --prefix=/install -r requirements.frozen.txt \
        -i ${PIP_INDEX_URL} \
        && break; \
      if [ "$attempt" = 3 ]; then echo "pip install 连续 3 次失败，放弃" >&2; exit 1; fi; \
      echo "pip install 第 $attempt 次失败，退避 $((attempt * 20))s 后重试" >&2; \
      sleep $((attempt * 20)); \
    done

# Stage 2: Runtime
# PYTHON_IMAGE ARG allows CI to pin digest: --build-arg PYTHON_IMAGE=python:3.13-slim@sha256:xxx
FROM ${PYTHON_IMAGE} AS runtime

# tini: proper PID 1 — reaps zombie processes, handles signals correctly
RUN apt-get update && apt-get install -y --no-install-recommends tini && rm -rf /var/lib/apt/lists/*

LABEL maintainer="MAOP Team"
LABEL description="MAOP Agent Orchestration Platform"
LABEL version="5.5.0"

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
# 3f335fb: COPY to the SAME repo-relative layout the repo-root alembic.ini
# expects (script_location = py/maop/migrations/alembic, resolved from /app).
# The previous flat COPY to migrations/ left alembic looking for
# py/maop/migrations/alembic which did not exist in the container — the
# entrypoint then failed "Path doesn't exist", restarting the dashboard
# container forever and failing the compose-smoke healthcheck.
COPY alembic.ini ./
COPY py/maop/migrations/ py/maop/migrations/
# 3f335fb: seed the migration SQL into a NON-volume-mounted path. /app/data
# is bind-mounted (named volume) at runtime, which SHADOWS anything baked
# here — the entrypoint copies from seed/ into the volume on first boot.
COPY data/migrations/ /app/seed/migrations/

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
