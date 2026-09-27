"""`tests/conftest.py::_leak_probe` 的自检。

为什么要有这些用例：#39 的探针上线后我从没验证过它**能**报东西，也没验证过它**只**报
该报的东西。实测两件事同时暴露：
1. 探针确实有效 —— 但它的输出里永远混着一条 `Thread-1 (run_server)`，那是
   `pytest_rerunfailures` 自己的 socket 服务线程（阻塞在 `socket.accept()`，整个 session
   都在），不是被测代码的泄漏。每条用例 setup 都会 WARNING，真嫌疑人会被它淹没。
2. 于是加了按 target 定义模块过滤的 `_suspicious_threads()`。过滤本身必须被证明有效，
   否则它就是一个"永远绿灯"的门禁 —— 下面的 `monkeypatch` 变异用例就是干这个的。

探针夹具会遍历活动线程，所以这里造的线程全部有超时且在 finally 里释放：自检工具自己
不许泄漏。
"""

from __future__ import annotations

import inspect
import threading

import pytest

from tests.conftest import (
    _HARNESS_THREAD_MODULES,
    _PROBE_PREV,
    _leak_probe_line,
    _suspicious_threads,
    _thread_module,
    _thread_origin,
)


def _park(event: threading.Event) -> None:
    """线程主体：等 event 或超时，绝不长期驻留。"""
    event.wait(timeout=5.0)


@pytest.fixture
def live_thread():
    """造一条"看起来属于被测代码"的临时线程，名字可预测，用完立刻放掉。"""
    stop = threading.Event()
    name = "selfcheck-plain-leak"
    t = threading.Thread(target=_park, args=(stop,), name=name, daemon=True)
    t.start()
    try:
        yield t
    finally:
        stop.set()
        t.join(timeout=10.0)
        assert not t.is_alive(), f"{name} 没退出，自检工具自己泄漏了"


def test_plain_thread_is_named_with_its_origin(live_thread: threading.Thread) -> None:
    """泄漏线程必须被报出来，且带上"谁造的"而不只是一个名字。"""
    hits = [entry for entry in _suspicious_threads() if live_thread.name in entry]
    assert hits, f"探针没抓到 {live_thread.name}：{_suspicious_threads()}"
    assert "test_conftest_leak_probe" in hits[0], hits[0]
    assert "_park" in hits[0], hits[0]


def test_harness_thread_is_filtered_and_that_is_the_only_reason(
    live_thread: threading.Thread,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """变异验证：把线程伪装成框架线程 → 不报；把过滤器关掉 → 立刻又报。

    两半缺一不可：只测"不报"的话，任何 bug（比如 enumerate 拿不到线程）都能让它通过。
    """
    fake = threading.Event()
    monkeypatch.setattr(_park, "__module__", "pytest_rerunfailures.plugin", raising=False)
    impostor = threading.Thread(target=_park, args=(fake,), name="selfcheck-impostor", daemon=True)
    impostor.start()
    try:
        names = [e.split("<-", 1)[0] for e in _suspicious_threads()]
        assert "selfcheck-impostor" not in names, names
        monkeypatch.setattr("tests.conftest._HARNESS_THREAD_MODULES", frozenset())
        names_after = [e.split("<-", 1)[0] for e in _suspicious_threads()]
        assert "selfcheck-impostor" in names_after, (
            "关掉过滤器后仍不报 —— 说明'不报'是别的原因，过滤器没在起作用"
        )
    finally:
        fake.set()
        impostor.join(timeout=10.0)


def test_allowlist_entries_are_real_module_names() -> None:
    """拼错的模块名等于没有过滤器，所以逐个核对可导入性。"""
    import importlib

    for mod in _HARNESS_THREAD_MODULES:
        if mod.startswith("_"):  # `_pytest` 是私有包但确实存在
            importlib.import_module(mod)
            continue
        try:
            importlib.import_module(mod)
        except ImportError as exc:  # pragma: no cover - 门禁本身
            pytest.fail(f"过滤器里的 {mod!r} 不是可导入的模块：{exc}")


def test_probe_fixture_is_autouse_and_defined_after_data_isolation() -> None:
    """夹具的**定义顺序**是踩过的坑：探针若在 `_isolate_data_dir` 之前跑，就会提前初始化
    settings 单例并把 `test_secrets.py` 弄红。顺序断言让那个坑不可能无声回归。
    """
    import tests.conftest as conftest_mod

    src = inspect.getsource(conftest_mod)
    probe_at = src.index("def _leak_probe(")
    isolation_at = src.index("def _isolate_data_dir(")
    assert probe_at > isolation_at, "探针夹具必须定义在数据隔离夹具之后（autouse 按定义顺序执行）"
    sig = inspect.signature(conftest_mod._leak_probe)
    assert list(sig.parameters) == ["request"], sig
    marks = getattr(conftest_mod._leak_probe, "_pytestfixturefunction", None)
    assert marks is not None and marks.autouse, "探针夹具必须是 autouse，否则什么都观测不到"


def test_thread_origin_falls_back_when_target_is_unknown() -> None:
    """子类化 Thread（没有 `_target`）也要有可读指纹，宁可多报也不漏报。"""
    gate = threading.Event()

    class Anonymous(threading.Thread):
        def run(self) -> None:
            gate.wait(timeout=5.0)

    t = Anonymous(name="selfcheck-subclass", daemon=True)
    assert _thread_module(t) == "?", _thread_module(t)
    assert _thread_origin(t) == "?:?", _thread_origin(t)
    t.start()
    try:
        assert any("selfcheck-subclass" in e for e in _suspicious_threads()), _suspicious_threads()
    finally:
        gate.set()
        t.join(timeout=10.0)


def test_the_real_rerunfailures_session_thread_is_not_reported() -> None:
    """回归位：`pytest_rerunfailures` 的 socket 服务线程整个 session 都在（`--reruns` 一开就有），
    而且它的限定名是 `ServerStatusDB.run_server` —— 带点号。过滤器早先的版本拿
    `<module>:<qual>` 整串去 split('.')，于是这条线程永远过滤不掉，每条用例 setup 都 WARNING，
    真嫌疑人被淹没。这里直接拿真实线程验证，而不是只验证伪造样本。
    """
    pytest.importorskip("pytest_rerunfailures")
    harness = [
        t for t in threading.enumerate()
        if _thread_module(t).split(".", 1)[0] == "pytest_rerunfailures"
    ]
    if not harness:
        pytest.skip("本进程未启用 --reruns，没有 rerunfailures 服务线程可验")
    reported = [e.split("<-", 1)[0] for e in _suspicious_threads()]
    for t in harness:
        assert t.name not in reported, f"框架线程漏进探针输出：{t.name} in {reported}"


def test_probe_line_names_the_leak_and_the_prev_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """证明"红的那一次自带嫌疑人"这条链路真的成立。

    只测 `_suspicious_threads()` 是不够的："什么时候才报"才是探针唯一可能被改坏的点 ——
    把条件写成 `if False` 也能全员绿。夹具本身不允许直接调用（pytest 会报错），所以判定
    逻辑单独成函数 `_leak_probe_line()`，夹具只负责把它交给 logger。
    """
    monkeypatch.setitem(_PROBE_PREV, "nodeid", "tests/test_predecessor.py::suspect")
    stop = threading.Event()
    t = threading.Thread(target=_park, args=(stop,), name="selfcheck-probe-leak", daemon=True)
    t.start()
    try:
        line = _leak_probe_line("tests/test_current.py::case")
        assert line is not None, "有泄漏线程却没生成探针日志"
        assert "selfcheck-probe-leak" in line, line
        assert "prev=tests/test_predecessor.py::suspect" in line, line
        assert "test=tests/test_current.py::case" in line, line
        assert "subprocess_run_patched=False" in line, line
        assert _PROBE_PREV["nodeid"] == "tests/test_current.py::case", "prev 记账没更新"
    finally:
        stop.set()
        t.join(timeout=10.0)


def test_probe_line_is_none_when_nothing_is_suspect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不误报：没有泄漏、没有 patch 时必须返回 None（否则噪音会淹掉信号）。

    这里把 `_suspicious_threads` 打成空表，而不是断言"进程里真的没有别的线程"：全量跑时同一
    worker 里可能残留别人的线程，那种断言会变成新的 flaky 源 —— 而"别人泄漏"本就该报。
    """
    import tests.conftest as c

    monkeypatch.delitem(_PROBE_PREV, "nodeid", raising=False)
    monkeypatch.setattr(c, "_suspicious_threads", list)
    assert _leak_probe_line("tests/test_clean.py::case") is None
    assert _PROBE_PREV["nodeid"] == "tests/test_clean.py::case", "即便不报也要记账 prev"


def test_probe_line_fires_on_a_subprocess_run_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """另一条触发路径：别的用例把全局 `subprocess.run` 换掉没还原。"""
    import subprocess

    import tests.conftest as c

    monkeypatch.delitem(_PROBE_PREV, "nodeid", raising=False)
    monkeypatch.setattr(c, "_suspicious_threads", list)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)
    line = _leak_probe_line("tests/test_after_patch.py::case")
    assert line is not None and "subprocess_run_patched=True" in line, line
