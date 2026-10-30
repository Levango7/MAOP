# maop-operator 镜像（M-T3.3 M0，build-only —— M0 不承诺部署）
#
# 构建来源（此前 ghcr.io/maop/operator 无构建来源的问题由本文件解决）：
#   docker build -f deploy/k8s/operator/Dockerfile -t ghcr.io/maop/operator:<tag> .
#
# operator 只需要 maop 本体 + kopf + kubernetes 客户端；不走完整 CI 依赖树
# （pyproject 的可选依赖组后续再收），避免 Lock drift 门与镜像膨胀。
FROM python:3.13-slim

LABEL org.opencontainers.image.source="https://github.com/Levango7/MAOP"
LABEL org.opencontainers.image.title="maop-operator"
LABEL org.opencontainers.image.description="MaopTask → maop run controller (M-T3.3 M0)"

WORKDIR /app

# 依赖先行（层缓存友好）：kopf 与 kubernetes 是 operator 的运行时依赖。
RUN pip install --no-cache-dir "kopf>=1.36,<2" "kubernetes>=29,<32"

# maop 本体（含 cli.py 的 `maop run` 入口）。
COPY py/ /app/py/
RUN pip install --no-cache-dir /app/py

# 非 root 运行。
RUN useradd --create-home operator
USER operator

ENTRYPOINT ["python", "-m", "maop.operator"]
