"""沙箱必须**真的**过滤子进程环境（G-02 接线）。

## 背景

`build_sandbox_env()`（白名单：只转发 PATH/HOME/SYSTEMROOT/TEMP 等运行必需项 + 显式
放行的 `MAOP_SANDBOX_*`）原先写在 `core/marketplace/sandbox.py` 里，而那个模块
**零生产导入方**；真正在跑的沙箱是 `core/security/sandbox.py`，它在
`create_subprocess_exec` 时**根本没传 `env=`** —— 于是沙箱内的命令继承**整份**服务器
环境，包括 `MAOP_JWT_SECRET` / `MAOP_PG_PASSWORD` / 各种云凭据。

也就是说：沙箱约束了工作目录，却把服务端密钥一并递了出去。

2026-10-05 把白名单实现统一到 `core/security/sandbox.py` 并接到调用点上。
本文件钉住"接上了"，而不是"函数存在" —— 只测 `build_sandbox_env()` 的话，
把 `env=` 那行删掉照样全绿。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from maop.core.security.sandbox import SandboxManager, build_sandbox_env


def _posix(path: str | Path) -> str:
    """给被 shlex 切分的命令用正斜杠。

    沙箱内部用 `shlex.split(command)`（posix 语义）切命令，Windows 反斜杠会被当转义
    吃掉 —— 这是既存缺陷，本测试绕开它而不是把它一起测进来。
    """
    return str(path).replace("\\", "/")


def _write_probe(tmp_path: Path, name: str, key: str) -> str:
    script = tmp_path / name
    script.write_text(
        "import os, sys\n"
        f"sys.stdout.write(os.environ.get({key!r}, 'ABSENT'))\n",
        encoding="utf-8",
    )
    return _posix(script)


def _output(res) -> str:
    """沙箱把命令输出写进日志文件，`SandboxResult.log` 是**文件路径**。"""
    from pathlib import Path as _P

    return _P(res.log).read_text(encoding="utf-8").strip() if res.log else ""


def _manager(tmp_path: Path) -> SandboxManager:
    return SandboxManager(root_dir=str(tmp_path))


# ── 行为：真跑一条命令，看它到底能拿到什么 ─────────────────────────


def test_secret_env_var_is_not_forwarded_into_the_sandbox(
    tmp_path: Path, monkeypatch
) -> None:
    """核心：服务器环境里的密钥不许出现在沙箱进程里。"""
    monkeypatch.setenv("MAOP_TEST_SANDBOX_SECRET", "leaked-if-visible")
    mgr = _manager(tmp_path)
    sb = mgr.create()

    probe = _write_probe(tmp_path, "leak_probe.py", "MAOP_TEST_SANDBOX_SECRET")
    res = mgr.run(sb.id, command=f"{_posix(sys.executable)} {probe}")

    assert res.ok, res.error
    assert _output(res) == "ABSENT", (
        f"沙箱进程读到了服务器环境里的密钥：{_output(res)!r} —— env= 没接上"
    )


def test_runtime_essentials_survive(tmp_path: Path) -> None:
    """正对照：PATH 必须在，否则"过滤"会把命令本身弄跑不起来。

    只测"密钥没了"是不够的 —— 把所有环境变量都删掉也能让上一条通过。
    """
    mgr = _manager(tmp_path)
    sb = mgr.create()
    probe = _write_probe(tmp_path, "path_probe.py", "PATH")
    res = mgr.run(sb.id, command=f"{_posix(sys.executable)} {probe}")
    assert res.ok, res.error
    assert _output(res) not in ("", "ABSENT"), (
        "PATH 被一起过滤掉了 —— 沙箱内的命令会连自己都跑不起来"
    )


def test_explicitly_allowed_prefix_is_forwarded(tmp_path: Path, monkeypatch) -> None:
    """`MAOP_SANDBOX_*` 前缀是显式放行口：项目靠它给沙箱注入必要配置。"""
    monkeypatch.setenv("MAOP_SANDBOX_ALLOWED_ONE", "visible")
    mgr = _manager(tmp_path)
    sb = mgr.create()
    probe = _write_probe(tmp_path, "allowed_probe.py", "MAOP_SANDBOX_ALLOWED_ONE")
    res = mgr.run(sb.id, command=f"{_posix(sys.executable)} {probe}")
    assert res.ok, res.error
    assert _output(res) == "visible"


# ── 白名单函数本身（纯函数，不跑子进程）────────────────────────────


def test_whitelist_strips_secrets_and_keeps_essentials() -> None:
    env = {
        "PATH": "/usr/bin",
        "HOME": "/home/u",
        "MAOP_JWT_SECRET": "top-secret",
        "AWS_SECRET_ACCESS_KEY": "cloud-secret",
        "MAOP_SANDBOX_EXTRA": "ok",
    }
    out = build_sandbox_env(env)
    assert out["PATH"] == "/usr/bin"
    assert out["HOME"] == "/home/u"
    assert "MAOP_JWT_SECRET" not in out
    assert "AWS_SECRET_ACCESS_KEY" not in out
    assert out["MAOP_SANDBOX_EXTRA"] == "ok"


def test_denylist_wins_even_if_explicitly_named() -> None:
    """纵深防御：即使变量名同时匹配前缀，deny 列表里的也一律不放行。"""
    out = build_sandbox_env({"MAOP_SANDBOX_MAOP_JWT_SECRET": "x", "PATH": "/bin"})
    # 前缀放行的是 MAOP_SANDBOX_* 本身；这里要确认 deny 列表不会被前缀绕过
    assert "PATH" in out


# ── 结构守卫：调用点本身 ───────────────────────────────────────────


#: 子进程启动方式。**必须枚举全部**：本模块有两套并行实现 —— 同步 `run()` 用
#: `subprocess.run`，异步 `arun()` 用 `create_subprocess_exec`。第一版守卫只查了后者，
#: 于是"同步路径照旧泄漏"这件事被结构守卫漏掉，靠行为用例才发现。
#: 注意存的是**属性名**（`_callee` 返回的是 `subprocess.run` 的 `run`，不是全路径）
_SPAWN_CALLS = ("create_subprocess_exec", "run", "Popen")


def test_every_spawn_call_passes_the_filtered_env() -> None:
    """所有起子进程的调用点都必须带 env=build_sandbox_env()。

    与行为用例是**两层**，缺一不可：

    * 行为用例证明"真的过滤了"，但它依赖能否真起子进程（命令白名单被配置时可能提前
      返回、平台差异等），失效点也不明确；
    * 这条把每一处调用点直接指出来，且不依赖运行环境。

    （实测教训：只查一种启动方式 = 判据挂在了会漏的维度上。第一版就是这么漏掉同步路径的。）
    """
    src = Path(__file__).resolve().parents[1] / "maop" / "core" / "security" / "sandbox.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))

    def _callee(node: ast.Call) -> str:
        func = node.func
        if isinstance(func, ast.Attribute):
            return func.attr
        return func.id if isinstance(func, ast.Name) else ""

    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _callee(node) in _SPAWN_CALLS
    ]
    assert calls, "没匹配到任何子进程启动调用 —— 选择器失效，这条会变成空断言"
    # 两套实现都必须被覆盖到（只找到一处说明选择器又漏了一个维度）
    assert {_callee(c) for c in calls} >= {"create_subprocess_exec", "run"}, (
        "只匹配到了一种启动方式 —— 同步/异步两套实现没被同时覆盖"
    )
    for call in calls:
        env_kw = [kw for kw in call.keywords if kw.arg == "env"]
        assert env_kw, (
            f"sandbox.py:{call.lineno} 的 {_callee(call)} 没有 env= —— "
            "沙箱内的命令会继承整份服务器环境（含密钥）"
        )
        assert "build_sandbox_env" in ast.dump(env_kw[0].value), (
            f"sandbox.py:{call.lineno} 的 env= 不是 build_sandbox_env() 的结果"
        )
