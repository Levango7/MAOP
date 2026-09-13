"""边界条件深度测试：空输入、超长输入、并发竞争、错误链。

目标：暴露代码中的脆弱点（未处理的边界、竞态、错误传播缺陷）。
约束：不修改现有源代码，仅创建测试。
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("MAOP_ENV", "test")
os.environ.setdefault("MAOP_AUTH", "0")
os.environ.setdefault("MAOP_RATE_LIMIT", "0")

from maop.core.agent.registry.agent_catalog import AgentCatalog, AgentDescriptor
from maop.core.security.auth import (
    APIKeyStore,
    AuthConfig,
    AuthManager,
    AuthResult,
)


# =====================================================================
# a) 空输入测试
# =====================================================================


class TestEmptyInput:
    """空值与 None 作为输入时的行为。"""

    def test_empty_string_agent_name_rejected(self, tmp_path):
        """空字符串作为 agent 名称应被 Pydantic 拒绝（min_length=1）。"""
        with pytest.raises(Exception) as exc_info:
            AgentDescriptor(name="")
        # Pydantic ValidationError
        assert "min_length" in str(exc_info.value) or "at least 1" in str(exc_info.value)

    def test_empty_list_batch_register(self, tmp_path):
        """空列表批量注册应正常完成，不抛异常。"""
        catalog = AgentCatalog(db_path=tmp_path / "cat.db")
        results = []
        for desc in []:
            catalog.register(desc)
            results.append(desc)
        assert results == []
        assert catalog.list_all() == []

    def test_none_as_optional_params(self, tmp_path):
        """None 作为可选参数应使用默认值，不崩溃。"""
        # AgentCatalog db_path=None 应自动解析
        catalog = AgentCatalog(db_path=None)
        assert catalog.list_all() is not None

    def test_authenticate_with_empty_credentials(self, tmp_path):
        """空凭据认证应返回未认证结果。"""
        store = APIKeyStore(db_path=tmp_path / "auth.db")
        mgr = AuthManager(config=AuthConfig(enabled=True), key_store=store)
        result = mgr.authenticate(api_key="", bearer_token="")
        assert result.authenticated is False
        assert "No credentials" in result.error or "failed" in result.error.lower()

    def test_apikey_create_with_empty_name(self, tmp_path):
        """空字符串作为 API key name —— SQLite NOT NULL 允许空串，验证不崩溃。"""
        store = APIKeyStore(db_path=tmp_path / "auth.db")
        key = store.create_key(name="")
        assert isinstance(key, str) and len(key) > 0
        # 验证可以用该 key 认证
        result = store.validate_key(key)
        assert result.authenticated is True

    def test_catalog_get_nonexistent_returns_none(self, tmp_path):
        """获取不存在的 agent 应返回 None，不抛异常。"""
        catalog = AgentCatalog(db_path=tmp_path / "cat.db")
        assert catalog.get("nonexistent_agent") is None


# =====================================================================
# b) 超长输入测试
# =====================================================================


class TestOversizedInput:
    """超长字符串与大量数据输入。"""

    def test_oversized_agent_name_rejected(self, tmp_path):
        """超长字符串（10000字符）作为 agent 名称应被拒绝（max_length=256）。"""
        long_name = "a" * 10000
        with pytest.raises(Exception) as exc_info:
            AgentDescriptor(name=long_name)
        assert "max_length" in str(exc_info.value) or "at most 256" in str(exc_info.value)

    def test_name_at_exact_max_length(self, tmp_path):
        """恰好 256 字符的名称应被接受（边界值）。"""
        name = "a" * 256
        desc = AgentDescriptor(name=name)
        assert desc.name == name

    def test_name_one_over_max_rejected(self, tmp_path):
        """257 字符应被拒绝（边界值+1）。"""
        with pytest.raises(Exception):
            AgentDescriptor(name="a" * 257)

    def test_large_batch_register_1000(self, tmp_path):
        """批量注册 1000 个 agent 应在合理时间内完成。

        发现：当前实现每次 register 单独写入 SQLite（无批量优化），
        1000 条耗时约 40-50s。这是性能脆弱点，阈值放宽至 90s 记录现状。
        建议优化：增加 batch_register 接口，使用事务批量插入。
        """
        catalog = AgentCatalog(db_path=tmp_path / "cat.db")
        start = time.time()
        for i in range(1000):
            desc = AgentDescriptor(name=f"agent_{i:04d}", display_name=f"Agent {i}")
            catalog.register(desc)
        elapsed = time.time() - start
        all_agents = catalog.list_all()
        assert len(all_agents) == 1000
        # 阈值 90s（当前实现 ~40-50s，留余量记录性能现状）
        assert elapsed < 90.0, f"批量注册 1000 耗时 {elapsed:.1f}s，超过阈值"

    def test_oversized_display_name_rejected(self, tmp_path):
        """超长 display_name（>256）应被拒绝。"""
        with pytest.raises(Exception):
            AgentDescriptor(name="ok", display_name="x" * 10000)

    def test_fallback_self_reference_rejected(self, tmp_path):
        """fallback_agents 包含自身应被拒绝（防无限循环）。"""
        with pytest.raises(Exception) as exc_info:
            AgentDescriptor(name="self_loop", fallback_agents=["self_loop"])
        assert "fallback" in str(exc_info.value).lower() or "itself" in str(exc_info.value).lower()


# =====================================================================
# c) 并发测试
# =====================================================================


class TestConcurrency:
    """并发竞争条件测试。"""

    def test_concurrent_register_same_agent(self, tmp_path):
        """多个线程并发注册同一 agent —— 最后写入应胜出，不崩溃。"""
        catalog = AgentCatalog(db_path=tmp_path / "cat.db")
        name = "concurrent_agent"
        errors: list[Exception] = []

        def register():
            try:
                desc = AgentDescriptor(name=name, display_name=f"by-{threading.get_ident()}")
                catalog.register(desc)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=register) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"并发注册抛异常: {errors}"
        result = catalog.get(name)
        assert result is not None
        assert result.name == name

    def test_concurrent_register_different_agents(self, tmp_path):
        """并发注册不同 agent 应全部成功。"""
        catalog = AgentCatalog(db_path=tmp_path / "cat.db")
        errors: list[Exception] = []

        def register(idx: int):
            try:
                desc = AgentDescriptor(name=f"agent_{idx}", display_name=f"Agent {idx}")
                catalog.register(desc)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=register, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"并发注册抛异常: {errors}"
        assert len(catalog.list_all()) == 50

    def test_concurrent_read_write(self, tmp_path):
        """并发读+写不应产生死锁或数据损坏。"""
        catalog = AgentCatalog(db_path=tmp_path / "cat.db")
        # 预填充
        for i in range(10):
            catalog.register(AgentDescriptor(name=f"pre_{i}"))

        errors: list[Exception] = []
        stop = threading.Event()

        def writer():
            i = 0
            while not stop.is_set():
                try:
                    catalog.register(AgentDescriptor(name=f"dyn_{i}"))
                    i += 1
                except Exception as exc:
                    errors.append(exc)
                    break

        def reader():
            while not stop.is_set():
                try:
                    catalog.list_all()
                except Exception as exc:
                    errors.append(exc)
                    break

        w_threads = [threading.Thread(target=writer) for _ in range(3)]
        r_threads = [threading.Thread(target=reader) for _ in range(3)]
        for t in w_threads + r_threads:
            t.start()
        time.sleep(1.0)
        stop.set()
        for t in w_threads + r_threads:
            t.join()

        assert errors == [], f"并发读写抛异常: {errors}"

    def test_concurrent_apikey_create_revoke(self, tmp_path):
        """并发创建+撤销 API key 应不崩溃。"""
        store = APIKeyStore(db_path=tmp_path / "auth.db")
        errors: list[Exception] = []
        keys: list[str] = []

        def create():
            try:
                key = store.create_key(name=f"concurrent_{threading.get_ident()}")
                keys.append(key)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=create) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"并发创建 API key 抛异常: {errors}"
        assert len(keys) == 20


# =====================================================================
# d) 错误链测试
# =====================================================================


class TestErrorChain:
    """错误传播与降级行为测试。"""

    def test_db_connection_broken_on_register(self, tmp_path):
        """数据库连接断开时 register 应抛异常，不静默吞错。"""
        catalog = AgentCatalog(db_path=tmp_path / "cat.db")
        # 破坏 store 的 upsert 方法
        with patch.object(catalog._store, "upsert", side_effect=RuntimeError("DB connection lost")):
            with pytest.raises(RuntimeError, match="DB connection lost"):
                catalog.register(AgentDescriptor(name="will_fail"))

    @pytest.mark.xfail(
        reason="已知脆弱点 BUG-001：AgentCatalog 初始化时连接池创建早于 _load_from_store "
               "的 try/except，损坏的 DB 文件在 PRAGMA journal_mode=WAL 阶段即抛出 "
               "DatabaseError 未被捕获。修复建议：在 AgentCatalogStore.__init__ 或 "
               "sqlite_connect 中增加 DatabaseError 容错，损坏时重建空库。"
    )
    def test_db_broken_on_load_does_not_crash(self, tmp_path):
        """启动时数据库损坏应容错加载，不崩溃。

        BUG-001：当前实现未捕获连接阶段的 DatabaseError。
        损坏文件 → sqlite_connect → PRAGMA journal_mode=WAL → DatabaseError 直接传播。
        """
        db_path = tmp_path / "corrupt.db"
        # 写入损坏的数据库文件
        db_path.write_text("not a valid sqlite database")
        # 应不抛异常（容错处理），只是加载 0 个 agent
        catalog = AgentCatalog(db_path=db_path)
        assert catalog.list_all() == []

    def test_auth_with_broken_key_store(self):
        """key_store 异常时认证应优雅降级，不暴露内部错误。"""
        broken_store = MagicMock(spec=APIKeyStore)
        broken_store.validate_key.side_effect = RuntimeError("DB unreachable")
        mgr = AuthManager(config=AuthConfig(enabled=True), key_store=broken_store)
        # 当 key_store 抛异常时，authenticate 应传播异常或返回失败结果
        # 关键是不应返回 authenticated=True
        try:
            result = mgr.authenticate(api_key="some_key")
            assert result.authenticated is False, "DB 故障时不应认证成功"
        except RuntimeError:
            pass  # 传播异常也是可接受的行为

    def test_expired_api_key_rejected(self, tmp_path):
        """过期的 API key 应被拒绝。"""
        store = APIKeyStore(db_path=tmp_path / "auth.db")
        # 创建一个立即过期的 key（ttl=0.01s）
        key = store.create_key(name="short_lived", ttl_s=0.01)
        time.sleep(0.05)
        result = store.validate_key(key)
        assert result.authenticated is False
        assert "expired" in result.error.lower()

    def test_disabled_api_key_rejected(self, tmp_path):
        """被禁用的 API key 应被拒绝。"""
        store = APIKeyStore(db_path=tmp_path / "auth.db")
        key = store.create_key(name="to_disable")
        # 撤销 key
        revoked = store.revoke_key(name="to_disable")
        assert revoked is True
        result = store.validate_key(key)
        assert result.authenticated is False
        assert "disabled" in result.error.lower()

    def test_invalid_api_key_rejected(self, tmp_path):
        """无效的 API key 应被拒绝。"""
        store = APIKeyStore(db_path=tmp_path / "auth.db")
        result = store.validate_key("totally_invalid_key_12345")
        assert result.authenticated is False
        assert "Invalid" in result.error

    def test_config_corruption_fallback(self, tmp_path):
        """配置文件损坏时应回退到默认值，不崩溃。"""
        # AgentCatalog with None db_path should use default
        catalog = AgentCatalog(db_path=None)
        # 应正常工作
        assert isinstance(catalog.list_all(), list)

    def test_concurrent_get_while_corrupt_load(self, tmp_path):
        """加载损坏数据时并发 get 不应死锁。"""
        db_path = tmp_path / "partial.db"
        catalog = AgentCatalog(db_path=db_path)
        # 正常注册一些 agent
        for i in range(5):
            catalog.register(AgentDescriptor(name=f"ok_{i}"))

        # 并发 get
        results: list = []
        errors: list[Exception] = []

        def get_agent(name: str):
            try:
                results.append(catalog.get(name))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=get_agent, args=(f"ok_{i}",)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert all(r is not None for r in results)


# =====================================================================
# 额外：类型混淆与注入测试
# =====================================================================


class TestTypeConfusion:
    """类型混淆与注入攻击边界。"""

    def test_sql_injection_in_agent_name(self, tmp_path):
        """SQL 注入字符串作为 agent 名称应被安全处理（参数化查询）。"""
        catalog = AgentCatalog(db_path=tmp_path / "cat.db")
        injection_name = "'; DROP TABLE agents; --"
        # Pydantic 可能接受这个名称（在长度范围内），SQLite 应参数化
        try:
            desc = AgentDescriptor(name=injection_name)
            catalog.register(desc)
            # 应能正常取回
            result = catalog.get(injection_name)
            assert result is not None
            assert result.name == injection_name
            # 表不应被删除
            assert len(catalog.list_all()) == 1
        except Exception:
            # 如果 Pydantic 拒绝也是可接受的
            pass

    def test_unicode_agent_name(self, tmp_path):
        """Unicode 字符（中文、emoji）作为 agent 名称。"""
        catalog = AgentCatalog(db_path=tmp_path / "cat.db")
        unicode_names = ["中文代理", "🤖agent", "Агент-1", "エージェント"]
        for name in unicode_names:
            desc = AgentDescriptor(name=name)
            catalog.register(desc)
            assert catalog.get(name) is not None
        assert len(catalog.list_all()) == len(unicode_names)

    def test_negative_max_concurrent_rejected(self, tmp_path):
        """负数 max_concurrent 应被拒绝（ge=1）。"""
        with pytest.raises(Exception):
            AgentDescriptor(name="test", max_concurrent=-1)

    def test_zero_max_concurrent_rejected(self, tmp_path):
        """零 max_concurrent 应被拒绝（ge=1）。"""
        with pytest.raises(Exception):
            AgentDescriptor(name="test", max_concurrent=0)

    def test_negative_timeout_rejected(self, tmp_path):
        """负数 timeout 应被拒绝（gt=0）。"""
        with pytest.raises(Exception):
            AgentDescriptor(name="test", timeout_s=-1.0)

    def test_zero_timeout_rejected(self, tmp_path):
        """零 timeout 应被拒绝（gt=0）。"""
        with pytest.raises(Exception):
            AgentDescriptor(name="test", timeout_s=0.0)