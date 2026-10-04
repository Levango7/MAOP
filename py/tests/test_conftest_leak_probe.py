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
    _run_fingerprint,
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


def test_probe_fixture_is_autouse_and_defined_after_data_isolation(
    request: pytest.FixtureRequest,
) -> None:
    """夹具的**定义顺序**与 autouse 接线都要钉住。

    顺序：探针若在 `_isolate_data_dir` 之前跑，就会提前初始化 settings 单例并把
    `test_secrets.py` 弄红 —— 那是 #39 踩过的坑，不该靠记忆防守。
    """
    import tests.conftest as conftest_mod

    src = inspect.getsource(conftest_mod)
    probe_at = src.index("@pytest.fixture(autouse=True)\ndef _leak_probe(request")
    isolation_at = src.index("def _isolate_data_dir(")
    assert probe_at > isolation_at, "探针夹具必须定义在数据隔离夹具之后（autouse 按定义顺序执行）"
    # autouse 用**行为**证明，不碰 pytest 内部属性：CI 是 pytest 9.1.1、本地 8.3.4
    # （`pytest>=8.0` 没 pin 死），而 `@pytest.fixture` 的载体在两代里完全不同 ——
    # 8.x 是"原函数 + `_pytestfixturefunction` 标记"，9.x 返回 `FixtureFunctionDefinition`。
    # 我第一版就是断言那个标记名，在 CI 上直接红（本地全绿，因为版本不同）。
    # 夹具若真为 autouse，它必然在本次用例 setup 时把 `_PROBE_PREV["nodeid"]` 写成我的 nodeid。
    assert _PROBE_PREV.get("nodeid") == request.node.nodeid, (
        f"探针夹具没有为每条用例自动运行：_PROBE_PREV={_PROBE_PREV!r} 期望含 {request.node.nodeid!r}"
    )


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


def test_run_fingerprint_names_defining_module_and_qualname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`run_impl` 必须能点名到 fake 的定义处，否则它只是把"被 patch 了"重说一遍。

    真实嫌疑人（`tests/test_agent_adapters.py` 的 `_mock_subprocess_run`）返回的就是这种
    局部闭包，`__qualname__` 里带着造它的函数名 —— 所以断言形状按那个形状来。
    """
    import subprocess

    def _the_suspect_run(cmd, **kwargs):
        return None

    monkeypatch.setattr(subprocess, "run", _the_suspect_run)
    fp = _run_fingerprint()
    assert fp.startswith("tests.test_conftest_leak_probe:"), fp
    assert "_the_suspect_run" in fp, fp


def test_run_fingerprint_does_not_touch_the_mock_it_reports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """反观察者效应：取证不许在被观测对象上留下调用或新建属性。

    特意不读 `return_value` —— MagicMock 的属性访问会**自动创建子 mock**，那等于探针
    反过来改变了它要观测的那个 fake。这条用例就是钉住这一点的：以后有人为了多打一个字段
    去碰 mock，会在这里红。
    """
    import subprocess
    from unittest.mock import MagicMock

    fake = MagicMock(name="run")
    monkeypatch.setattr(subprocess, "run", fake)

    before = dict(fake.__dict__)
    fp = _run_fingerprint()
    after = fake.__dict__

    assert fp.startswith("MagicMock@"), fp
    assert "unittest.mock" in fp, fp
    assert fake.call_count == 0, "探针调用了被观测的 fake"
    assert fake.mock_calls == [], f"探针在被观测的 fake 上留下了调用记录：{fake.mock_calls}"
    created = set(after) - set(before)
    assert not created, f"探针在被观测对象上新建了属性（观察者效应）：{sorted(created)}"
    # 只比键集合不够：读 `return_value` 不新增键，而是把已有的 `_mock_return_value`
    # 换成一个新建的子 mock。所以还要比**值身份**，并确认没长出任何子 mock。
    changed = sorted(k for k, v in before.items() if k not in after or after[k] is not v)
    assert not changed, f"探针改动了被观测对象的内部状态：{changed}"
    children = getattr(fake, "_mock_children", None)
    assert not children, f"探针让 MagicMock 自动创建了子 mock（观察者效应）：{sorted(children)}"


def test_probe_line_carries_run_impl_only_when_subprocess_is_patched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """字段存在性：线程泄漏但 subprocess 干净时是 `run_impl=-`；被换掉时是具体定义处。"""
    import subprocess

    import tests.conftest as c

    monkeypatch.delitem(_PROBE_PREV, "nodeid", raising=False)
    monkeypatch.setattr(c, "_suspicious_threads", lambda: ["ghost<-some.module:target"])
    line = _leak_probe_line("tests/x.py::thread_only")
    assert line is not None and "run_impl=-" in line, line

    monkeypatch.setattr(c, "_suspicious_threads", list)
    monkeypatch.delitem(_PROBE_PREV, "nodeid", raising=False)
    assert _leak_probe_line("tests/x.py::clean") is None, "干净时整行都不该打"

    def _culprit_run(cmd, **kwargs):
        return None

    monkeypatch.setattr(subprocess, "run", _culprit_run)
    dirty = _leak_probe_line("tests/x.py::patched")
    assert dirty is not None and "subprocess_run_patched=True" in dirty, dirty
    assert "run_impl=tests.test_conftest_leak_probe:" in dirty, dirty
    assert "_culprit_run" in dirty, dirty
    assert "run_impl=-" not in dirty, dirty


# ── T2.3-2: 会话级修复 + patcher 归因（治本层）────────────────────────


def test_repair_restores_dirtyed_global(monkeypatch: pytest.MonkeyPatch) -> None:
    """全局被换掉时 repair_subprocess_run() 报告修复并还原；干净时返回 False。"""
    import subprocess

    import tests.conftest as c

    # 本用例会真的把计数器 +1，而计数器在 session 结束时会被上报（_repair_report）。
    # 不还原的话，一次正常的全绿跑会打出 ::warning:: —— 自检工具自己制造假警报。
    monkeypatch.setattr(c, "_REPAIR_COUNT", c._REPAIR_COUNT)
    original = subprocess.run
    assert c.repair_subprocess_run() is False, "干净时不应报告修复"

    def fake(*a, **k):
        return None

    monkeypatch.setattr(subprocess, "run", fake)
    assert c.repair_subprocess_run() is True, "被弄脏时应报告修复"
    assert subprocess.run is original, "修复后应还原为标准库原版"


def test_repair_logs_culprit_with_nodeid(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """归因报告点名"哪条用例"——泄漏源是未 stop 的 patcher 时可精确定位到用例 nodeid。"""
    import logging
    import subprocess
    from unittest.mock import MagicMock, patch

    import tests.conftest as c

    monkeypatch.setattr(c, "_REPAIR_COUNT", c._REPAIR_COUNT)
    original = subprocess.run
    monkeypatch.setitem(c._PROBE_PREV, "nodeid", "tests/offender.py::test_leaks")
    patcher = patch("subprocess.run", MagicMock())
    patcher.start()
    try:
        with caplog.at_level(logging.WARNING, logger="tests.conftest.leak_probe"):
            repaired = c.repair_subprocess_run()
        assert repaired is True
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "REPAIR" in text, text
        assert "tests/offender.py::test_leaks" in text, (
            f"归因未点名肇事用例：{text}"
        )
    finally:
        try:
            patcher.stop()
        except RuntimeError:
            pass  # repair 已还原，stop 会报"未 start"——预期
        subprocess.run = original


def test_patch_attribution_installed_once() -> None:
    """归因钩子幂等（重复安装会把 start/stop 套娃）。"""
    from unittest import mock as _mock

    import tests.conftest as c

    c._install_patch_attribution()
    c._install_patch_attribution()
    assert getattr(_mock._patch, "_leakhunt_wrapped", False) is True


def test_short_stack_filters_mock_frames() -> None:
    """栈裁剪必须滤掉 mock 包内部帧——否则归因只会报 _manager.py:120 这种废信息。"""
    import tests.conftest as c

    stack = c._short_stack()
    for noise in ("_manager.py", "_callers.py", "conftest.py"):
        assert noise not in stack, f"归因栈里混入噪声帧 {noise}: {stack}"


# ── T2.3-3: 修复次数必须在**绿跑里也可见**（2026-10-04）───────────────
#
# 为什么要有这一层：`repair_subprocess_run()` 的 WARNING 走 logging，而 pytest
# 默认只把**失败用例**捕获的日志印进报告。修复生效后用例不失败了 —— 于是"泄漏
# 源仍在活跃"这件事在绿跑里彻底隐身。在泄漏源至今未归位的前提下，那等于永久
# 丢掉线索。所以会话结束时无条件汇总一次（xdist 下每 worker 各报各的）。


def test_repair_report_is_silent_on_a_clean_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """一次都没修 → 不吭声。没有这条，它会退化成每条 CI 跑都刷屏的噪音。"""
    import tests.conftest as c

    monkeypatch.setattr(c, "_REPAIR_COUNT", 0)
    assert c._repair_report() is None


def test_repair_report_names_count_and_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """报告要点名"修了几次"和"哪个 worker"，否则无法据此定位。"""
    import tests.conftest as c

    monkeypatch.setattr(c, "_REPAIR_COUNT", 3)
    text = c._repair_report("gw7")
    assert text is not None
    assert "3" in text and "gw7" in text, text
    assert "泄漏源未定位" in text, text


def test_sessionfinish_emits_the_warning_annotation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """接线证明：真调用 session 结束钩子，stdout 里必须出现 ::warning::。

    这是本层唯一会红在"函数对但没人调用"上的断言 —— 只测 `_repair_report()`
    的话，把钩子里那三行删掉照样全绿（本仓栽过同形的坑）。

    三个模块级单例重置被替换成 no-op：它们是给**会话真正结束**用的，在测试中途
    调用会关掉别的用例正在用的连接池。要做断言的只有"报告有没有被打印"。
    """
    from types import SimpleNamespace

    import tests.conftest as c

    monkeypatch.setattr(c, "_REPAIR_COUNT", 2)
    monkeypatch.setattr(c, "_tmp_dirs", [])
    monkeypatch.setattr("maop.core.backends.db_utils.close_all_pools", lambda: None)
    monkeypatch.setattr("maop.core.backends.backends.reset_backends", lambda: None)
    monkeypatch.setattr(
        "maop.core.agent.plugins_hooks.hook_manager.reset_hook_manager", lambda: None
    )

    class _Session:
        def __init__(self) -> None:
            self.config = SimpleNamespace(workerinput={})

    c.pytest_sessionfinish(_Session(), 0)  # type: ignore[arg-type]

    out = capsys.readouterr().out
    assert "::warning title=leak-probe::" in out, out
    assert "2" in out, out
    assert "master" in out, out


def test_sessionfinish_is_silent_when_no_repair_happened(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """对照：干净跑不许出现 ::warning::（否则注解区会被自己人淹掉）。"""
    from types import SimpleNamespace

    import tests.conftest as c

    monkeypatch.setattr(c, "_REPAIR_COUNT", 0)
    monkeypatch.setattr(c, "_tmp_dirs", [])
    monkeypatch.setattr("maop.core.backends.db_utils.close_all_pools", lambda: None)
    monkeypatch.setattr("maop.core.backends.backends.reset_backends", lambda: None)
    monkeypatch.setattr(
        "maop.core.agent.plugins_hooks.hook_manager.reset_hook_manager", lambda: None
    )

    class _Session:
        def __init__(self) -> None:
            self.config = SimpleNamespace(workerinput={})

    c.pytest_sessionfinish(_Session(), 0)  # type: ignore[arg-type]

    assert "::warning title=leak-probe::" not in capsys.readouterr().out


# ── T2.3-3b: 会话结束报告的编码安全（2026-10-04 CI 实测）─────────────
#
# CI 实测（windows-latest 3.10/3.13）：`print(中文)` 在 en-US runner 的 cp1252
# 控制台上抛 UnicodeEncodeError，而它发生在 `pytest_sessionfinish` 里 —— 异常冒出去
# 把整个 pytest 会话崩成 exit=1，junit 里却"没有任何失败用例"。于是"守卫报了个警"
# 变成了"测试作业失败"。本仓在 scripts/ci_merge_gate.py 栽过同形，这次是在测试钩子里。


class _Cp1252Stdout:
    """模拟 cp1252 控制台：写非 ASCII 直接抛，行为与 Windows runner 一致。"""

    def __init__(self) -> None:
        self.written: list[str] = []

    def write(self, text: str) -> int:
        text.encode("cp1252")  # 中文在这里抛 UnicodeEncodeError
        self.written.append(text)
        return len(text)

    def flush(self) -> None:
        pass


def test_emit_session_warning_survives_a_cp1252_console(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cp1252 控制台下必须降级成 ASCII，而不是把会话崩掉。"""
    import sys

    import tests.conftest as c

    fake = _Cp1252Stdout()
    monkeypatch.setattr(sys, "stdout", fake)

    c._emit_session_warning("本进程共修复被替换的 subprocess.run 3 次")  # 不许抛

    assert fake.written, "降级路径什么都没写出去 —— 报告丢了"
    joined = "".join(fake.written)
    assert joined.isascii(), joined
    assert "::warning title=leak-probe::" in joined, joined
    # 中文应退化成 \uXXXX 转义而不是被丢弃
    assert "\\u" in joined, joined


def test_emit_session_warning_keeps_utf8_text_when_possible(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """正常（UTF-8）控制台下走原样输出，不做无谓转义。"""
    import tests.conftest as c

    c._emit_session_warning("泄漏修复 2 次")
    out = capsys.readouterr().out
    assert out.startswith("::warning title=leak-probe::"), out
    assert "泄漏修复 2 次" in out, out


def test_repair_report_includes_culprit_attribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """报告必须带归因串 —— 只报次数的话，拿到告警也查不下去。

    这是 CI 实测暴露的：Windows 腿上确实发生了修复（`_REPAIR_COUNT > 0`），
    但 logging 里的 culprit 只对**失败用例**可见，日志里一条都看不到。
    """
    import tests.conftest as c

    monkeypatch.setattr(c, "_REPAIR_COUNT", 2)
    monkeypatch.setattr(c, "_REPAIR_WHO", ["tests/somewhere.py::test_x @ some.py:42"])
    text = c._repair_report("gw3")
    assert text is not None
    assert "tests/somewhere.py::test_x @ some.py:42" in text, text
