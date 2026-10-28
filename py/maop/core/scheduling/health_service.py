"""MAOP HealthService — 统一健康探测与下发.

取代分散在各模块的重复探测（AgentRegistry.health_check_all、
RedisAgentRegistry._probe_loop、Supervisor.patrol 等），提供单一
探测循环，结果统一写入 ``AgentCatalog.healthy``，供调度路径
（Dispatcher / DistributedScheduler）统一消费。

探测方式（按优先级降级）：
  1. CLI ``--version``（lifecycle agent，subprocess，无需网络）
  2. HTTP ``health_url``（A2A / HTTP adapter agent，httpx）
  3. TCP ping（兜底，socket connect）

结果写入：
  - ``AgentCatalog.update_health(name, healthy)`` — 元数据层健康标志
  - 内存缓存 ``_health_cache`` — 供 get_health 快速读取
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from maop.core.agent.registry.agent_catalog import AgentCatalog, AgentDescriptor

logger = logging.getLogger(__name__)


@dataclass
class ProbeResult:
    """单次探测结果."""

    agent_name: str
    healthy: bool
    latency_ms: int = 0
    error: str = ""
    probed_at: str = ""
    source: str = "health_service"


class HealthService:
    """统一健康探测服务.

    参数
    ----------
    catalog : AgentCatalog | None
        元数据注册中心；不传则使用 ``AgentCatalog.default()`` 单例。
    interval_s : float
        后台循环探测间隔（秒），默认 30s。
    probe_timeout_s : float
        单次探测超时（秒），默认 10s。
    """

    def __init__(
        self,
        catalog: AgentCatalog | None = None,
        *,
        interval_s: float = 30.0,
        probe_timeout_s: float = 10.0,
    ) -> None:
        self._catalog = catalog or AgentCatalog.default()
        self._interval_s = float(interval_s)
        self._probe_timeout_s = float(probe_timeout_s)
        self._running = False
        self._loop_task: asyncio.Task | None = None
        self._health_cache: dict[str, ProbeResult] = {}
        self._cache_lock = asyncio.Lock()

    # ── 生命周期 ──────────────────────────────────────────────────

    async def start(self) -> None:
        """启动后台探测循环。幂等：已运行时无-op。"""
        if self._running:
            return
        self._running = True
        self._loop_task = asyncio.create_task(self._loop())
        logger.info("[health-service] started (interval=%.1fs)", self._interval_s)

    async def stop(self) -> None:
        """停止后台探测循环。幂等。"""
        self._running = False
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass  # silent-ok: interrupt — stop() 主动取消自身循环任务
            self._loop_task = None
        logger.info("[health-service] stopped")

    async def _loop(self) -> None:
        """后台循环：每 interval_s 探测一次全部 enabled agent。"""
        while self._running:
            try:
                await self.probe_all_enabled()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[health-service] probe loop iteration failed")
            try:
                await asyncio.wait_for(
                    asyncio.sleep(self._interval_s),
                    timeout=self._interval_s,
                )
            except asyncio.TimeoutError:
                pass  # silent-ok: expected-signal — wait_for 超时代替 sleep 等一轮间隔

    # ── 公共 API ──────────────────────────────────────────────────

    async def probe_agent(self, agent_name: str) -> ProbeResult:
        """探测单个 agent，并将结果写入 AgentCatalog + 内存缓存。"""
        result = await self._probe_once(agent_name)
        await self._persist(agent_name, result)
        async with self._cache_lock:
            self._health_cache[agent_name] = result
        return result

    async def probe_all_enabled(self) -> list[ProbeResult]:
        """并发探测全部 enabled agent，结果写入 AgentCatalog + 缓存。"""
        agents = self._catalog.list_enabled()
        if not agents:
            return []

        sem = asyncio.Semaphore(10)

        async def _one(desc: AgentDescriptor) -> ProbeResult:
            async with sem:
                return await self.probe_agent(desc.name)

        results = await asyncio.gather(*[_one(d) for d in agents])
        return results

    async def get_health(self, agent_name: str) -> ProbeResult | None:
        """获取 agent 健康状态。优先返回内存缓存，缓存未命中返回 None。"""
        async with self._cache_lock:
            return self._health_cache.get(agent_name)

    async def list_unhealthy(self) -> list[ProbeResult]:
        """列出当前 unhealthy 的 agent。"""
        async with self._cache_lock:
            return [r for r in self._health_cache.values() if not r.healthy]

    def is_healthy(self, agent_name: str) -> bool | None:
        """同步读取缓存中的健康状态；无缓存返回 None。

        供同步调用路径（Dispatcher）使用，不触发新探测。
        """
        result = self._health_cache.get(agent_name)
        if result is None:
            return None
        return result.healthy

    # ── 内部：单次探测 ────────────────────────────────────────────

    async def _probe_once(self, agent_name: str) -> ProbeResult:
        desc = self._catalog.get(agent_name)
        if desc is None:
            return ProbeResult(
                agent_name=agent_name,
                healthy=False,
                error="agent not found in catalog",
                probed_at=self._now_iso(),
            )

        probed_at = self._now_iso()

        # 1. CLI --version（lifecycle / adapter agent）
        cli_path = getattr(desc, "adapter_config", {}).get("cli_path") or ""
        if cli_path:
            try:
                ok, latency = await asyncio.wait_for(
                    asyncio.to_thread(self._cli_ping, cli_path),
                    timeout=self._probe_timeout_s,
                )
                return ProbeResult(
                    agent_name=agent_name,
                    healthy=ok,
                    latency_ms=latency,
                    probed_at=probed_at,
                    source="cli",
                )
            except asyncio.TimeoutError:
                return ProbeResult(
                    agent_name=agent_name,
                    healthy=False,
                    latency_ms=int(self._probe_timeout_s * 1000),
                    error="cli timeout",
                    probed_at=probed_at,
                    source="cli",
                )
            except Exception as exc:
                logger.debug("[health-service] cli probe %s failed: %s", agent_name, exc)

        # 2. HTTP health_url（A2A / HTTP adapter）
        health_url = getattr(desc, "adapter_config", {}).get("health_url") or ""
        if health_url:
            try:
                ok, latency = await asyncio.wait_for(
                    self._http_ping(health_url),
                    timeout=self._probe_timeout_s,
                )
                return ProbeResult(
                    agent_name=agent_name,
                    healthy=ok,
                    latency_ms=latency,
                    probed_at=probed_at,
                    source="http",
                )
            except asyncio.TimeoutError:
                return ProbeResult(
                    agent_name=agent_name,
                    healthy=False,
                    latency_ms=int(self._probe_timeout_s * 1000),
                    error="http timeout",
                    probed_at=probed_at,
                    source="http",
                )
            except Exception as exc:
                logger.debug("[health-service] http probe %s failed: %s", agent_name, exc)

        # 3. TCP ping（兜底）
        host = getattr(desc, "adapter_config", {}).get("host", "")
        port = int(getattr(desc, "adapter_config", {}).get("port", 0) or 0)
        if host and port:
            try:
                ok, latency = await asyncio.wait_for(
                    self._tcp_ping(host, port),
                    timeout=self._probe_timeout_s,
                )
                return ProbeResult(
                    agent_name=agent_name,
                    healthy=ok,
                    latency_ms=latency,
                    probed_at=probed_at,
                    source="tcp",
                )
            except asyncio.TimeoutError:
                return ProbeResult(
                    agent_name=agent_name,
                    healthy=False,
                    latency_ms=int(self._probe_timeout_s * 1000),
                    error="tcp timeout",
                    probed_at=probed_at,
                    source="tcp",
                )
            except Exception as exc:
                logger.debug("[health-service] tcp probe %s failed: %s", agent_name, exc)

        # 无可用探测方式 → 标记 unknown（不算 unhealthy，避免误杀）
        return ProbeResult(
            agent_name=agent_name,
            healthy=True,
            probed_at=probed_at,
            source="none",
            error="no probe method configured",
        )

    @staticmethod
    def _cli_ping(cli_path: str) -> tuple[bool, int]:
        """同步执行 CLI --version，返回 (healthy, latency_ms)。"""
        start = time.monotonic()
        try:
            proc = subprocess.run(
                [cli_path, "--version"],
                capture_output=True,
                check=False,
                text=True,
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0,
            )
            latency_ms = int((time.monotonic() - start) * 1000)
            return (proc.returncode == 0 or proc.returncode == 1, latency_ms)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            latency_ms = int((time.monotonic() - start) * 1000)
            return (False, latency_ms)
        except (OSError, ValueError, subprocess.SubprocessError):
            # 与上一分支同处置，但保留可区分的异常集：CLI 缺失/不可执行是 OSError，
            # creationflags 等参数不合法是 ValueError，其余 subprocess 故障是
            # SubprocessError。原来的 ``except Exception`` 会连编程错误一起吞掉。
            latency_ms = int((time.monotonic() - start) * 1000)
            return (False, latency_ms)

    @staticmethod
    async def _http_ping(health_url: str) -> tuple[bool, int]:
        """HTTP GET health_url，返回 (healthy, latency_ms)。"""
        import httpx

        start = time.monotonic()
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(health_url)
        latency_ms = int((time.monotonic() - start) * 1000)
        return (200 <= resp.status_code < 300, latency_ms)

    @staticmethod
    async def _tcp_ping(host: str, port: int) -> tuple[bool, int]:
        """TCP connect 兜底探测，返回 (healthy, latency_ms)。"""
        start = time.monotonic()
        _, writer = await asyncio.open_connection(host, port)
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, RuntimeError):
            # wait_closed 实际只抛 OSError（连接被重置/管道断裂）与 RuntimeError
            # （事件循环已关闭）；收窄后不再吞掉不相关的编程错误。
            pass  # silent-ok: best-effort-cleanup — writer 已 close，关闭回执失败不影响探测结果
        latency_ms = int((time.monotonic() - start) * 1000)
        return (True, latency_ms)

    # ── 持久化 ────────────────────────────────────────────────────

    async def _persist(self, agent_name: str, result: ProbeResult) -> None:
        """将探测结果写入 AgentCatalog（健康标志 + 检查时间戳）。"""
        try:
            self._catalog.update_health(agent_name, result.healthy)
        except Exception as exc:
            logger.warning(
                "[health-service] failed to persist health for %s: %s", agent_name, exc
            )

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()


# ── 进程级单例 ────────────────────────────────────────────────────

_health_service_instance: HealthService | None = None
_health_service_lock = __import__("threading").Lock()


def get_health_service() -> HealthService:
    """获取进程级 HealthService 单例。"""
    global _health_service_instance
    if _health_service_instance is None:
        with _health_service_lock:
            if _health_service_instance is None:
                _health_service_instance = HealthService()
    return _health_service_instance


def reset_health_service() -> None:
    """重置单例（供测试隔离使用）。"""
    global _health_service_instance
    with _health_service_lock:
        _health_service_instance = None
