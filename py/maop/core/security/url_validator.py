"""URL 安全校验工具 — 防御 SSRF（Server-Side Request Forgery）.

提供 ``validate_webhook_url`` 函数，在 Webhook URL 写入持久化层和发送
HTTP 请求前进行双重校验（defense-in-depth），拒绝指向内网/云元数据端点
的请求。

校验规则：
  1. Scheme 必须为 ``http`` 或 ``https``
  2. Host 必须存在
  3. 若 Host 为 IP 地址：
       - 拒绝 loopback（127.0.0.0/8、::1）
       - 拒绝 private（10.x、172.16-31.x、192.168.x、fc00::/7）
       - 拒绝 link-local（169.254.0.0/16、fe80::/10，含云元数据端点）
       - 拒绝 multicast
       - 拒绝 unspecified（0.0.0.0、::）
  4. 若 Host 为域名：
       - 拒绝常见内网别名（localhost、metadata.google.internal 等）
       - 当 ``check_dns=True`` 时，解析域名并对所有 A/AAAA 记录执行
         与 IP 地址相同的校验（防止 DNS rebinding 到内网地址）

注意：
  - 域名形式的主机名无法穷举所有内网域名，本函数默认仅拦截已知别名；
    更严格的部署可启用 ``check_dns=True`` 在 DNS 解析后对解析结果再次校验。
  - DNS 解析会增加网络 I/O 延迟（通常 1-100ms），默认禁用；仅在写入
    持久化层等非热路径上启用。
  - IPv4 映射的 IPv6 地址（如 ::ffff:127.0.0.1）由 ``ipaddress`` 模块
    自动归一化，loopback/private 等属性会正确反映其 IPv4 部分。
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class SSRFError(ValueError):
    """Webhook URL failed SSRF validation."""


# 已知内网/元数据域名别名（小写匹配）。
# - localhost: 本地回环别名
# - metadata.google.internal: GCE 元数据端点
# - metadata: Azure IMDS 短别名（部分环境）
# - instance-data: AWS EC2 用户数据别名
_INTERNAL_HOSTNAMES: frozenset[str] = frozenset({
    "localhost",
    "metadata.google.internal",
    "metadata",
    "instance-data",
})


def _check_ip_address(hostname: str, ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    """Raise SSRFError if the given IP address is internal/unsafe.

    Shared by the direct-IP path and the DNS-resolution path so that a
    domain resolving to 127.0.0.1 is rejected with the same message as
    a literal ``http://127.0.0.1`` URL.

    IPv4-mapped IPv6（::ffff:x.x.x.x）：按映射的 IPv4 部分判断，
    否则 ::ffff:127.0.0.1 的 IPv6 层面 is_loopback=False、
    is_reserved=True → 误报 Reserved（测试期望 Loopback/Private）。
    """
    mapped4 = ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) else None
    check = mapped4 if mapped4 is not None else ip
    # Reject loopback (127.0.0.0/8, ::1)
    if check.is_loopback:
        raise SSRFError(f"Loopback address not allowed: {hostname}")
    # Reject link-local (169.254.0.0/16, fe80::/10) - includes cloud metadata
    # Must check before is_private (Python treats link-local as private too)
    if check.is_link_local:
        raise SSRFError(f"Link-local address not allowed: {hostname}")
    # Reject unspecified (0.0.0.0, ::)
    # Must check before is_private (Python treats unspecified as private too)
    if check.is_unspecified:
        raise SSRFError(f"Unspecified address not allowed: {hostname}")
    # Reject multicast
    if check.is_multicast:
        raise SSRFError(f"Multicast address not allowed: {hostname}")
    # Reject reserved (240.0.0.0/4, 255.255.255.255/32 etc.)
    # Must check before is_private (Python treats some reserved as private too)
    if check.is_reserved:
        raise SSRFError(f"Reserved address not allowed: {hostname}")
    # Reject private (10.x, 172.16-31.x, 192.168.x, fc00::/7)
    # Checked last as a catch-all, since Python's is_private overlaps with
    # link-local, unspecified, and reserved ranges.
    if check.is_private:
        raise SSRFError(f"Private address not allowed: {hostname}")


def validate_webhook_url(url: str, *, check_dns: bool = False) -> None:
    """Validate that a webhook URL is safe from SSRF.

    Checks:
      1. Scheme must be http or https
      2. Host must not be an internal/private/loopback/link-local address
      3. Host must not be a cloud metadata endpoint
      4. If ``check_dns=True`` and the host is a domain name, resolve it
         via ``socket.getaddrinfo`` and apply the same IP checks to every
         resolved address. This catches DNS rebinding attacks where a
         domain initially resolves to a public IP but later resolves to
         an internal one.

    Args:
        url: The webhook URL to validate.
        check_dns: If True, perform DNS resolution for domain hosts and
            reject if any A/AAAA record points to an internal address.
            Default False because DNS resolution adds network latency
            (1-100ms) and external dependency; enable only on
            non-hot paths (e.g. webhook URL persistence, not per-request).

    Raises:
        SSRFError: If the URL fails any safety check.
    """
    if not isinstance(url, str) or not url:
        raise SSRFError("URL must be a non-empty string")

    parsed = urlparse(url)

    # 1. Scheme check
    if parsed.scheme not in ("http", "https"):
        raise SSRFError(f"URL scheme must be http or https, got: {parsed.scheme!r}")

    if not parsed.hostname:
        raise SSRFError("URL must have a hostname")

    hostname = parsed.hostname

    # 2. Check if hostname is an IP address
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        # hostname is a domain name, not an IP
        # Check for common internal hostnames
        if hostname.lower() in _INTERNAL_HOSTNAMES:
            raise SSRFError(f"Internal hostname not allowed: {hostname}") from None
        # t88-L4: optional DNS resolution to catch domains that resolve to
        # internal/private IPs (DNS rebinding defense). Disabled by default
        # because it adds network latency and a DNS dependency.
        if check_dns:
            _validate_dns_resolution(hostname)
    else:
        _check_ip_address(hostname, ip)

    # 3. Cloud metadata endpoint check
    #    (169.254.169.254 is link-local, already caught above)


def _validate_dns_resolution(hostname: str) -> None:
    """Resolve ``hostname`` and reject if any address is internal/unsafe.

    Uses ``socket.getaddrinfo`` to fetch both A and AAAA records. Each
    resolved address is run through ``_check_ip_address`` so the same
    loopback/private/link-local rules apply to DNS results as to literal
    IPs.

    A DNS resolution failure (NXDOMAIN, timeout, etc.) is logged at
    warning level but does NOT raise — the caller may still want to
    accept the URL on a best-effort basis, and a non-resolvable domain
    is not itself an SSRF vector (the subsequent HTTP request will fail
    naturally). Strict callers can wrap this in their own logic.
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        logger.warning(
            "[url_validator] DNS resolution failed for %r (accepting on "
            "best-effort basis, subsequent HTTP request will fail if the "
            "domain truly does not resolve): %s",
            hostname, exc,
        )
        return

    seen: set[str] = set()
    for family, _type, _proto, _canon, sockaddr in infos:
        # sockaddr is (host, port) for IPv4 or (host, port, flow, scope) for IPv6
        addr_str = sockaddr[0]
        if addr_str in seen:
            continue
        seen.add(addr_str)
        try:
            ip = ipaddress.ip_address(addr_str)
        except ValueError:
            # Should not happen for getaddrinfo results, but be defensive.
            continue
        # Reuse the same IP check; raises SSRFError on violation.
        _check_ip_address(hostname, ip)


__all__ = ["SSRFError", "validate_webhook_url"]