"""Tests for HarnessAuthProvider — Harness LICENSE 授权提供者测试。

覆盖：
  * 注册 + 验证往返
  * 不存在 license 验证
  * 绑定 / 解绑 license
  * 绑定检查（匹配 / 不匹配）
  * 吊销 license
  * 吊销后验证无效
  * 过期检查（未过期 / 已过期 / 永不过期）
  * 列出所有 license
  * 持久化（重新打开 DB 数据仍在）
  * 并发访问（多线程安全）
"""
from __future__ import annotations

import threading
import time  # noqa: F401
from datetime import datetime, timedelta, timezone
from pathlib import Path  # noqa: F401
from typing import Any

import pytest

from maop.core.agent.auth.harness_auth import HarnessAuthProvider, HarnessLicense

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def provider(tmp_path: Any) -> HarnessAuthProvider:
    """隔离的 HarnessAuthProvider（独立 DB）。"""
    p = HarnessAuthProvider(db_path=tmp_path / "harness.db")
    yield p
    p.close()


# ── 辅助 ──────────────────────────────────────────────────────────


def _make_license(
    key: str = "ZCODE-LIC-001",
    product: str = "ZCode",
    vendor: str = "Zhipu",
    expires_at: str = "",
) -> HarnessLicense:
    """构造测试用 HarnessLicense。"""
    return HarnessLicense(
        license_key=key,
        product_name=product,
        vendor=vendor,
        issued_at="2026-01-01T00:00:00",
        expires_at=expires_at,
        max_concurrent=5,
        metadata={"tier": "pro"},
    )


def _future_iso(days: int) -> str:
    """返回距今 days 天后的 ISO 日期字符串。"""
    dt = datetime.now(timezone.utc) + timedelta(days=days)
    return dt.strftime("%Y-%m-%d")


def _past_iso(days: int) -> str:
    """返回距今 days 天前的 ISO 日期字符串。"""
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%d")


# ── 注册与验证 ────────────────────────────────────────────────────


class TestRegisterAndValidate:
    """注册 + 验证往返。"""

    def test_register_and_validate(self, provider: HarnessAuthProvider) -> None:
        """注册 license 后验证应返回该 license。"""
        lic = _make_license()
        provider.register_license(lic)
        result = provider.validate_license(lic.license_key)
        assert result is not None
        assert result.license_key == lic.license_key
        assert result.product_name == "ZCode"
        assert result.vendor == "Zhipu"
        assert result.status == "active"
        assert result.max_concurrent == 5
        assert result.metadata == {"tier": "pro"}

    def test_validate_nonexistent(self, provider: HarnessAuthProvider) -> None:
        """验证不存在的 license 应返回 None。"""
        result = provider.validate_license("NONEXISTENT-KEY")
        assert result is None


# ── 绑定 ──────────────────────────────────────────────────────────


class TestBinding:
    """绑定 / 解绑 / 绑定检查。"""

    def test_bind_license(self, provider: HarnessAuthProvider) -> None:
        """绑定 license 到模型账号后应能查到绑定关系。"""
        lic = _make_license()
        provider.register_license(lic)
        provider.bind_license(lic.license_key, "account-001")
        assert provider.check_binding(lic.license_key, "account-001") is True

    def test_check_binding_matched(self, provider: HarnessAuthProvider) -> None:
        """绑定的模型账号匹配时 check_binding 返回 True。"""
        lic = _make_license()
        provider.register_license(lic)
        provider.bind_license(lic.license_key, "glm-account")
        assert provider.check_binding(lic.license_key, "glm-account") is True

    def test_check_binding_unmatched(self, provider: HarnessAuthProvider) -> None:
        """绑定的模型账号不匹配时 check_binding 返回 False。"""
        lic = _make_license()
        provider.register_license(lic)
        provider.bind_license(lic.license_key, "account-a")
        # 不同账号 → 不匹配。
        assert provider.check_binding(lic.license_key, "account-b") is False
        # 未绑定任何账号 → False。
        lic2 = _make_license(key="LIC-002")
        provider.register_license(lic2)
        assert provider.check_binding(lic2.license_key, "account-a") is False

    def test_unbind_license(self, provider: HarnessAuthProvider) -> None:
        """解绑后 check_binding 应返回 False。"""
        lic = _make_license()
        provider.register_license(lic)
        provider.bind_license(lic.license_key, "account-001")
        assert provider.check_binding(lic.license_key, "account-001") is True
        provider.unbind_license(lic.license_key)
        assert provider.check_binding(lic.license_key, "account-001") is False


# ── 吊销 ──────────────────────────────────────────────────────────


class TestRevoke:
    """吊销 license。"""

    def test_revoke_license(self, provider: HarnessAuthProvider) -> None:
        """吊销后 license 的 status 应为 revoked。"""
        lic = _make_license()
        provider.register_license(lic)
        provider.revoke_license(lic.license_key)
        licenses = provider.list_licenses()
        assert len(licenses) == 1
        assert licenses[0].status == "revoked"

    def test_revoked_not_valid(self, provider: HarnessAuthProvider) -> None:
        """吊销后 validate_license 应返回 None。"""
        lic = _make_license()
        provider.register_license(lic)
        provider.revoke_license(lic.license_key)
        assert provider.validate_license(lic.license_key) is None


# ── 过期检查 ──────────────────────────────────────────────────────


class TestExpiry:
    """过期检查。"""

    def test_check_expiry_not_expired(self, provider: HarnessAuthProvider) -> None:
        """未过期的 license：返回 (False, 剩余天数)。"""
        lic = _make_license(expires_at=_future_iso(30))
        provider.register_license(lic)
        is_expired, remaining = provider.check_expiry(lic.license_key)
        assert is_expired is False
        # 剩余天数应接近 30（允许 1 天误差，因时间戳截断到日期）。
        assert 29 <= remaining <= 30

    def test_check_expiry_expired(self, provider: HarnessAuthProvider) -> None:
        """已过期的 license：返回 (True, 0)，且 status 自动置为 expired。"""
        lic = _make_license(expires_at=_past_iso(5))
        provider.register_license(lic)
        is_expired, remaining = provider.check_expiry(lic.license_key)
        assert is_expired is True
        assert remaining == 0
        # status 应被自动更新为 expired。
        licenses = provider.list_licenses()
        assert licenses[0].status == "expired"
        # 过期后 validate_license 应返回 None。
        assert provider.validate_license(lic.license_key) is None

    def test_check_expiry_no_expiry(self, provider: HarnessAuthProvider) -> None:
        """永不过期的 license（expires_at 为空）：返回 (False, -1)。"""
        lic = _make_license(expires_at="")
        provider.register_license(lic)
        is_expired, remaining = provider.check_expiry(lic.license_key)
        assert is_expired is False
        assert remaining == -1


# ── 列表 ──────────────────────────────────────────────────────────


class TestList:
    """列出所有 license。"""

    def test_list_licenses(self, provider: HarnessAuthProvider) -> None:
        """list_licenses 应返回所有已注册的 license。"""
        lic1 = _make_license(key="LIC-A", product="ZCode", vendor="Zhipu")
        lic2 = _make_license(key="LIC-B", product="DeepSeek Harness", vendor="DeepSeek")
        provider.register_license(lic1)
        provider.register_license(lic2)
        licenses = provider.list_licenses()
        assert len(licenses) == 2
        keys = {l.license_key for l in licenses}
        assert keys == {"LIC-A", "LIC-B"}
        # 验证产品名正确。
        products = {l.license_key: l.product_name for l in licenses}
        assert products["LIC-A"] == "ZCode"
        assert products["LIC-B"] == "DeepSeek Harness"


# ── 持久化 ────────────────────────────────────────────────────────


class TestPersistence:
    """SQLite 持久化。"""

    def test_persistence(self, tmp_path: Any) -> None:
        """重新打开同一 DB 文件，已注册的 license 应仍在。"""
        db_path = tmp_path / "persist.db"
        lic = _make_license(key="PERSIST-LIC")
        # 第一次实例：注册 license。
        p1 = HarnessAuthProvider(db_path=db_path)
        p1.register_license(lic)
        p1.bind_license(lic.license_key, "persist-account")
        p1.close()
        # 第二次实例：验证数据仍在。
        p2 = HarnessAuthProvider(db_path=db_path)
        try:
            result = p2.validate_license(lic.license_key)
            assert result is not None
            assert result.license_key == "PERSIST-LIC"
            assert result.product_name == "ZCode"
            # 绑定关系也应持久化。
            assert p2.check_binding(lic.license_key, "persist-account") is True
        finally:
            p2.close()


# ── 并发访问 ──────────────────────────────────────────────────────


class TestConcurrency:
    """多线程并发安全。"""

    def test_concurrent_access(self, tmp_path: Any) -> None:
        """多线程并发注册 / 验证 / 绑定不应出错且数据一致。"""
        db_path = tmp_path / "concurrent.db"
        provider = HarnessAuthProvider(db_path=db_path)
        errors: list[Exception] = []
        num_threads = 8
        ops_per_thread = 20

        def worker(tid: int) -> None:
            try:
                for i in range(ops_per_thread):
                    key = f"T{tid}-LIC-{i}"
                    lic = _make_license(key=key)
                    provider.register_license(lic)
                    provider.bind_license(key, f"account-{tid}")
                    # 验证刚注册的 license。
                    result = provider.validate_license(key)
                    if result is None:
                        errors.append(
                            AssertionError(f"线程 {tid} 迭代 {i}: validate 返回 None")
                        )
                    # 检查绑定。
                    if not provider.check_binding(key, f"account-{tid}"):
                        errors.append(
                            AssertionError(f"线程 {tid} 迭代 {i}: 绑定检查失败")
                        )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        provider.close()

        # 不应有任何错误。
        assert errors == [], f"并发访问出错: {errors}"
        # 验证所有 license 都已注册。
        verifier = HarnessAuthProvider(db_path=db_path)
        try:
            all_licenses = verifier.list_licenses()
            assert len(all_licenses) == num_threads * ops_per_thread
        finally:
            verifier.close()