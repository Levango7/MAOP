"""Tests for maop.enterprise.crl -- License 在线撤销（CRL）机制。

Mocks urllib.request.urlopen via monkeypatch to avoid real network calls.
Constructs LicenseInfo directly (no real signature needed, since CRL check
happens after signature verification).
"""

from __future__ import annotations

import io
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from maop.enterprise.crl import CRLError, CRLChecker, LicenseRevokedError
from maop.enterprise.license import LicenseInfo


# -- 辅助函数 ---------------------------------------------------------------


def _make_license_info(customer: str = "Test Corp") -> LicenseInfo:
    """构造一个有效的 LicenseInfo 对象（不需真实签名）。"""
    now = datetime.now(timezone.utc)
    return LicenseInfo(
        customer=customer,
        edition="enterprise",
        issued_at=now - timedelta(days=100),
        expires_at=now + timedelta(days=365),
    )


def _make_crl(
    revoked: list[dict[str, Any]] | None = None,
    expires_in_hours: int = 1,
) -> dict:
    """构造一个合法的 CRL JSON dict。"""
    now = datetime.now(timezone.utc)
    return {
        "version": 1,
        "updated_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=expires_in_hours)).isoformat(),
        "revoked": revoked or [],
    }


class _FakeResponse:
    """Minimal file-like response object compatible with urllib context mgr."""

    def __init__(self, payload: bytes, status: int = 200) -> None:
        self._buf = io.BytesIO(payload)
        self.status = status

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n if n != -1 else -1)

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        pass


@pytest.fixture(autouse=True)
def _clean_crl_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """清除 CRL 相关环境变量，避免污染测试。"""
    for var in ("MAOP_CRL_URL", "MAOP_CRL_CACHE_TTL_S", "MAOP_CRL_STRICT"):
        monkeypatch.delenv(var, raising=False)


# -- 测试用例 ---------------------------------------------------------------


class TestCRLChecker:
    """CRLChecker 单元测试。"""

    def test_crl_not_configured_skips_check(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """未配置 MAOP_CRL_URL 时，不执行 CRL 检查（无网络请求）。"""
        call_count = {"n": 0}

        def fake_urlopen(req, timeout=None):
            call_count["n"] += 1
            return _FakeResponse(b"{}")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        checker = CRLChecker(cache_path=tmp_path / "crl.json")
        assert checker.crl_url == ""
        assert checker.strict is False

        revoked, reason = checker.is_revoked("Anyone")
        assert revoked is False
        assert reason == ""
        # 未配置 URL，不应发起任何网络请求
        assert call_count["n"] == 0

    def test_revoked_license_rejected(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """配置 CRL URL，mock HTTP 返回含撤销条目的 CRL，验证抛 LicenseRevokedError。"""
        crl = _make_crl(revoked=[
            {
                "customer": "Bad Corp",
                "revoked_at": "2026-07-25T14:30:00Z",
                "reason": "non-payment",
            }
        ])

        def fake_urlopen(req, timeout=None):
            return _FakeResponse(json.dumps(crl).encode())

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        checker = CRLChecker(
            crl_url="https://crl.example.com/list.json",
            cache_path=tmp_path / "crl.json",
        )
        info = _make_license_info(customer="Bad Corp")

        with pytest.raises(LicenseRevokedError, match="Bad Corp") as exc_info:
            checker.check_license(info)
        assert exc_info.value.customer == "Bad Corp"
        assert exc_info.value.reason == "non-payment"
        assert exc_info.value.revoked_at == "2026-07-25T14:30:00Z"

    def test_valid_license_passes_crl(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """配置 CRL URL，mock HTTP 返回不含撤销条目的 CRL，验证通过。"""
        crl = _make_crl(revoked=[
            {
                "customer": "Other Corp",
                "revoked_at": "2026-07-20T10:00:00Z",
                "reason": "fraud",
            }
        ])

        def fake_urlopen(req, timeout=None):
            return _FakeResponse(json.dumps(crl).encode())

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        checker = CRLChecker(
            crl_url="https://crl.example.com/list.json",
            cache_path=tmp_path / "crl.json",
        )
        info = _make_license_info(customer="Good Corp")

        # 不抛异常即通过
        checker.check_license(info)

    def test_crl_cache_used_when_fetch_fails(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """第二次检查时 mock HTTP 失败，验证使用缓存。"""
        crl = _make_crl(revoked=[])
        call_count = {"n": 0}

        def fake_urlopen(req, timeout=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # 第一次：返回有效 CRL
                return _FakeResponse(json.dumps(crl).encode())
            # 第二次：网络失败
            raise OSError("network down")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        # TTL=0 使缓存立即过期，第二次必须尝试重新拉取
        checker = CRLChecker(
            crl_url="https://crl.example.com/list.json",
            cache_path=tmp_path / "crl.json",
            cache_ttl_s=0,
        )

        # 第一次：拉取成功，缓存写入
        checker.check_license(_make_license_info("Corp A"))
        assert call_count["n"] == 1
        assert checker.cache_path.exists()

        # 第二次：缓存已过期（TTL=0），拉取失败，降级使用过期缓存
        checker.check_license(_make_license_info("Corp B"))
        assert call_count["n"] == 2  # 确实尝试了拉取

    def test_strict_mode_rejects_when_no_crl(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """MAOP_CRL_STRICT=1 且无 CRL 时抛 CRLError。"""
        monkeypatch.setenv("MAOP_CRL_STRICT", "1")

        def fake_urlopen(req, timeout=None):
            raise OSError("unreachable")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        checker = CRLChecker(
            crl_url="https://crl.example.com/list.json",
            cache_path=tmp_path / "crl.json",  # 不存在
        )
        assert checker.strict is True

        with pytest.raises(CRLError, match="strict"):
            checker.is_revoked("Anyone")

    def test_lax_mode_allows_when_no_crl(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """默认宽松模式，无 CRL 时允许 license。"""
        def fake_urlopen(req, timeout=None):
            raise OSError("unreachable")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        checker = CRLChecker(
            crl_url="https://crl.example.com/list.json",
            cache_path=tmp_path / "crl.json",  # 不存在
        )
        assert checker.strict is False

        revoked, reason = checker.is_revoked("Anyone")
        assert revoked is False
        assert reason == ""

    def test_expired_crl_cache_refetches(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """缓存过期时重新拉取。"""
        # 1. 手动写入一个过期的缓存（mtime 设为 2 小时前）
        old_crl = _make_crl(revoked=[])
        cache_path = tmp_path / "crl.json"
        cache_path.write_text(json.dumps(old_crl), encoding="utf-8")
        old_time = time.time() - 7200
        os.utime(str(cache_path), (old_time, old_time))

        # 2. Mock HTTP 返回新 CRL（含新撤销条目）
        new_crl = _make_crl(revoked=[
            {
                "customer": "Newly Revoked",
                "revoked_at": "2026-07-26T00:00:00Z",
                "reason": "fraud",
            }
        ])
        fetch_count = {"n": 0}

        def fake_urlopen(req, timeout=None):
            fetch_count["n"] += 1
            return _FakeResponse(json.dumps(new_crl).encode())

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        checker = CRLChecker(
            crl_url="https://crl.example.com/list.json",
            cache_path=cache_path,
            cache_ttl_s=3600,  # 1 小时
        )

        # 缓存已过期（mtime 是 2 小时前），应重新拉取
        revoked, reason = checker.is_revoked("Newly Revoked")
        assert fetch_count["n"] == 1  # 确实发起了拉取
        assert revoked is True
        assert reason == "fraud"
