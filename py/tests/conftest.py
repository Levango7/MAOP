"Global test fixtures — overrides pytest's tmp_path to avoid Windows\nPermissionError on ``C:\\Users\\<user>\\AppData\\Local\\Temp\\pytest-of-<user>``.\n\nThe built-in ``tmp_path`` fixture creates directories under a shared\n``pytest-of-<user>`` base.  On Windows this base can accumulate restrictive\nACLs (e.g. after a run under a different session / elevated prompt), causing\nevery subsequent ``tmp_path`` access to raise ``PermissionError [WinError 5]``.\n\nFix: provide our own ``tmp_path`` that uses ``tempfile.mkdtemp()`` instead.\nEach test gets an isolated temp directory that is cleaned up after the test\nsession ends.\n"

from __future__ import annotations

import contextlib
import importlib.util
import logging
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

import pytest

# ── 企业版测试的可选性 ────────────────────────────────────────────
# `maop.enterprise` 由**独立的 maop-enterprise 包（MAOS 私有仓库）**提供，
# 通过同命名空间注入到 maop 包里（ADR-017 双仓库物理隔离）。
#
# - 本地开发机：通常 editable 安装了 MAOS → maop.enterprise 可导入 →
#   企业版测试正常收集运行。
# - CI / 纯净环境：只有 maop，没有 maop-enterprise → 26 个测试文件在
#   **收集阶段**就 ImportError，导致整轮 pytest 全平台失败。
#   （workflow 注释亦记载：2026-08-30 unit tests failed on ALL 9 platforms
#    while identical code + env passes locally。）
#
# 故在此显式检测：企业包不可用时跳过这些文件的收集，而不是让整轮测试崩掉。
# 两种写法都要匹配：`maop.enterprise`（导入路径）与 `maop-enterprise`（包名 /
# 注释里常这么写，如 test_router_misc_coverage.py 的
# "enterprise installed (maop-enterprise wheel): guard removed"）。
_ENTERPRISE_MARKERS = ("maop.enterprise", "maop-enterprise")
# 间接依赖（自身不含上述字样，但经 fixture/被导入模块链式引入 maop.enterprise）。
# 例：test_relay_platform.py → routers.relay_platform → services.integration_service
#     → maop.enterprise.n8n（模块级导入）。
# 注：生产侧已有守卫（_register_relay_platform_router 用 try/except 降级为
# warning），故这**不是**产品缺陷，只是这些测试需要企业包。
_ENTERPRISE_INDIRECT = (
    "test_relay_platform.py",
)
# 注意：find_spec 在模块不可导入时会**抛出** ImportError / ModuleNotFoundError，
# 而不是返回 None。若不捕获，conftest 自身加载失败会让整轮测试全崩（比不守卫
# 更糟）。故必须 try/except。
try:
    _ent_spec = importlib.util.find_spec("maop.enterprise")
except (ImportError, ValueError):  # ModuleNotFoundError 是 ImportError 子类
    _ent_spec = None

if _ent_spec is None:
    _tests_root = Path(__file__).resolve().parent
    _ignored = {
        str(p.relative_to(_tests_root))
        for p in _tests_root.rglob("test_*.py")
        if any(m in p.read_text(encoding="utf-8", errors="ignore")
               for m in _ENTERPRISE_MARKERS)
    }
    _ignored.update(_ENTERPRISE_INDIRECT)
    collect_ignore = sorted(_ignored)

# ── Force test environment BEFORE any maop import ──────────────────
# server.py import 时固化 _auth_enabled / _rl_enabled（get_settings 单例、
# 模块级 os.environ 读取）。若 MAOP_ENV 未设置，_default_auth_enabled()
# 按 secure-by-default 返回 True → AuthMiddleware 启用。
# MAOP_AUTH=0：auth 关闭，中间件授予 read-only 角色（P0-4 安全修复）。
# 需 admin 角色的测试用 monkeypatch setattr require_admin 为 no-op
# （如 test_edition_switch_guard.py、test_dag_sse_endpoint.py 等），
# 或用自定义中间件注入 auth_roles=["admin"]（如 test_task_splitter.py）。
# MAOP_AUTH_DISABLED_ADMIN 已废弃并忽略，不再设置。
os.environ.setdefault("MAOP_ENV", "test")
os.environ.setdefault("MAOP_AUTH", "0")
# 批量路由测试（test_routers_smoke/batch_coverage）连打数十个真实端点，
# app 单例的 RateLimitMiddleware._buckets 跨测试共享计数 → 偶发 429。
# 限流逻辑有专属测试（test_stress 等用 MAOP_RATE_LIMIT=0 单独验证），
# 全局禁用避免批量 smoke 被限流误伤（同 test_secrets.py 的 MAOP_RATE_LIMIT_ENABLED=0）。
os.environ.setdefault("MAOP_RATE_LIMIT", "0")
os.environ.setdefault("MAOP_RATE_LIMIT_ENABLED", "0")

# ── 锁定 auth 固化值（防 e2e 模块级 env 篡改）────────────────────────
# tests/e2e/test_auth_enabled.py / test_edition_switch.py /
# test_routing_rbac_tenant.py 在模块级执行 os.environ["MAOP_AUTH"]="1"
# （收集阶段即生效且不恢复）。若其中任一在 maop.dashboard.server 首次
# import 之前运行 → server._auth_enabled / auth._auth_enabled 固化为 True
# → AuthMiddleware 启用 → 所有无凭据测试请求 401（CI #98 的
# test_agent_token_stream 3 个 SSE 失败即此根因）。
# 这里在收集任何 test 模块之前先 import，锁定 auth=off 固化值；
# 三个 e2e 文件随后会按自身 skip 逻辑（run in isolation）跳过。
import maop.dashboard.routers.auth as _auth_mod  # noqa: F401
import maop.dashboard.server as _server_mod  # noqa: F401


# ── Disable sentence_transformers in test environment ──────────────
# Importing sentence_transformers pulls in torch (~30s) and tries to
# download models from HuggingFace Hub. All MAOP code paths catch
# ImportError and fall back to HashEmbedding, so we inject a stub that
# raises ImportError on attribute access.
class _DisabledSentenceTransformers:
    """Stub module that raises ImportError on any attribute access."""

    def __getattr__(self, name: str):
        raise ImportError(
            f"sentence_transformers is disabled in the test environment "
            f"(attribute {name!r} requested)"
        )

    def __dir__(self):
        return []


if "sentence_transformers" not in sys.modules:
    sys.modules["sentence_transformers"] = _DisabledSentenceTransformers()  # type: ignore[assignment]

# Keep track of all created dirs so we can clean up at session end.
_tmp_dirs: list[str] = []

# 在任何用例跑起来之前抓住标准库原函数，供 _leak_probe 判断"是否还被别的用例 mock 着"。
import subprocess as _subprocess_mod

_ORIGINAL_SUBPROCESS_RUN = _subprocess_mod.run
_PROBE_PREV: dict[str, str] = {}
# 本进程内 `repair_subprocess_run()` 实际修过多少次（会话结束时汇总上报，见
# `_repair_report`）。按进程计数：xdist 下每个 worker 各报各的。
_REPAIR_COUNT = 0


@pytest.fixture
def tmp_path() -> Path:
    """Provide a temporary directory that does **not** rely on pytest's
    ``pytest-of-<user>`` base directory.

    Uses ``tempfile.mkdtemp()`` which creates a fresh directory under the
    system TEMP root with per-process random suffix — no shared base, no
    permission inheritance issues.
    """
    d = tempfile.mkdtemp(prefix="MAOP_test_")
    _tmp_dirs.append(d)
    return Path(d)


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set MAOP_DATA_DIR to tmp_path/data so each test gets an isolated DB.

    Also relaxes plugin checksum strictness (MAOP_PLUGIN_STRICT_CHECKSUM=0) so
    that non-checksum-related tests don't need to embed SHA-256 in every
    manifest. Tests that verify the strict default can override this env var
    via ``monkeypatch.delenv`` or ``monkeypatch.setenv``.

    Forces HuggingFace Hub / sentence-transformers into offline mode so that
    ``SentenceTransformer(model)`` fails fast (LocalEntryNotFoundError) instead
    of hanging on a network download attempt — which would exceed the pytest
    per-test timeout.  Code paths catch this and fall back to HashEmbedding.
    """
    monkeypatch.setenv("MAOP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MAOP_PLUGIN_STRICT_CHECKSUM", "0")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    # settings 单例在 conftest import server 时已固化（当时 MAOP_DATA_DIR 未设
    # → data_dir 指向仓库根 data/），monkeypatch.setenv 不刷新单例 → 所有测试
    # 共享仓库 data/maop.db → xdist 并发 sqlite3.OperationalError: database
    # is locked（test_three_layer_memory 等）。每个测试刷新单例让
    # MAOP_DATA_DIR 真正生效（get_db_path/get_memory_db_path 读 settings）。
    from maop.config.settings import reload_settings
    reload_settings()
    yield
    # 先收口后台进化线程：它们在测试结束后才跑完时会重新解析 get_db_path()，
    # 那时 MAOP_DATA_DIR 已被还原、tmp_path 即将被删 → 会写到共享的
    # data/maop.db 并与其它 xdist worker 抢锁（历史上还会触发把在用的库当损坏
    # 删掉，表现为 worker SIGBUS/node down + 覆盖率门禁假红）。monkeypatch 的还
    # 原发生在本 fixture 结束之后，所以此刻环境仍是本测试隔离的 tmp 目录。
    from maop.core.memory.episodic_store import wait_for_evolution_threads
    wait_for_evolution_threads(timeout_s=15.0)
    # 每个测试后清空 ConnectionPool 单例池：每个测试独立 MAOP_DATA_DIR 产生
    # 独立 db_path → 独立池 → 连接句柄跨测试累积，进程 GC 时才回收 →
    # ResourceWarning: unclosed database 洪泛（xdist 全量下耗尽 worker 句柄）。
    from maop.core.backends.db_utils import close_all_pools
    close_all_pools()
    from maop.core.backends.backends import reset_backends
    reset_backends()
    from maop.core.agent.plugins_hooks.hook_manager import reset_hook_manager
    reset_hook_manager()


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Clean up all temp directories created by our ``tmp_path`` override."""
    # 泄漏修复汇总：必须放在这里且**无条件**执行 —— 修复过的用例不会再失败，
    # 于是 logging 那条 WARNING 在绿跑里永远不会被 pytest 印出来（它默认只打印
    # 失败用例捕获的日志）。不在这里报，泄漏源就真的没人看得见了。
    worker = (getattr(session.config, "workerinput", None) or {}).get("workerid", "master")
    report = _repair_report(str(worker))
    if report:
        print(f"::warning title=leak-probe::{report}")
    # 清空 ConnectionPool 模块级单例池：池连接 release() 回池不关闭，每个测试
    # 独立 MAOP_DATA_DIR 会产生大量池与连接句柄，进程退出时 GC 才回收 →
    # ResourceWarning: unclosed database 洪泛（xdist 全量下耗尽 worker 句柄）。
    from maop.core.backends.db_utils import close_all_pools
    close_all_pools()
    from maop.core.backends.backends import reset_backends
    reset_backends()
    from maop.core.agent.plugins_hooks.hook_manager import reset_hook_manager
    reset_hook_manager()
    for d in _tmp_dirs:
        with contextlib.suppress(Exception):
            shutil.rmtree(d, ignore_errors=True)
    _tmp_dirs.clear()


# ── v5.2.0 Evolution loop fixtures (spec §15 / ADR-019 / ADR-020) ────
# AC-08 conftest fixture 模板：避免 evolution 模块级单例路径固化导致
# 跨测试污染（ADR-019 同类风险）。
#
# 用法：
#     def test_my_evo_thing(evolution_loop_factory):
#         loop = evolution_loop_factory()  # 每次新实例 + 显式 db_path
#         loop.run_cycle(dry_run=True)
#
# 禁止在测试 module-top 写：
#     from maop.core.evolution.evolution_loop import EvolutionLoop
# （会触发模块级 _init_db 路径固化 — 跨测试污染）

from typing import Any

# 拟扩展的 evolution 单例属性名（spec §15 + ADR-019 同类风险登记）。
# 任何 evol 子模块在 module 顶层定义 `_*_instance` / `_*_singleton` / `_*_db_path` 等
# 缓存性单例时，必须加入此列表。
_EVO_SINGLETON_ATTRS: tuple[str, ...] = (
    "_db_path",
    "_instance",
    "_singleton",
    "_global_loop",
    "_global_history",
    "_global_bus",
    "_global_pool",
    "_global_lb",
    "_metrics",
    "_otel_tracer",
)

_EVOLUTION_MODULES: tuple[str, ...] = (
    "maop.core.evolution.evolution_loop",
    "maop.core.evolution.evolution_loop_types",
    "maop.core.evolution.evolution_collectors",
    "maop.core.evolution.evolution_analyzers",
    "maop.core.evolution.evolution_agent",
    "maop.core.evolution.evolution_phases",
)


def _try_reset_evo_singletons() -> None:
    """对 evolution 子模块的潜在单例属性做 best-effort 重置（不存在则跳过）。

    通过直接 setattr 尝试清空缓存性引用。某些属性可能是 property 或不允许
    set，捕获 AttributeError / TypeError 即可——清理失败不致命，下个测试的
    ``EvolutionLoop(root_dir=...)`` 仍能正确使用传入的 root_dir。
    """
    import importlib

    for mod_name in _EVOLUTION_MODULES:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        for attr in _EVO_SINGLETON_ATTRS:
            if hasattr(mod, attr):
                try:
                    setattr(mod, attr, None)
                except (AttributeError, TypeError):
                    pass


# 测试框架自己的后台线程，不是被测代码的泄漏 —— 报出来只会淹没真嫌疑人。
# 按 target 函数的**定义模块**判定，不按线程名：自动生成的名字是
# `Thread-N (run_server)` 这种格式，随 Python 版本与函数名变化，模块名才是稳定指纹。
# 实测（2026-09-27）`pytest_rerunfailures.py:746 run_server` 会阻塞在 `socket.accept()`
# 整个 session，导致每条用例 setup 都被记成"残留线程"。
_HARNESS_THREAD_MODULES = frozenset({"pytest_rerunfailures", "xdist", "_pytest", "execnet"})


def _thread_module(thread: threading.Thread) -> str:
    """target 函数的定义模块；拿不到就是 '?'（宁可多报也不漏报）。"""
    return getattr(getattr(thread, "_target", None), "__module__", None) or "?"


def _thread_origin(thread: threading.Thread) -> str:
    target = getattr(thread, "_target", None)
    qual = getattr(target, "__qualname__", None) or "?"
    return f"{_thread_module(thread)}:{qual}"


def _suspicious_threads() -> list[str]:
    """当前存活的非主线程，剔除测试框架自己的线程。"""
    out = []
    for t in threading.enumerate():
        if t is threading.main_thread():
            continue
        if _thread_module(t).split(".", 1)[0] in _HARNESS_THREAD_MODULES:
            continue
        out.append(f"{t.name}<-{_thread_origin(t)}")
    return out


def _run_fingerprint() -> str:
    """当前 `subprocess.run` 的身份，格式 `定义模块:限定名`。

    只读 `type(fn)` 与函数自身的 `__module__ / __qualname__`：**绝不读 `return_value`**
    （MagicMock 的自动属性会凭空造出子 mock —— 探针反过来改变被观测对象），也绝不调用它。
    非函数（Mock / partial / callable 类）退化到 `类型名@类型的模块`，因为对 Mock 实例取
    dunder 属性同样可能触发自动创建。
    """
    fn = _subprocess_mod.run
    kind = type(fn).__name__
    if kind != "function":
        return f"{kind}@{getattr(type(fn), '__module__', '?')}"
    return (
        f"{getattr(fn, '__module__', '?')}:"
        f"{getattr(fn, '__qualname__', getattr(fn, '__name__', '?'))}"
    )


def _leak_probe_line(nodeid: str) -> str | None:
    """决定这条用例要不要记 WARNING；返回 None 表示一切干净。

    单独成函数是为了能被用例直接调用断言 —— pytest 不允许直接调夹具，而"什么时候才报"
    恰恰是探针唯一可能被改坏的地方（把条件写成 `if False` 也能全员绿）。
    """
    patched = _subprocess_mod.run is not _ORIGINAL_SUBPROCESS_RUN
    impl = _run_fingerprint() if patched else "-"
    threads = _suspicious_threads()
    prev = _PROBE_PREV.get("nodeid")
    _PROBE_PREV["nodeid"] = nodeid
    if not patched and not threads:
        return None
    return (
        f"[leak-probe] test={nodeid} prev={prev} "
        f"subprocess_run_patched={patched} run_impl={impl} threads={threads[:8]}"
    )


# ── 归因：谁在什么用例、哪一行替换了 subprocess.run ────────────────────
#
# 探针原本只能报"上一条用例 nodeid"（prev=），但 xdist `--dist load` 会把**任意**
# 后续用例发到同一 worker，prev 与真正的肇事者可能隔着几十条 —— 定位效率极低。
# 这里给 `unittest.mock._patch.start/stop` 挂一对钩子，记录每个**仍在生效**的
# patcher：目标、启动时的用例 nodeid、启动栈顶几帧。teardown 发现全局被换掉时，
# 直接把"谁在何行"打进日志，从"知道现象"升级到"知道责任人"。
_PATCH_SITES: dict[int, dict[str, object]] = {}
_PATCH_INSTANCES: dict[int, object] = {}


def _patch_target_is_subprocess_run(patcher: object) -> bool:
    """判断 patcher 的目标是否落在 ``subprocess.run``（含 `<mod>.subprocess.run` 形式）。"""
    getter = getattr(patcher, "get_original", None)
    if not callable(getter):
        return False
    try:
        getter()
    except Exception:
        return False
    return getattr(patcher, "attribute", None) == "run" and "subprocess" in str(
        getattr(patcher, "target", "")
    )


def _short_stack(skip: int = 2) -> str:
    """抓当前调用栈里"调用方"的最后几帧（定位 patch 起始位置用）。

    必须跳过 mock 包内部帧（``unittest/mock/…``）——patch 的 start → __enter__
    链路全在 mock 里，直接取栈顶只会得到 ``_manager.py:120`` 这种无信息量的
    行号。过滤后取**最靠近用户代码**的几帧。
    """
    import traceback

    frames = traceback.extract_stack()[:-skip]
    # 过滤两类噪声：conftest 自身；unittest/mock 包内部（start → __enter__ 链路
    # 全在 mock 里，会把栈顶占满 _manager.py / _callers.py 这种无信息量的帧）。
    user_frames = [
        f
        for f in frames
        if "conftest.py" not in f.filename
        and "/unittest/" not in f.filename.replace("\\", "/")
        and Path(f.filename).name not in ("_callers.py", "_manager.py", "python.py")
    ]
    tail = user_frames[-3:]
    return " <- ".join(
        f"{Path(f.filename).name}:{f.lineno}" for f in tail
    ) or "<unknown>"


def _install_patch_attribution() -> None:
    """给 mock._patch.start/stop 挂钩子（幂等）。"""
    from unittest import mock as _mock

    if getattr(_mock._patch, "_leakhunt_wrapped", False):
        return

    orig_start = _mock._patch.start
    orig_stop = _mock._patch.stop

    def start(self, *a, **kw):  # type: ignore[no-untyped-def]
        result = orig_start(self, *a, **kw)
        try:
            if _patch_target_is_subprocess_run(self):
                _PATCH_SITES[id(self)] = {
                    "nodeid": _PROBE_PREV.get("nodeid", "<unknown>"),
                    "site": _short_stack(),
                    "target": str(getattr(self, "target", "?")),
                }
                _PATCH_INSTANCES[id(self)] = self
        except Exception:  # 观测不许影响被观测对象
            pass
        return result

    def stop(self, *a, **kw):  # type: ignore[no-untyped-def]
        result = orig_stop(self, *a, **kw)
        _PATCH_SITES.pop(id(self), None)
        _PATCH_INSTANCES.pop(id(self), None)
        return result

    _mock._patch.start = start  # type: ignore[method-assign]
    _mock._patch.stop = stop  # type: ignore[method-assign]
    _mock._patch._leakhunt_wrapped = True  # type: ignore[attr-defined]


def _leak_attribution() -> str:
    """描述当前仍在生效的 subprocess.run patcher；无则返回空串。"""
    if not _PATCH_SITES:
        return ""
    parts = [
        f"{meta['nodeid']} @ {meta['site']} (target={meta['target']})"
        for meta in _PATCH_SITES.values()
    ]
    return "; ".join(parts)


def repair_subprocess_run() -> bool:
    """把全局 ``subprocess.run`` 恢复为标准库原版；返回是否发生了修复。

    T2.3-2（2026-10-04）：泄漏的 ``subprocess.run`` 会**跨用例存活**，在 xdist
    ``--dist load`` 下被随机传播到后续任意用例（曾打红
    ``TestCallSyncFallback`` 三元凶）。这里在每条用例 teardown 强制复核并修复，
    让污染跨不过用例边界——治本，而不只是受害文件自保。
    修复前先记 WARNING（含 patcher 归因），不静默。
    """
    global _REPAIR_COUNT
    if _subprocess_mod.run is _ORIGINAL_SUBPROCESS_RUN:
        return False
    who = _leak_attribution() or f"impl={_run_fingerprint()}"
    _REPAIR_COUNT += 1
    logging.getLogger("tests.conftest.leak_probe").warning(
        "[leak-probe] REPAIR subprocess.run <- standard library; culprit: %s",
        who,
    )
    _subprocess_mod.run = _ORIGINAL_SUBPROCESS_RUN
    return True


def _repair_report(worker: str = "master") -> str | None:
    """会话结束时要不要报"本进程修过几次泄漏"；一次都没修则返回 None。

    单独成函数是为了能被用例直接断言（pytest 不允许直接调夹具，而这里要钉的
    恰恰是"全绿时也会报"这个语义）。

    为什么需要它：`repair_subprocess_run()` 的 WARNING 走 logging，而 pytest 默认
    **只在用例失败时**才把捕获的日志打进报告 —— 修复生效后用例当然不失败了，
    于是"泄漏源仍在活跃"这件事在绿跑里完全不可见。泄漏源至今没归位的前提下，
    这会让我们永久失去线索。这里在 session 结束时无条件汇总一次，并以
    ``::warning::`` 输出（runner 会把它渲染成 PR 注解）。
    """
    if _REPAIR_COUNT == 0:
        return None
    return (
        f"[leak-probe] 本进程共修复被替换的 subprocess.run {_REPAIR_COUNT} 次"
        f"（worker={worker}）：仍有测试在跨用例泄漏全局 subprocess.run。"
        "症状已被会话级守卫挡住，但泄漏源未定位 —— 请按用例日志里的 "
        "culprit:/run_impl: 归因继续追。"
    )


@pytest.fixture(autouse=True)
def _leak_probe(request: pytest.FixtureRequest):
    """只观测、不改行为：记下"本用例开始时，进程已经被谁弄脏了"。

    动机：`tests/test_tool_manager.py::TestCallSyncFallback` 3 条在 macOS/Windows 随机红
    （症状 `assert '42' in 'ok'`、两条 `assert True is False`）。读代码排除了三条猜测
    （没有 module/class 作用域的 patch、`ToolManager` 没有类级共享注册表、per-test
    `MAOP_DATA_DIR` 隔离有效），本地推不出机制 —— 于是让 CI 自己交代：

    - `prev=` 同一 worker 里上一条跑过的用例 nodeid（若 patched=True，它就是嫌疑犯）
    - `subprocess_run_patched=` 全局 `subprocess.run` 是否已不是标准库原版
    - `run_impl=` 当前 `subprocess.run` 的定义处 `模块:限定名`（仅在被换掉时才有值）——
      把"被 patch 了"升级成"被谁 patch"。fake 多为测试文件里的局部闭包，其
      `__qualname__` 会带出所在测试方法，故这一项通常直接点名嫌疑人所在文件
    - `threads=` 残留线程，格式 `名字<-定义模块:限定名`（泄漏后台线程是这一族的已知模式）

    只在"可疑"时打 WARNING，避免 8400+ 条噪音；用例失败时 pytest 会把 setup 阶段捕获到的
    日志印进失败详情，红的那一次自带线索。框架自己的线程（见 `_HARNESS_THREAD_MODULES`）
    不算泄漏 —— 实测 `pytest_rerunfailures` 的 `ServerStatusDB.run_server` 整个 session 都
    阻塞在 `socket.accept()`，不剔除的话每条用例 setup 都会 WARNING，真嫌疑人反而被淹没。

    ⚠️ 本探针**刻意不碰数据库 / settings**。早先版本在这里调 `get_db_path()` 想做"跨用例
    工具行串味"取证，但它跑在 `_isolate_data_dir` 之前，提前解析并初始化了 `data_dir` /
    settings 单例，直接把 `tests/test_secrets.py` 的 4 条用例弄红（那些用例依赖"密钥文件按
    当前 data_dir 查找"）。观测型工具不许有副作用 —— 该夹具因此也定义在 `_isolate_data_dir`
    之后（autouse 夹具按定义顺序执行）。

    **teardown 侧的修复**（T2.3-2）：setup 只观测；yield 之后调用
    ``repair_subprocess_run()``——全局被换掉就恢复原版并记 WARNING（含 mock
    patcher 归因）。这是结构性不变量：泄漏的 ``subprocess.run`` 跨不过用例
    边界，xdist 的随机分发也就无从传播。
    """
    _install_patch_attribution()
    line = _leak_probe_line(request.node.nodeid)
    if line:
        logging.getLogger("tests.conftest.leak_probe").warning(line)
    yield
    repair_subprocess_run()


@pytest.fixture
def evolution_loop_factory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """可重置的 EvolutionLoop 工厂：每次返回新实例 + 显式 db_path。

    与 ``_isolate_data_dir`` autouse 协同：
    - autouse 设 ``MAOP_DATA_DIR=tmp_path/data`` + ``reload_settings()`` →
      ``get_db_path()`` 读到的路径正确。
    - 本 fixture 进一步 best-effort 重置 evolution 子模块的单例属性。
    - 显式传 ``root_dir=tmp_path/evolution`` 给 EvolutionLoop（不依赖单例缓存）。

    Returns:
        callable: () -> EvolutionLoop（每次新实例）
    """
    db_root = tmp_path / "evolution"
    db_root.mkdir(parents=True, exist_ok=True)
    # 工厂默认开启开关，让「开关=开」路径易于测试；测试自身可 monkeypatch.setenv
    # 在调用 _factory() 之前覆盖为 "0" 验证「开关=关」零行为变化（AC-01）。
    monkeypatch.setenv("MAOP_EVOLUTION_LOOP_ENABLED", "1")

    def _factory(**kwargs: Any):
        _try_reset_evo_singletons()
        from maop.core.evolution.evolution_loop import EvolutionLoop
        return EvolutionLoop(root_dir=db_root, **kwargs)

    return _factory


@pytest.fixture(autouse=True)
def _reset_evolution_singletons():
    """任何测试跑完都重置 evolution 模块的潜在单例属性（autouse）。

    与 ``_isolate_data_dir`` 协同：后者在 yield 前 reload_settings，
    本 fixture 在 yield 后清理 evolution 自身的单例。两个机制互不冲突。
    """
    yield
    _try_reset_evo_singletons()


@pytest.fixture()
def crl_signer(tmp_path, monkeypatch):
    """CRL 签名密钥（临时）——2026-09-25 起「无签名 CRL 一律拒绝」。

    生成临时 Ed25519 密钥对，把 enterprise 的信任锚（``_PUBLIC_KEY_PATH``）
    与期望指纹（``MAOP_LICENSE_KEY_FP``）指向它，并返回
    ``sign(payload: dict) -> dict``：在 payload 上追加 base64url 签名。

    凡是要验证「撤销条目真正生效」的用例都必须用它签名 CRL，否则 CRL 会
    被 ``_reject_if_unusable`` 作为不可用数据丢弃。
    """
    import base64
    import hashlib
    import json

    import maop.enterprise.license as license_mod
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.generate()
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    pub_path = tmp_path / "crl_keys" / "public_key.pem"
    pub_path.parent.mkdir(parents=True, exist_ok=True)
    pub_path.write_bytes(pub_pem)
    monkeypatch.setattr(license_mod, "_PUBLIC_KEY_PATH", pub_path)
    monkeypatch.setenv("MAOP_LICENSE_KEY_FP", hashlib.sha256(pub_pem).hexdigest())

    def sign(payload: dict) -> dict:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        sig = base64.urlsafe_b64encode(priv.sign(canonical)).rstrip(b"=").decode()
        return {**payload, "signature": sig}

    return sign
