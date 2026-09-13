"""MAOP 健康检查调度器 — 定期自动探测 Agent 健康状态.

后台守护线程按固定间隔调用各 Agent 适配器的 ``health_check()``，
将结果同步到 ``AgentCatalog.update_health()``，使路由器始终基于
最新的健康标志进行决策，避免选到已宕机的 Agent.

核心组件：
  - ``HealthStatus``            — 单次健康检查结果（Pydantic 模型）
  - ``HealthCheckScheduler``    — 后台调度器

Usage::

    from maop.core.agent.router.health_check_scheduler import HealthCheckScheduler

    scheduler = HealthCheckScheduler(catalog=catalog, adapters={"cli": adapter})
    scheduler.start()          # 启动后台线程（立即返回）
    ...
    scheduler.stop()           # 停止后台线程

    # 同步执行一轮检查（用于测试）
    scheduler.check_once()
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from maop.core.agent.delegation.agent_proxy import AgentAdapter
from maop.core.agent.registry.agent_catalog import AgentCatalog

logger = logging.getLogger(__name__)


class HealthStatus(BaseModel):
    """单次健康检查结果快照.

    由 ``HealthCheckScheduler.check_agent`` 在每次检查后更新，
    通过 ``get_status`` 获取各 Agent 最新快照。
    """

    agent_name: str = Field(..., description="Agent 唯一标识")
    healthy: bool = Field(..., description="是否健康")
    last_check: str = Field(default="", description="上次检查时间（ISO 8601 UTC）")
    latency_ms: int = Field(default=0, ge=0, description="检查耗时（毫秒）")
    error: str = Field(default="", description="错误信息，空表示无错误")


class HealthCheckScheduler:
    """Agent 健康检查后台调度器.

    定期遍历 ``AgentCatalog`` 中所有注册 Agent，调用对应适配器的
    ``health_check()``，将结果回写到 ``AgentCatalog.update_health()``.

    线程安全：使用 ``threading.RLock`` 保护内部状态（``_statuses`` / ``_thread``），
    使用 ``threading.Event`` 控制后台线程停止.

    异常安全：单个 Agent 检查失败（适配器抛异常或超时）不影响其他 Agent.

    Parameters
    ----------
    catalog : AgentCatalog
        Agent 注册中心.
    adapters : dict[str, AgentAdapter]
        agent_name → AgentAdapter 映射.
    check_interval_s : float
        检查间隔秒数（默认 60 秒）.
    timeout_s : float
        单次健康检查超时秒数（默认 10 秒）.
    max_leaked_threads : int
        允许并发存在的超时泄漏 daemon 线程上限（默认 16）.
        超过该阈值时跳过新的健康检查，避免 ``adapter.health_check()``
        长期阻塞导致线程无限累积（P2 资源泄漏修复）.
    """

    def __init__(
        self,
        catalog: AgentCatalog,
        adapters: dict[str, AgentAdapter],
        check_interval_s: float = 60.0,
        timeout_s: float = 10.0,
        max_leaked_threads: int = 16,
    ) -> None:
        self._catalog: AgentCatalog = catalog
        self._adapters: dict[str, AgentAdapter] = adapters
        self._check_interval_s: float = check_interval_s
        self._timeout_s: float = timeout_s

        # 线程安全原语
        self._stop_event: threading.Event = threading.Event()
        self._lock: threading.RLock = threading.RLock()

        # 后台线程引用
        self._thread: threading.Thread | None = None

        # 各 Agent 最新健康状态快照（agent_name → HealthStatus）
        self._statuses: dict[str, HealthStatus] = {}

        # P2 修复：跟踪超时后仍存活的 daemon worker 线程，限制并发泄漏上限
        self._max_leaked_threads: int = max_leaked_threads
        self._leaked_workers: list[threading.Thread] = []

    # ── 后台线程控制 ──────────────────────────────────────────────
    def start(self) -> None:
        """启动后台健康检查线程.

        幂等：若线程已存活则直接返回，不创建多个线程.
        线程设为 daemon=True，不阻止进程退出.
        start() 立即返回，检查在后台线程执行.
        """
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                logger.debug("[health_check] 已在运行，跳过重复 start")
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                daemon=True,
                name="health-check-scheduler",
            )
            self._thread.start()
            logger.info("[health_check] 后台线程已启动")

    def stop(self) -> None:
        """停止后台健康检查线程.

        设置停止事件并等待线程退出（最多等待 timeout_s + 5 秒）.
        未启动时调用无副作用.
        """
        with self._lock:
            self._stop_event.set()
            thread = self._thread
        if thread is not None:
            # 锁外 join，避免死锁（后台线程可能在 check_agent 中获取锁）
            thread.join(timeout=self._timeout_s + 5.0)
            with self._lock:
                self._thread = None
            logger.info("[health_check] 后台线程已停止")

    def _run_loop(self) -> None:
        """后台线程主循环：定期执行健康检查.

        循环退出条件：``_stop_event`` 被设置.
        使用 ``_stop_event.wait(timeout=check_interval_s)`` 实现可中断的间隔等待.
        """
        while not self._stop_event.is_set():
            try:
                self.check_once()
            except Exception as exc:
                # 兜底：check_once 内部已逐 Agent 容错，此处仅防意外
                logger.error("[health_check] check_once 异常: %s", exc)
            # 可中断的间隔等待
            self._stop_event.wait(timeout=self._check_interval_s)

    # ── 健康检查执行 ──────────────────────────────────────────────
    def check_once(self) -> None:
        """执行一轮健康检查：遍历所有注册 Agent 并逐个检查.

        同步执行，主要用于测试或手动触发.
        单个 Agent 检查失败不影响其他 Agent.
        """
        agents = self._catalog.list_all()
        for agent in agents:
            try:
                self.check_agent(agent.name)
            except Exception as exc:
                logger.warning(
                    "[health_check] 检查 Agent %r 时异常: %s", agent.name, exc
                )

    def check_agent(self, agent_name: str) -> bool:
        """检查单个 Agent 健康状态.

        流程：
          1. 从 ``_adapters`` 查找适配器；无适配器则跳过（返回 False，不报错）
          2. 在子线程中调用 ``adapter.health_check()``，主线程等待 join(timeout)
          3. 超时则标记 unhealthy，错误信息含 "timeout"
          4. 适配器抛异常则捕获，标记 unhealthy
          5. 将结果回写到 ``catalog.update_health()``
          6. 更新内部 ``_statuses`` 快照

        Parameters
        ----------
        agent_name : str
            Agent 唯一标识.

        Returns
        -------
        bool
            True 表示健康，False 表示不健康 / 无适配器 / 超时 / 异常.
        """
        adapter = self._adapters.get(agent_name)
        if adapter is None:
            # 无适配器：跳过，不报错，不更新 catalog
            logger.debug("[health_check] Agent %r 无适配器，跳过", agent_name)
            return False

        # P2 修复：清理已结束的泄漏线程，检查当前并发泄漏数；
        # 超过阈值时跳过本次检查，避免线程无限累积导致资源泄漏
        with self._lock:
            self._leaked_workers = [
                t for t in self._leaked_workers if t.is_alive()
            ]
            leaked_count = len(self._leaked_workers)
        if leaked_count >= self._max_leaked_threads:
            logger.warning(
                "[health_check] 泄漏 daemon 线程数已达上限 %d/%d，跳过 Agent %r 健康检查",
                leaked_count, self._max_leaked_threads, agent_name,
            )
            # 标记为不健康并回写 catalog，使路由器感知该 Agent 不可用
            try:
                self._catalog.update_health(agent_name, False)
            except Exception as exc:
                logger.error(
                    "[health_check] 跳过检查时更新 catalog 失败 (%s): %s",
                    agent_name, exc,
                )
            with self._lock:
                self._statuses[agent_name] = HealthStatus(
                    agent_name=agent_name,
                    healthy=False,
                    last_check=datetime.now(timezone.utc).isoformat(),
                    latency_ms=0,
                    error=f"skipped: leaked threads {leaked_count}/{self._max_leaked_threads}",
                )
            return False

        start_mono = time.monotonic()
        healthy = False
        error_msg = ""

        # 在子线程中执行 health_check，实现超时保护
        result_box: dict[str, Any] = {"healthy": False, "error": ""}
        worker = threading.Thread(
            target=self._invoke_health_check,
            args=(adapter, result_box),
            daemon=True,
        )
        worker.start()
        worker.join(timeout=self._timeout_s)

        if worker.is_alive():
            # 子线程仍在运行 → 超时（daemon 线程随主进程退出，无需手动终止）
            healthy = False
            error_msg = f"timeout after {self._timeout_s}s"
            # P2 修复：记录泄漏 daemon 线程，限制并发泄漏上限
            with self._lock:
                # 先清理已结束的泄漏线程，再追加当前超时线程
                self._leaked_workers = [
                    t for t in self._leaked_workers if t.is_alive()
                ]
                self._leaked_workers.append(worker)
                leaked = len(self._leaked_workers)
            logger.warning(
                "[health_check] Agent %r 健康检查超时，当前泄漏 daemon 线程 %d/%d",
                agent_name, leaked, self._max_leaked_threads,
            )
        else:
            healthy = result_box["healthy"]
            error_msg = result_box["error"]

        latency_ms = int((time.monotonic() - start_mono) * 1000)

        # 回写到 catalog
        try:
            self._catalog.update_health(agent_name, healthy)
        except Exception as exc:
            error_msg = f"catalog update failed: {exc}"
            logger.error(
                "[health_check] 更新 catalog 健康 state 失败 (%s): %s",
                agent_name,
                exc,
            )

        # 更新内部状态快照
        status = HealthStatus(
            agent_name=agent_name,
            healthy=healthy,
            last_check=datetime.now(timezone.utc).isoformat(),
            latency_ms=latency_ms,
            error=error_msg,
        )
        with self._lock:
            self._statuses[agent_name] = status

        return healthy

    @staticmethod
    def _invoke_health_check(
        adapter: AgentAdapter, result_box: dict[str, Any]
    ) -> None:
        """在子线程中调用适配器 health_check，捕获异常写入 result_box.

        异常安全：任何异常都被捕获并记录到 result_box["error"]，
        不会向子线程外抛出（子线程异常无法被 join 捕获）.
        """
        try:
            result_box["healthy"] = adapter.health_check()
        except Exception as exc:
            result_box["healthy"] = False
            result_box["error"] = f"{type(exc).__name__}: {exc}"

    # ── 状态查询 ──────────────────────────────────────────────────
    def get_status(self) -> dict[str, HealthStatus]:
        """获取各 Agent 最新健康状态快照.

        Returns
        -------
        dict[str, HealthStatus]
            agent_name → HealthStatus 映射（返回副本，调用方修改不影响内部状态）.
        """
        with self._lock:
            return dict(self._statuses)