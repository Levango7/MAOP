"""M-X1 M1：Marketplace 验签公钥三通道解析测试（全部离线）。

锁死契约：D2 优先级（env → 内置 → HTTPS）；HTTPS 通道 bootstrap 钉扎
（无指纹拒绝启用、指纹不符拒绝）+ SSRF 三闸（scheme 必须 https://、
解析边界全公网单播、禁重定向）+ 体积上限；拉取结果不落盘；HTTPS 失败
降级 None（验签语义随 require_signature 在安装时 fail-closed）。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import cast

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

# typing.Self 是 3.11+；仓库测试惯例走 typing_extensions（py3.10 矩阵腿必经）。
from typing_extensions import Self

from maop.core.marketplace.key_resolve import (
    build_verifier,
    fetch_verify_key_https,
    key_fingerprint,
    resolve_marketplace_verify_key,
)
from maop.core.marketplace.signing import generate_keypair, sign_payload

_FAKE_PEM = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MCowBQYDK2VwAyEAGb9EDWbLDO054qIZAe94ajYUOE1AwFvbmZQsVFdJ1vU=\n"
    "-----END PUBLIC KEY-----\n"
)
_PUBLIC_IP = "93.184.216.34"


def _patch_dns(monkeypatch: pytest.MonkeyPatch, addr: str) -> None:
    """DNS 打桩：getaddrinfo 固定返回给定地址（零真实解析）。"""

    def _fake_getaddrinfo(host: str, port: int, **kw: object) -> list:
        return [(2, 1, 6, "", (addr, port))]

    monkeypatch.setattr(
        "maop.core.marketplace.key_resolve.socket.getaddrinfo",
        _fake_getaddrinfo,
    )


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self, amount: int) -> bytes:
        return self._payload[:amount]

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _FakeOpener:
    def __init__(self, payload: bytes, seen: list[str]) -> None:
        self._payload = payload
        self.seen = seen

    def open(self, request: object, timeout: float = 0.0) -> _FakeResponse:
        self.seen.append(getattr(request, "full_url", str(request)))
        return _FakeResponse(self._payload)


def _patch_opener(
    monkeypatch: pytest.MonkeyPatch, payload: bytes
) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr(
        "maop.core.marketplace.key_resolve.urllib.request.build_opener",
        lambda handler: _FakeOpener(payload, seen),
    )
    return seen


# ── key_fingerprint ──────────────────────────────────────────────


def test_fingerprint_normalizes_line_endings() -> None:
    crlf = _FAKE_PEM.replace("\n", "\r\n").encode("utf-8")
    lf = _FAKE_PEM.encode("utf-8")
    assert key_fingerprint(crlf) == key_fingerprint(lf)
    assert key_fingerprint(lf) == hashlib.sha256(lf.strip()).hexdigest()


# ── fetch_verify_key_https（DNS + opener 双打桩，零网络）──────────


def test_fetch_rejects_non_https_scheme() -> None:
    for url in ("http://keys.example.com/pub.pem", "ftp://x/y", "file:///etc/key"):
        with pytest.raises(ValueError, match="https://"):
            fetch_verify_key_https(url, "a" * 64)


def test_fetch_rejects_missing_fingerprint() -> None:
    with pytest.raises(ValueError, match="fingerprint is required"):
        fetch_verify_key_https("https://keys.example.com/pub.pem", "  ")


def test_fetch_rejects_unresolvable_host(monkeypatch: pytest.MonkeyPatch) -> None:
    def _gaierror(host: str, port: int, **kw: object) -> list:
        import socket

        raise socket.gaierror("name or service not known")

    monkeypatch.setattr(
        "maop.core.marketplace.key_resolve.socket.getaddrinfo", _gaierror
    )
    with pytest.raises(ValueError, match="unresolvable"):
        fetch_verify_key_https("https://k.example.com/p", "a" * 64)


def test_fetch_rejects_loopback_resolved_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SSRF 闸：解析到环回地址即拒绝（本机服务不可达）。"""
    _patch_dns(monkeypatch, "127.0.0.1")
    with pytest.raises(ValueError, match="SSRF boundary"):
        fetch_verify_key_https("https://k.example.com/p", "a" * 64)


def test_fetch_rejects_link_local_metadata_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SSRF 闸：云元数据地址 169.254.169.254 必须被挡。"""
    _patch_dns(monkeypatch, "169.254.169.254")
    with pytest.raises(ValueError, match="SSRF boundary"):
        fetch_verify_key_https("https://k.example.com/p", "a" * 64)


def test_fetch_rejects_private_range_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SSRF 闸：RFC1918 私网段拒绝（内网服务不可达）。"""
    _patch_dns(monkeypatch, "10.1.2.3")
    with pytest.raises(ValueError, match="SSRF boundary"):
        fetch_verify_key_https("https://k.example.com/p", "a" * 64)


def test_fetch_rejects_oversized_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dns(monkeypatch, _PUBLIC_IP)
    _patch_opener(monkeypatch, b"x" * 70000)
    with pytest.raises(ValueError, match="cap"):
        fetch_verify_key_https("https://k.example.com/p", "a" * 64)


def test_fetch_rejects_fingerprint_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dns(monkeypatch, _PUBLIC_IP)
    _patch_opener(monkeypatch, _FAKE_PEM.encode("utf-8"))
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        fetch_verify_key_https("https://k.example.com/p", "0" * 64)


def test_fetch_accepts_matching_fingerprint_crlf_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """签发侧以 LF 存储、分发侧 CRLF 传输：归一化后指纹必须一致。"""
    crlf_pem = _FAKE_PEM.replace("\n", "\r\n").encode("utf-8")
    fp = key_fingerprint(crlf_pem)
    _patch_dns(monkeypatch, _PUBLIC_IP)
    seen = _patch_opener(monkeypatch, crlf_pem)
    data = fetch_verify_key_https("https://k.example.com/p", fp)
    assert data == crlf_pem
    assert seen and seen[0].startswith("https://")


# ── resolve_marketplace_verify_key：D2 优先级 ────────────────────


def _plant_builtin(root: Path, pem: str) -> None:
    cfg = root / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "marketplace_verify_key.pub").write_text(pem, encoding="utf-8")


def test_resolve_env_path_wins_over_builtin(tmp_path: Path) -> None:
    env_key = tmp_path / "env.pub"
    env_key.write_text("ENV-KEY", encoding="utf-8")
    _plant_builtin(tmp_path, "BUILTIN-KEY")
    pem = resolve_marketplace_verify_key(tmp_path, env_value=str(env_key))
    assert pem == b"ENV-KEY"


def test_resolve_env_inline_pem(tmp_path: Path) -> None:
    pem = resolve_marketplace_verify_key(tmp_path, env_value=_FAKE_PEM)
    assert pem == _FAKE_PEM.encode("utf-8")


def test_resolve_env_garbage_is_ignored_falls_to_builtin(tmp_path: Path) -> None:
    _plant_builtin(tmp_path, "BUILTIN-KEY")
    pem = resolve_marketplace_verify_key(
        tmp_path, env_value="not-a-path-or-pem"
    )
    assert pem == b"BUILTIN-KEY"


def test_resolve_builtin_channel(tmp_path: Path) -> None:
    _plant_builtin(tmp_path, "BUILTIN-KEY")
    assert resolve_marketplace_verify_key(tmp_path) == b"BUILTIN-KEY"


def test_resolve_all_channels_unconfigured_returns_none(tmp_path: Path) -> None:
    assert resolve_marketplace_verify_key(tmp_path) is None


def test_resolve_https_refused_without_fingerprint(tmp_path: Path) -> None:
    pem = resolve_marketplace_verify_key(
        tmp_path, key_server_url="https://k.example.com/p"
    )
    assert pem is None


def test_resolve_https_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fp = key_fingerprint(_FAKE_PEM.encode("utf-8"))
    _patch_dns(monkeypatch, _PUBLIC_IP)
    _patch_opener(monkeypatch, _FAKE_PEM.encode("utf-8"))
    pem = resolve_marketplace_verify_key(
        tmp_path,
        key_server_url="https://k.example.com/p",
        expected_fp=fp,
    )
    assert pem == _FAKE_PEM.encode("utf-8")


def test_resolve_https_failure_degrades_to_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """网络失败 → 告警 + None（不中断启动；验签流程安装时关死）。"""

    class _BoomOpener:
        def open(self, request: object, timeout: float = 0.0) -> object:
            raise OSError("connection refused")

    _patch_dns(monkeypatch, _PUBLIC_IP)
    monkeypatch.setattr(
        "maop.core.marketplace.key_resolve.urllib.request.build_opener",
        lambda handler: _BoomOpener(),
    )
    pem = resolve_marketplace_verify_key(
        tmp_path,
        key_server_url="https://k.example.com/p",
        expected_fp="a" * 64,
    )
    assert pem is None


# ── build_verifier：签发→验签往返（真 Ed25519，零网络）───────────


def test_build_verifier_roundtrip() -> None:
    private_pem, public_pem = generate_keypair()
    payload = {"name": "demo", "version": "1"}
    private_key = cast(
        Ed25519PrivateKey,
        serialization.load_pem_private_key(
            private_pem.encode("utf-8"), password=None
        ),
    )
    signature_hex = sign_payload(payload, private_key)
    verifier = build_verifier(public_pem.encode("utf-8"))
    assert verifier.verify(payload, signature_hex) is True
