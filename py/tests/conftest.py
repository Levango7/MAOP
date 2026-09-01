"Global test fixtures — overrides pytest's tmp_path to avoid Windows\nPermissionError on ``C:\\Users\\<user>\\AppData\\Local\\Temp\\pytest-of-<user>``.\n\nThe built-in ``tmp_path`` fixture creates directories under a shared\n``pytest-of-<user>`` base.  On Windows this base can accumulate restrictive\nACLs (e.g. after a run under a different session / elevated prompt), causing\nevery subsequent ``tmp_path`` access to raise ``PermissionError [WinError 5]``.\n\nFix: provide our own ``tmp_path`` that uses ``tempfile.mkdtemp()`` instead.\nEach test gets an isolated temp directory that is cleaned up after the test\nsession ends.\n"

from __future__ import annotations

import contextlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# ── Force test environment BEFORE any maop import ──────────────────
# server.py import 时固化 _auth_enabled / _rl_enabled（get_settings 单例、
# 模块级 os.environ 读取）。若 MAOP_ENV 未设置，_default_auth_enabled()
# 按 secure-by-default 返回 True → AuthMiddleware 启用。
# MAOP_AUTH_DISABLED_ADMIN=1：auth 关闭（MAOP_AUTH=0）时中间件仍授予 admin
# 角色（middleware.py:65），兼容两类测试：token_stream（需 auth=0 避免 401）
# 与 routers smoke/batch（require_admin 需 admin 角色）。生产不设此值。
os.environ.setdefault("MAOP_ENV", "test")
os.environ.setdefault("MAOP_AUTH", "0")
os.environ.setdefault("MAOP_AUTH_DISABLED_ADMIN", "1")
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
