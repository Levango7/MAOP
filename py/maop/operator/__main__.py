"""``python -m maop.operator`` —— operator 进程入口（镜像 ENTRYPOINT）.

等价于 kopf 文档推荐的 ``kopf.run`` 启动方式；缺 kopf 时报带指引的错。
"""

from __future__ import annotations

import logging

from maop.operator import handlers

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    kopf = handlers._require_kopf()
    handlers.register(kopf)
    logger.info("[operator] starting kopf runner (maoptasks.maop.io v1alpha1)")
    # 内置健康服务：chart 的 liveness/readiness 探针打 :8080（端口与
    # deployment.yaml 的 metrics 容器口一致）。kopf 单端点只服务一个路径，
    # chart readiness 因此同打 /healthz。端口写死而非读 env——避免新增
    # config-drift 基线；要换端口属后续 values 化。
    # stub 注入测试时 stub 可能无 run 属性；生产 kopf 必有。
    kopf.run(liveness_endpoint="http://0.0.0.0:8080/healthz")


if __name__ == "__main__":
    main()
