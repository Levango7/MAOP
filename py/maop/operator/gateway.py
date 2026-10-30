"""运行时网关：status 回写与子进程执行的默认实现（均可注入替换）.

测试注入 fake（见 ``tests/test_operator_m0.py``）；生产里
:class:`KubernetesStatusGateway` 惰性导入 ``kubernetes`` 客户端——本包不把它
放进 pyproject（避免 Lock drift 门与全量依赖膨胀），镜像内由
``deploy/k8s/operator/Dockerfile`` 显式安装。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Protocol, cast

logger = logging.getLogger(__name__)


class StatusGateway(Protocol):
    """status 子资源回写接口（控制器唯一需要的 k8s 能力）。"""

    async def patch_status(
        self, *, name: str, namespace: str, patch: dict[str, Any]
    ) -> None: ...


class AgentResolver(Protocol):
    """MaopAgent CR 解析接口（M1，可注入替换）。解析不到返回 ``None``。"""

    async def resolve_agent(
        self, *, name: str, namespace: str
    ) -> dict[str, Any] | None: ...


class KubernetesStatusGateway:
    """基于官方客户端的默认实现（惰性导入，调用时才需要装包）.

    M1 起同时实现 :class:`AgentResolver`：读取 MaopAgent CR 供 controller
    决定是否 pin 执行 agent。
    """

    def __init__(self) -> None:
        try:
            from kubernetes import client, config
        except ImportError as exc:  # pragma: no cover - 镜像内必装
            raise RuntimeError(
                "KubernetesStatusGateway requires the 'kubernetes' package "
                "(installed by deploy/k8s/operator/Dockerfile)"
            ) from exc
        try:
            config.load_incluster_config()
        except config.ConfigException:  # pragma: no cover - 本地调试走 kubeconfig
            config.load_kube_config()
        self._api = client.CustomObjectsApi()

    async def patch_status(
        self, *, name: str, namespace: str, patch: dict[str, Any]
    ) -> None:
        # 官方客户端是同步阻塞调用；patch 体很小，丢线程池即可。
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: self._api.patch_namespaced_custom_object_status(
                group="maop.io",
                version="v1alpha1",
                namespace=namespace,
                plural="maoptasks",
                name=name,
                body={"status": patch},
            ),
        )


    async def resolve_agent(
        self, *, name: str, namespace: str
    ) -> dict[str, Any] | None:
        """读取同名 MaopAgent CR；404 → ``None``（未声明），其余错误上抛（kopf 重试）."""
        from kubernetes.client.rest import ApiException

        def _get() -> Any:
            return self._api.get_namespaced_custom_object(
                group="maop.io",
                version="v1alpha1",
                namespace=namespace,
                plural="maopagents",
                name=name,
            )

        # 与 patch_status 同理：官方客户端是同步阻塞调用，丢线程池。
        loop = asyncio.get_running_loop()
        try:
            raw = await loop.run_in_executor(None, _get)
        except ApiException as exc:
            if exc.status == 404:
                return None
            raise
        return cast("dict[str, Any]", raw)


@dataclass(frozen=True)
class RunOutcome:
    """一次子进程执行的观测结果."""

    exit_code: int | None = None
    timed_out: bool = False
    cancelled: bool = False
    stdout_tail: str = ""
    stderr_tail: str = ""


class SubprocessRunner(Protocol):
    """命令执行接口（M0 用 asyncio 子进程，后续可换 Job/容器执行器）。"""

    async def run(
        self, command: list[str], *, timeout_s: float | None = None
    ) -> RunOutcome: ...


class AsyncSubprocessRunner:
    """默认 runner：``asyncio.create_subprocess_exec``，可选超时."""

    def __init__(self, *, tail_chars: int = 4000) -> None:
        self._tail_chars = tail_chars

    async def run(
        self, command: list[str], *, timeout_s: float | None = None
    ) -> RunOutcome:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return RunOutcome(exit_code=proc.returncode, timed_out=True)
        except asyncio.CancelledError:
            proc.kill()
            await proc.wait()
            raise

        def _tail(raw: bytes | None) -> str:
            text = (raw or b"").decode("utf-8", errors="replace")
            return text[-self._tail_chars :] if self._tail_chars else text

        return RunOutcome(
            exit_code=proc.returncode,
            stdout_tail=_tail(stdout),
            stderr_tail=_tail(stderr),
        )
