"""Marketplace 验签公钥多通道解析（D2③ HTTPS 拉取，M-X1 M1）。

治理合同：``docs/marketplace-signing-key-governance.md`` D2——三通道按
**运维覆盖语义**回退：环境变量（``MAOP_MARKETPLACE_VERIFY_KEY``，路径或
内联 PEM；运维覆盖/换钥窗口）→ 安装包内置（``config/marketplace_verify_key.pub``，
随产品走、离线可用）→ 企业 distribution server 的 HTTPS 端点（本模块，
M1 扩展）。

**bootstrap 信任**（治理文档 D2 预警）：HTTPS 通道拉来的钥必须自证——
期望指纹（``MAOP_MARKETPLACE_VERIFY_KEY_FP``，对行尾归一化后的 PEM 做
SHA-256）未配置时通道**拒绝启用**（fail-closed）；指纹不符同样拒绝。
不落地、不缓存、不写盘：拉取结果只进内存，消费方经
:func:`build_verifier` 构造验签器。签发侧把 distribution server 公钥指纹
带外分发给运维，即构成钉扎。
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import socket
import urllib.parse
import urllib.request
from pathlib import Path
from typing import cast

from maop.core.marketplace.signing import (
    PackageVerifier,
    load_public_key_from_bytes,
)

logger = logging.getLogger(__name__)

#: 单次拉取的体积上限（PEM 公钥实际 ~120 字节，64 KiB 已是千倍冗余）。
MAX_KEY_BYTES = 65536

_BUILTIN_RELATIVE = Path("config") / "marketplace_verify_key.pub"
_PEM_HEADER = "-----BEGIN"


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """公钥端点不应跳转——重定向是 SSRF 边界校验的绕过面，一律拒绝。"""

    def redirect_request(
        self,
        req: object,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


def _assert_public_host(url: str) -> None:
    """SSRF 边界闸：主机解析出的**所有**地址都必须是公网单播地址。

    任一记录落在私有/环回/链路本地/保留/多播/未指定段即拒绝——挡住内网、
    云元数据（169.254.169.254）、本机服务三类目标。
    """
    parsed = urllib.parse.urlsplit(url)
    hostname = parsed.hostname or ""
    if not hostname:
        raise ValueError("marketplace key server URL has no host: " + url[:120])
    port = parsed.port or 443
    try:
        infos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(
            f"marketplace key server host unresolvable: {exc}"
        ) from exc
    addrs = {info[4][0] for info in infos}
    if not addrs:
        raise ValueError("marketplace key server host did not resolve")
    for addr in addrs:
        ip = ipaddress.ip_address(addr)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError(
                "marketplace key server host resolves to non-public "
                f"address {addr} — refusing (SSRF boundary)"
            )


def key_fingerprint(pem: bytes) -> str:
    """对行尾归一化（``\\r\\n`` → ``\\n``）并去首尾空白后的 PEM 做 SHA-256。

    归一化保证同一把钥在 CRLF/LF 检出差异下指纹一致（与 license 侧
    指纹守卫同一配方）。
    """
    return hashlib.sha256(pem.replace(b"\r\n", b"\n").strip()).hexdigest()


def fetch_verify_key_https(
    url: str,
    expected_fp: str,
    timeout_s: float = 10.0,
    max_bytes: int = MAX_KEY_BYTES,
) -> bytes:
    """从 HTTPS 端点拉取验签公钥 PEM，SSRF 闸 + 指纹钉扎全部通过才返回字节。

    fail-closed 闸序：scheme 必须 https://；期望指纹必须非空；主机解析边界
    （全公网单播）；禁重定向；体积上限；指纹不符即拒绝——任何一闸不过都不
    返回未验证内容。

    残余风险（如实登记）：解析与连接之间的 DNS rebinding TOCTOU 窗口需自管
    连接层才能彻底消解；本通道按「指纹钉扎 + 解析边界 + 禁重定向 + 短超时」
    纵深防御接受该残余，攻击者须同时控制 DNS 并持有正确指纹对应的私钥才有
    实际收益。
    """
    if not url.lower().startswith("https://"):
        raise ValueError(
            "marketplace key server URL must use https://, got: " + url[:120]
        )
    fp_wanted = expected_fp.strip().lower()
    if not fp_wanted:
        raise ValueError(
            "expected fingerprint is required for HTTPS key fetch "
            "(bootstrap trust: an unpinned fetched key trusts the endpoint)"
        )
    _assert_public_host(url)
    request = urllib.request.Request(
        url, headers={"Accept": "application/x-pem-file"}
    )
    opener = urllib.request.build_opener(_NoRedirectHandler())
    with opener.open(request, timeout=timeout_s) as resp:
        data = cast(bytes, resp.read(max_bytes + 1))
    if len(data) > max_bytes:
        raise ValueError(
            f"fetched key exceeds {max_bytes} bytes cap — refusing"
        )
    fp_got = key_fingerprint(data)
    if fp_got != fp_wanted:
        raise ValueError(
            "fetched key fingerprint mismatch: expected " + fp_wanted[:16]
            + "..., got " + fp_got[:16] + "... — refusing"
        )
    return data


def build_verifier(pem: bytes) -> PackageVerifier:
    """从 PEM 字节构造验签器（程序化注入，不落盘、不走路径）。"""
    verifier = PackageVerifier()
    verifier.set_public_key(load_public_key_from_bytes(pem))
    return verifier


def resolve_marketplace_verify_key(
    root_dir: Path,
    env_value: str = "",
    key_server_url: str = "",
    expected_fp: str = "",
    timeout_s: float = 10.0,
) -> bytes | None:
    """按 D2 优先级解析验签公钥，返回 PEM 字节；全部通道未配置/失败则 None。

    优先级（运维覆盖语义）：环境变量 → 内置 → HTTPS。HTTPS 失败（网络抖动、
    指纹不符、未钉指纹）降级为 None 并告警——不中断消费方启动；要求验签的
    流程（``require_signature=True``）会在安装时 fail-closed，语义自洽。
    """
    # ② 环境变量：路径优先，其次内联 PEM（运维换钥窗口直接贴 PEM）。
    if env_value:
        candidate = Path(env_value)
        if candidate.is_file():
            logger.info("[marketplace] verify key from env path: %s", env_value)
            return candidate.read_bytes()
        if _PEM_HEADER in env_value:
            logger.info("[marketplace] verify key from inline env PEM")
            return env_value.encode("utf-8")
        logger.warning(
            "[marketplace] MAOP_MARKETPLACE_VERIFY_KEY set but not a file or "
            "inline PEM — ignoring: %s",
            env_value[:120],
        )
    # ① 安装包内置：随产品走的仓库/项目根公钥。
    builtin = root_dir / _BUILTIN_RELATIVE
    if builtin.is_file():
        logger.info("[marketplace] verify key from built-in: %s", builtin)
        return builtin.read_bytes()
    # ③ HTTPS 回退通道：URL 与期望指纹都配置才启用（bootstrap 信任钉扎）。
    if key_server_url:
        if not expected_fp:
            logger.warning(
                "[marketplace] key server URL configured without expected "
                "fingerprint — HTTPS channel refused (bootstrap trust)"
            )
            return None
        try:
            pem = fetch_verify_key_https(
                key_server_url, expected_fp, timeout_s=timeout_s
            )
        except (ValueError, OSError, TimeoutError) as exc:
            logger.warning(
                "[marketplace] HTTPS key fetch failed — channel degrades to "
                "no-verifier (signature-required installs fail closed): %s",
                exc,
            )
            return None
        logger.info("[marketplace] verify key fetched via HTTPS (fingerprint OK)")
        return pem
    return None
