"""MAOP 个人版 48h 长稳压测脚本（P1-4 交付门禁）。

在持续负载下采样四项关键指标，验证 MAOP 个人版长稳性：

1. 内存 RSS（psutil.Process().memory_info().rss，单位 bytes）
2. 文件句柄数（psutil.Process().num_handles() on Windows /
   num_fds() on POSIX）
3. 连接池占用（maop.core.backends.db_utils._pools 中所有
   ConnectionPool 的当前空闲连接数总和）
4. CPU 使用率（psutil.Process().cpu_percent(interval=1)）

负载生成：
- 高频记忆读写：每秒 10-20 次 add/search（独立工作线程，调用真实
  MemoryFacade API，不 mock）
- Agent 编排：每 5 秒一次小 DAG（asyncio，Engine + mock LLM
  step_executor，不实际调 LLM API）

输出：
- CSV 数据：deliverables/soak-test-data.csv
- 汇总统计 + 趋势分析（线性回归斜率）打印到 stdout 并写入
  deliverables/soak-test-summary.json

用法::

    # 48h 完整测试（用户自行运行）
    python py/tests/soak/soak_test.py --duration 172800

    # 15 分钟冒烟测试
    python py/tests/soak/soak_test.py --duration 900

    # 自定义参数
    python py/tests/soak/soak_test.py --duration 600 --sample-interval 10 \
        --mem-rps 15 --dag-interval 3

注意：脚本独立可运行，不依赖 pytest fixture。SIGINT/SIGTERM
优雅退出，输出最终报告。
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
import random
import signal
import statistics
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── 项目根目录定位 ──────────────────────────────────────────────
# 脚本位于 <root>/py/tests/soak/soak_test.py，向上 3 级是项目根。
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[2]  # F:\Nexus\MAOP
PY_DIR = SCRIPT_DIR.parents[1]        # F:\Nexus\MAOP\py

# 把 py/ 加入 sys.path 让 `import maop` 可用（独立运行不依赖 pytest）
if str(PY_DIR) not in sys.path:
    sys.path.insert(0, str(PY_DIR))

# ── 测试环境变量（与 conftest.py 对齐，避免 auth/限流干扰）──────
os.environ.setdefault("MAOP_ENV", "test")
os.environ.setdefault("MAOP_AUTH", "0")
os.environ.setdefault("MAOP_AUTH_DISABLED_ADMIN", "1")
os.environ.setdefault("MAOP_RATE_LIMIT", "0")
os.environ.setdefault("MAOP_RATE_LIMIT_ENABLED", "0")
# 隔离数据目录，避免污染开发库
os.environ.setdefault("MAOP_DATA_DIR", str(PROJECT_ROOT / "py" / "data"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

# ── psutil 可选导入 ─────────────────────────────────────────────
try:
    import psutil
    _PSUTIL_OK = True
except ImportError:
    _PSUTIL_OK = False


logger = logging.getLogger("soak_test")


# ════════════════════════════════════════════════════════════════
# 指标采样
# ════════════════════════════════════════════════════════════════


@dataclass
class Sample:
    """单次采样快照。"""

    timestamp: float           # unix 时间戳
    elapsed_s: float           # 自测试开始的秒数
    rss_bytes: int             # 进程 RSS（bytes）
    handle_count: int          # 文件句柄数
    pool_size: int             # 连接池占用（空闲连接数总和）
    cpu_percent: float         # CPU 使用率（%）
    mem_ops_total: int = 0     # 累计记忆操作次数
    dag_runs_total: int = 0    # 累计 DAG 执行次数


@dataclass
class SoakConfig:
    """压测配置。"""

    duration_s: int = 48 * 3600       # 测试时长（秒）
    sample_interval_s: int = 30       # 采样间隔（秒）
    mem_rps: int = 15                 # 记忆读写次数/秒（10-20 之间）
    dag_interval_s: int = 5           # DAG 执行间隔（秒）
    leak_threshold_mb_per_h: float = 1.0  # 内存泄漏告警阈值（MB/h）
    csv_path: Path = field(default_factory=lambda: PROJECT_ROOT / "deliverables" / "soak-test-data.csv")
    summary_path: Path = field(default_factory=lambda: PROJECT_ROOT / "deliverables" / "soak-test-summary.json")


def get_process() -> psutil.Process:
    """获取当前进程的 psutil.Process 句柄。"""
    return psutil.Process(os.getpid())


def get_handle_count(proc: psutil.Process) -> int:
    """获取进程文件句柄数（Windows: num_handles / POSIX: num_fds）。"""
    fn = getattr(proc, "num_handles", None) or getattr(proc, "num_fds", None)
    if fn is None:
        return -1
    try:
        return int(fn())
    except Exception:
        return -1


def get_pool_size() -> int:
    """获取 maop ConnectionPool 当前空闲连接数总和。

    遍历 maop.core.backends.db_utils._pools 中所有 ConnectionPool，
    返回各池 _pool 列表长度之和。若模块未导入或访问失败，返回 -1。
    """
    try:
        from maop.core.backends.db_utils import _pools  # type: ignore
        total = 0
        for pool in _pools.values():
            try:
                total += len(pool._pool)  # type: ignore[attr-defined]
            except Exception:
                pass
        return total
    except Exception:
        return -1


def sample_once(proc: psutil.Process, start_time: float, mem_ops: int, dag_runs: int) -> Sample:
    """采集一次指标快照。

    cpu_percent(interval=1) 会阻塞 1 秒，因此采样实际耗时 ≥ 1s。
    """
    mem_info = proc.memory_info()
    rss = int(mem_info.rss)
    handles = get_handle_count(proc)
    pool_size = get_pool_size()
    cpu = float(proc.cpu_percent(interval=1.0))
    now = time.time()
    return Sample(
        timestamp=now,
        elapsed_s=now - start_time,
        rss_bytes=rss,
        handle_count=handles,
        pool_size=pool_size,
        cpu_percent=cpu,
        mem_ops_total=mem_ops,
        dag_runs_total=dag_runs,
    )


# ════════════════════════════════════════════════════════════════
# 负载生成 — 记忆读写（独立线程）
# ════════════════════════════════════════════════════════════════


class MemoryLoadWorker:
    """高频记忆读写工作线程。

    每秒执行 ``rps`` 次 add/search，调用真实 MemoryFacade API。
    用一个共享 ``stop_event`` 控制退出。
    """

    def __init__(self, root_dir: Path, rps: int, stop_event: threading.Event) -> None:
        self._root = root_dir
        self._rps = rps
        self._stop = stop_event
        self._thread: threading.Thread | None = None
        self._ops_count = 0
        self._ops_lock = threading.Lock()
        self._mem: Any = None
        self._error: Exception | None = None

    @property
    def ops_count(self) -> int:
        with self._ops_lock:
            return self._ops_count

    @property
    def error(self) -> Exception | None:
        return self._error

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="mem-load", daemon=True)
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        try:
            from maop.memory.facade import MemoryFacade

            self._mem = MemoryFacade(root_dir=self._root, mode="agent")
        except Exception as exc:
            self._error = exc
            logger.exception("MemoryFacade 初始化失败")
            return

        rng = random.Random()
        # 预置任务主题池，让 search 命中率合理
        topics = [
            "fix login timeout", "refactor auth module", "optimize db query",
            "add unit tests", "update api docs", "fix memory leak",
            "improve cache hit rate", "handle edge case", "cleanup dead code",
            "upgrade dependency",
        ]
        # 写入一批种子数据，让 search 不至于空结果
        try:
            for t in topics:
                self._mem.short_term_store(
                    f"seed entry for {t}",
                    task=t, agent="soak-seed", topic=t, tags=["seed"],
                )
        except Exception:
            logger.debug("seed store 失败", exc_info=True)

        interval = 1.0 / max(1, self._rps)
        while not self._stop.is_set():
            try:
                # 50% 写入 / 50% 搜索
                if rng.random() < 0.5:
                    topic = rng.choice(topics)
                    suffix = rng.randint(0, 1_000_000)
                    self._mem.short_term_store(
                        f"soak entry {suffix} for {topic}",
                        task=topic, agent="soak", topic=topic,
                        tags=["soak", str(suffix % 10)],
                    )
                else:
                    query = rng.choice(topics)
                    self._mem.short_term_search(query, top=5)
                with self._ops_lock:
                    self._ops_count += 1
            except Exception as exc:
                # 单次操作失败不致命，记录后继续
                logger.debug("mem op 失败: %s", exc, exc_info=True)
            # 精确节流：sleep interval
            self._stop.wait(interval)


# ════════════════════════════════════════════════════════════════
# 负载生成 — Agent 编排（asyncio）
# ════════════════════════════════════════════════════════════════


async def _mock_step_executor(
    step: Any, context: dict[str, Any], workdir: str, trace_id: str,
) -> Any:
    """Mock LLM step executor：返回固定成功响应，不调真实 LLM API。"""
    from maop.engine_types import StepResult, StepStatus  # type: ignore
    # 模拟一点处理延迟，让 DAG 不至于空转
    await asyncio.sleep(0.05)
    return StepResult(
        id=step.id,
        status=StepStatus.SUCCESS,
        output=f"mock-output-{step.id}-{step.agent}",
        exit_code=0,
    )


def build_small_dag(dag_id: str) -> list[Any]:
    """构造一个小型 DAG（5 个 step）用于压测。

    结构：
        s1 (agent) ─┬─> s2 (agent) ─┬─> s4 (verify) ─> s5 (terminal)
                    └─> s3 (agent) ──┘
    """
    from maop.engine_types import StepType, WorkflowStep

    return [
        WorkflowStep(id=f"{dag_id}-s1", type=StepType.AGENT, agent="mock",
                     task="analyze input", timeout=10),
        WorkflowStep(id=f"{dag_id}-s2", type=StepType.AGENT, agent="mock",
                     task="implement plan", depends_on=[f"{dag_id}-s1"], timeout=10),
        WorkflowStep(id=f"{dag_id}-s3", type=StepType.AGENT, agent="mock",
                     task="parallel research", depends_on=[f"{dag_id}-s1"], timeout=10),
        WorkflowStep(id=f"{dag_id}-s4", type=StepType.VERIFY,
                     task="verify outputs", depends_on=[f"{dag_id}-s2", f"{dag_id}-s3"],
                     timeout=10),
        WorkflowStep(id=f"{dag_id}-s5", type=StepType.TERMINAL,
                     depends_on=[f"{dag_id}-s4"], timeout=10),
    ]


async def dag_load_loop(
    stop_event: threading.Event,
    interval_s: int,
    dag_runs_holder: list[int],
) -> None:
    """每 ``interval_s`` 秒执行一个小 DAG。"""
    from maop.engine import Engine

    engine = Engine(step_executor=_mock_step_executor)
    dag_idx = 0
    while not stop_event.is_set():
        try:
            steps = build_small_dag(f"dag{dag_idx}")
            result = await engine.run(steps, context={"task": f"soak-dag-{dag_idx}"})
            if result.success:
                dag_runs_holder[0] += 1
            else:
                logger.debug("DAG %d 失败", dag_idx)
        except Exception as exc:
            logger.debug("DAG %d 异常: %s", dag_idx, exc, exc_info=True)
        dag_idx += 1
        # 用 asyncio.sleep + stop_event 检查实现可中断等待
        for _ in range(interval_s * 10):
            if stop_event.is_set():
                return
            await asyncio.sleep(0.1)


# ════════════════════════════════════════════════════════════════
# 趋势分析 — 线性回归
# ════════════════════════════════════════════════════════════════


def linear_regression(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """简单最小二乘线性回归，返回 (slope, intercept)。

    若样本不足或 x 方差为 0，返回 (0.0, mean(ys))。
    """
    n = len(xs)
    if n < 2:
        return 0.0, (ys[0] if ys else 0.0)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0:
        return 0.0, mean_y
    slope = num / den
    intercept = mean_y - slope * mean_x
    return slope, intercept


def rss_slope_mb_per_hour(samples: list[Sample]) -> float:
    """计算 RSS 随时间的增长斜率，单位 MB/h。"""
    if not samples:
        return 0.0
    xs = [s.elapsed_s for s in samples]
    ys = [float(s.rss_bytes) for s in samples]
    slope_b_per_s, _ = linear_regression(xs, ys)
    # bytes/s → MB/h：× 3600 / 1024 / 1024
    return slope_b_per_s * 3600.0 / (1024 * 1024)


# ════════════════════════════════════════════════════════════════
# 汇总统计
# ════════════════════════════════════════════════════════════════


@dataclass
class MetricSummary:
    """单项指标汇总。"""

    name: str
    unit: str
    min_val: float
    max_val: float
    avg_val: float
    final_val: float
    slope: float          # 随时间的变化率（unit/s）
    slope_display: str    # 人类可读斜率（如 "1.23 MB/h"）
    leak_suspect: bool    # 是否怀疑泄漏

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "unit": self.unit,
            "min": self.min_val,
            "max": self.max_val,
            "avg": self.avg_val,
            "final": self.final_val,
            "slope": self.slope,
            "slope_display": self.slope_display,
            "leak_suspect": self.leak_suspect,
        }


# 短时测试不足以判定泄漏——Python 进程 JIT/缓存/GC 预热会放大斜率。
# 仅当测试时长 ≥ 此阈值时才标记 leak_suspect。
MIN_DURATION_FOR_LEAK_S: int = 3600  # 1 小时


def summarize(
    samples: list[Sample],
    leak_threshold_mb_per_h: float,
    min_duration_for_leak_s: int = MIN_DURATION_FOR_LEAK_S,
) -> dict[str, Any]:
    """计算所有指标的汇总统计。

    Parameters
    ----------
    samples : list[Sample]
        采样列表。
    leak_threshold_mb_per_h : float
        内存 RSS 泄漏告警阈值（MB/h）。
    min_duration_for_leak_s : int
        标记泄漏疑似的最低测试时长（秒）。低于此时长只报告斜率，
        不标记 ``leak_suspect``，避免短时测试的预热效应误报。
    """
    if not samples:
        return {"error": "no samples"}

    duration_s = samples[-1].elapsed_s
    leak_eligible = duration_s >= min_duration_for_leak_s

    def _summarize(name: str, unit: str, values: list[float],
                   slope_per_s: float, slope_display: str,
                   leak: bool = False) -> MetricSummary:
        # 短时测试不标记泄漏
        if not leak_eligible:
            leak = False
        return MetricSummary(
            name=name, unit=unit,
            min_val=min(values),
            max_val=max(values),
            avg_val=statistics.mean(values),
            final_val=values[-1],
            slope=slope_per_s,
            slope_display=slope_display,
            leak_suspect=leak,
        )

    rss_vals = [float(s.rss_bytes) for s in samples]
    handle_vals = [float(s.handle_count) for s in samples if s.handle_count >= 0]
    pool_vals = [float(s.pool_size) for s in samples if s.pool_size >= 0]
    cpu_vals = [s.cpu_percent for s in samples]
    xs = [s.elapsed_s for s in samples]

    # RSS 斜率：bytes/s → MB/h
    rss_slope_b_per_s, _ = linear_regression(xs, rss_vals)
    rss_slope_mb_per_h = rss_slope_b_per_s * 3600.0 / (1024 * 1024)
    rss_summary = _summarize(
        "memory_rss", "bytes", rss_vals,
        rss_slope_b_per_s,
        f"{rss_slope_mb_per_h:.4f} MB/h",
        leak=(rss_slope_mb_per_h > leak_threshold_mb_per_h),
    )

    # 句柄斜率：个/s → 个/h
    h_slope = 0.0
    h_summary: MetricSummary | None = None
    if handle_vals:
        h_slope, _ = linear_regression(xs[:len(handle_vals)], handle_vals)
        h_summary = _summarize(
            "file_handles", "count", handle_vals,
            h_slope,
            f"{h_slope * 3600:.4f} handles/h",
            leak=(h_slope * 3600 > 10),  # >10 handles/h 视为泄漏
        )

    # 连接池斜率
    p_slope = 0.0
    p_summary: MetricSummary | None = None
    if pool_vals:
        p_slope, _ = linear_regression(xs[:len(pool_vals)], pool_vals)
        p_summary = _summarize(
            "conn_pool", "count", pool_vals,
            p_slope,
            f"{p_slope * 3600:.4f} conns/h",
            leak=(p_slope * 3600 > 1),  # >1 conn/h 视为泄漏
        )

    # CPU 斜率
    c_slope, _ = linear_regression(xs, cpu_vals)
    c_summary = _summarize(
        "cpu_percent", "%", cpu_vals,
        c_slope,
        f"{c_slope * 3600:.4f} %/h",
        leak=False,  # CPU 不算泄漏
    )

    overall_pass = not any(
        m.leak_suspect for m in [rss_summary, h_summary, p_summary, c_summary]
        if m is not None
    )

    return {
        "sample_count": len(samples),
        "duration_s": duration_s,
        "leak_eligible": leak_eligible,
        "min_duration_for_leak_s": min_duration_for_leak_s,
        "mem_ops_total": samples[-1].mem_ops_total,
        "dag_runs_total": samples[-1].dag_runs_total,
        "metrics": {
            "memory_rss": rss_summary.to_dict(),
            "file_handles": h_summary.to_dict() if h_summary else None,
            "conn_pool": p_summary.to_dict() if p_summary else None,
            "cpu_percent": c_summary.to_dict(),
        },
        "overall_pass": overall_pass,
        "leak_threshold_mb_per_h": leak_threshold_mb_per_h,
    }


# ════════════════════════════════════════════════════════════════
# CSV 写入
# ════════════════════════════════════════════════════════════════


CSV_FIELDS = [
    "timestamp", "elapsed_s",
    "rss_bytes", "handle_count", "pool_size", "cpu_percent",
    "mem_ops_total", "dag_runs_total",
]


def csv_init(path: Path) -> None:
    """初始化 CSV 文件，写入表头。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()


def csv_append(path: Path, sample: Sample) -> None:
    """追加一行采样数据到 CSV。"""
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writerow({
            "timestamp": f"{sample.timestamp:.3f}",
            "elapsed_s": f"{sample.elapsed_s:.3f}",
            "rss_bytes": sample.rss_bytes,
            "handle_count": sample.handle_count,
            "pool_size": sample.pool_size,
            "cpu_percent": f"{sample.cpu_percent:.4f}",
            "mem_ops_total": sample.mem_ops_total,
            "dag_runs_total": sample.dag_runs_total,
        })


# ════════════════════════════════════════════════════════════════
# ASCII 趋势图
# ════════════════════════════════════════════════════════════════


def ascii_sparkline(values: list[float], width: int = 60) -> str:
    """把数值序列渲染为 ASCII sparkline 字符串。"""
    if not values:
        return ""
    if len(values) <= width:
        # 不抽样
        sampled = values
    else:
        # 等距抽样到 width 个点
        idxs = [int(i * (len(values) - 1) / (width - 1)) for i in range(width)]
        sampled = [values[i] for i in idxs]
    lo = min(sampled)
    hi = max(sampled)
    if hi == lo:
        return "─" * len(sampled)
    chars = "▁▂▃▄▅▆▇█"
    out = []
    for v in sampled:
        # 映射到 0..7
        idx = int((v - lo) / (hi - lo) * (len(chars) - 1))
        out.append(chars[idx])
    return "".join(out)


def render_trend_chart(samples: list[Sample]) -> str:
    """渲染四项指标的 ASCII 趋势图，返回多行字符串。"""
    if not samples:
        return "(无样本)"
    rss = [float(s.rss_bytes) / (1024 * 1024) for s in samples]  # MB
    handles = [float(s.handle_count) for s in samples if s.handle_count >= 0]
    pool = [float(s.pool_size) for s in samples if s.pool_size >= 0]
    cpu = [s.cpu_percent for s in samples]

    lines = []
    lines.append("内存 RSS (MB):")
    lines.append(f"  min={min(rss):.2f}  max={max(rss):.2f}  final={rss[-1]:.2f}")
    lines.append(f"  {ascii_sparkline(rss)}")
    if handles:
        lines.append("文件句柄数:")
        lines.append(f"  min={min(handles):.0f}  max={max(handles):.0f}  final={handles[-1]:.0f}")
        lines.append(f"  {ascii_sparkline(handles)}")
    if pool:
        lines.append("连接池占用:")
        lines.append(f"  min={min(pool):.0f}  max={max(pool):.0f}  final={pool[-1]:.0f}")
        lines.append(f"  {ascii_sparkline(pool)}")
    lines.append("CPU 使用率 (%):")
    lines.append(f"  min={min(cpu):.2f}  max={max(cpu):.2f}  final={cpu[-1]:.2f}")
    lines.append(f"  {ascii_sparkline(cpu)}")
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════
# 主控
# ════════════════════════════════════════════════════════════════


class SoakTestRunner:
    """长稳测试主控。"""

    def __init__(self, config: SoakConfig) -> None:
        self.cfg = config
        self.stop_event = threading.Event()
        self.samples: list[Sample] = []
        self._mem_worker: MemoryLoadWorker | None = None
        self._dag_runs_holder: list[int] = [0]
        self._proc: Any = None
        self._start_time: float = 0.0

    def request_stop(self, signum: int | None = None, frame: Any = None) -> None:
        """信号处理器：请求优雅停止。"""
        if signum is not None:
            logger.info("收到信号 %d，准备优雅退出...", signum)
        else:
            logger.info("请求停止...")
        self.stop_event.set()

    def run(self) -> dict[str, Any]:
        """执行长稳测试，返回汇总字典。"""
        if not _PSUTIL_OK:
            raise RuntimeError(
                "psutil 未安装，请运行: pip install psutil"
            )

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )

        logger.info("=" * 72)
        logger.info("MAOP 个人版长稳压测启动")
        logger.info("  项目根: %s", PROJECT_ROOT)
        logger.info("  时长: %d s (%.2f h)", self.cfg.duration_s, self.cfg.duration_s / 3600)
        logger.info("  采样间隔: %d s", self.cfg.sample_interval_s)
        logger.info("  记忆 RPS: %d", self.cfg.mem_rps)
        logger.info("  DAG 间隔: %d s", self.cfg.dag_interval_s)
        logger.info("  CSV: %s", self.cfg.csv_path)
        logger.info("=" * 72)

        # 初始化 CSV
        csv_init(self.cfg.csv_path)
        logger.info("CSV 已初始化: %s", self.cfg.csv_path)

        # 启动记忆负载线程
        self._mem_worker = MemoryLoadWorker(
            root_dir=PROJECT_ROOT, rps=self.cfg.mem_rps, stop_event=self.stop_event,
        )
        self._mem_worker.start()
        if self._mem_worker.error is not None:
            raise RuntimeError(
                f"记忆负载线程初始化失败: {self._mem_worker.error}"
            )
        logger.info("记忆负载线程已启动 (rps=%d)", self.cfg.mem_rps)

        # 启动 DAG 负载（asyncio，独立线程跑 event loop）
        dag_thread = threading.Thread(
            target=self._run_dag_loop,
            name="dag-load",
            daemon=True,
        )
        dag_thread.start()
        logger.info("DAG 负载线程已启动 (interval=%d s)", self.cfg.dag_interval_s)

        # 采样循环
        self._proc = get_process()
        self._start_time = time.time()
        # 初始化 cpu_percent 基线（首次调用返回 0.0 且无意义）
        self._proc.cpu_percent(interval=None)

        logger.info("开始采样循环...")
        try:
            while not self.stop_event.is_set():
                elapsed = time.time() - self._start_time
                if elapsed >= self.cfg.duration_s:
                    logger.info("达到目标时长 %d s，结束", self.cfg.duration_s)
                    break

                # 等待采样间隔（可被 stop_event 中断）
                wait_start = time.time()
                while (time.time() - wait_start) < self.cfg.sample_interval_s:
                    if self.stop_event.is_set():
                        break
                    time.sleep(0.2)

                if self.stop_event.is_set():
                    break

                # 采样
                mem_ops = self._mem_worker.ops_count
                dag_runs = self._dag_runs_holder[0]
                s = sample_once(self._proc, self._start_time, mem_ops, dag_runs)
                self.samples.append(s)
                csv_append(self.cfg.csv_path, s)
                logger.info(
                    "采样 #%d  elapsed=%.1fs  RSS=%.1fMB  handles=%d  pool=%d  cpu=%.2f%%  "
                    "mem_ops=%d  dag_runs=%d",
                    len(self.samples), s.elapsed_s,
                    s.rss_bytes / (1024 * 1024),
                    s.handle_count, s.pool_size, s.cpu_percent,
                    s.mem_ops_total, s.dag_runs_total,
                )
        finally:
            # 停止负载
            self.stop_event.set()
            logger.info("等待记忆负载线程退出...")
            if self._mem_worker is not None:
                self._mem_worker.join(timeout=5.0)
            logger.info("等待 DAG 负载线程退出...")
            dag_thread.join(timeout=5.0)

        # 汇总
        logger.info("=" * 72)
        logger.info("采样完成，共 %d 个样本", len(self.samples))
        summary = summarize(self.samples, self.cfg.leak_threshold_mb_per_h)
        summary["config"] = {
            "duration_s": self.cfg.duration_s,
            "sample_interval_s": self.cfg.sample_interval_s,
            "mem_rps": self.cfg.mem_rps,
            "dag_interval_s": self.cfg.dag_interval_s,
            "leak_threshold_mb_per_h": self.cfg.leak_threshold_mb_per_h,
        }
        summary["csv_path"] = str(self.cfg.csv_path)
        summary["trend_chart"] = render_trend_chart(self.samples)

        # 写入 summary json
        self.cfg.summary_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.cfg.summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False, default=str)
        logger.info("汇总已写入: %s", self.cfg.summary_path)

        # 打印汇总到 stdout
        self._print_summary(summary)

        return summary

    def _run_dag_loop(self) -> None:
        """在独立线程中跑 asyncio event loop 执行 DAG 负载。"""
        try:
            asyncio.run(dag_load_loop(
                self.stop_event, self.cfg.dag_interval_s, self._dag_runs_holder,
            ))
        except Exception:
            logger.exception("DAG 负载线程异常退出")

    def _print_summary(self, summary: dict[str, Any]) -> None:
        """打印汇总到 stdout。"""
        print()
        print("=" * 72)
        print("MAOP 长稳压测汇总")
        print("=" * 72)
        print(f"样本数: {summary.get('sample_count', 0)}")
        print(f"实际时长: {summary.get('duration_s', 0):.1f} s")
        print(f"记忆操作总数: {summary.get('mem_ops_total', 0)}")
        print(f"DAG 执行总数: {summary.get('dag_runs_total', 0)}")
        print()
        metrics = summary.get("metrics", {})
        for key in ("memory_rss", "file_handles", "conn_pool", "cpu_percent"):
            m = metrics.get(key)
            if not m:
                continue
            print(f"[{m['name']}] 单位={m['unit']}")
            print(f"  min={m['min']:.4f}  max={m['max']:.4f}  avg={m['avg']:.4f}  final={m['final']:.4f}")
            print(f"  斜率={m['slope_display']}  泄漏疑似={m['leak_suspect']}")
            print()
        verdict = "PASS" if summary.get("overall_pass") else "FAIL"
        print(f"结论: {verdict}")
        print()
        print("趋势图:")
        print(summary.get("trend_chart", ""))
        print()
        print(f"CSV: {summary.get('csv_path', '')}")
        print(f"汇总: {self.cfg.summary_path}")


# ════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════


def parse_args(argv: list[str] | None = None) -> SoakConfig:
    """解析命令行参数。"""
    p = argparse.ArgumentParser(
        description="MAOP 个人版 48h 长稳压测脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--duration", type=int, default=48 * 3600,
        help="测试时长（秒），默认 172800 (48h)",
    )
    p.add_argument(
        "--sample-interval", type=int, default=30,
        help="采样间隔（秒），默认 30",
    )
    p.add_argument(
        "--mem-rps", type=int, default=15,
        help="记忆读写次数/秒（10-20 之间），默认 15",
    )
    p.add_argument(
        "--dag-interval", type=int, default=5,
        help="DAG 执行间隔（秒），默认 5",
    )
    p.add_argument(
        "--leak-threshold", type=float, default=1.0,
        help="内存泄漏告警阈值（MB/h），默认 1.0",
    )
    p.add_argument(
        "--csv-path", type=Path,
        default=PROJECT_ROOT / "deliverables" / "soak-test-data.csv",
        help="CSV 输出路径",
    )
    p.add_argument(
        "--summary-path", type=Path,
        default=PROJECT_ROOT / "deliverables" / "soak-test-summary.json",
        help="汇总 JSON 输出路径",
    )
    args = p.parse_args(argv)

    return SoakConfig(
        duration_s=args.duration,
        sample_interval_s=args.sample_interval,
        mem_rps=args.mem_rps,
        dag_interval_s=args.dag_interval,
        leak_threshold_mb_per_h=args.leak_threshold,
        csv_path=args.csv_path,
        summary_path=args.summary_path,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。"""
    cfg = parse_args(argv)

    if not _PSUTIL_OK:
        print("ERROR: psutil 未安装。请运行: pip install psutil", file=sys.stderr)
        return 2

    runner = SoakTestRunner(cfg)

    # 注册信号处理器（SIGINT / SIGTERM）
    def _handler(signum: int, frame: Any) -> None:
        runner.request_stop(signum, frame)

    try:
        signal.signal(signal.SIGINT, _handler)
    except (ValueError, OSError):
        pass  # 非主线程或不支持
    try:
        signal.signal(signal.SIGTERM, _handler)
    except (ValueError, OSError, AttributeError):
        pass  # Windows 无 SIGTERM

    try:
        summary = runner.run()
    except KeyboardInterrupt:
        runner.request_stop(signal.SIGINT)
        # 给一点时间让线程退出
        time.sleep(1.0)
        return 130
    except Exception:
        logger.exception("长稳测试异常退出")
        return 1

    return 0 if summary.get("overall_pass", False) else 1


if __name__ == "__main__":
    sys.exit(main())