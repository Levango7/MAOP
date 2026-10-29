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
    # stub 注入测试时 stub 可能无 run 属性；生产 kopf 必有。
    kopf.run()  # type: ignore[attr-defined]


if __name__ == "__main__":
    main()
