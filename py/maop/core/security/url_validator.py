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

注意：
  - 域名形式的主机名无法穷举所有内网域名，本函数仅拦截已知别名；
    更严格的部署应在 DNS 解析后对解析结果再次校验（本函数不解析 DNS）。
  - IPv4 映射的 IPv6 地址（如 ::ffff:127.0.0.1）由 ``ipaddress`` 模块
    自动归一化，loopback/private 等属性会正确反映其 IPv4 部分。
"""

from __future__ import annotations

import ipaddress
import logging
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


def validate_webhook_url(url: str) -> None:
    """Validate that a webhook URL is safe from SSRF.

    Checks:
      1. Scheme must be http or https
      2. Host must not be an internal/private/loopback/link-local address
      3. Host must not be a cloud metadata endpoint

    Args:
        url: The webhook URL to validate.

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
    else:
        # Reject loopback (127.0.0.0/8, ::1)
        if ip.is_loopback:
            raise SSRFError(f"Loopback address not allowed: {hostname}")
        # Reject link-local (169.254.0.0/16, fe80::/10) - includes cloud metadata
        # Must check before is_private (Python treats link-local as private too)
        if ip.is_link_local:
            raise SSRFError(f"Link-local address not allowed: {hostname}")
        # Reject unspecified (0.0.0.0, ::)
        # Must check before is_private (Python treats unspecified as private too)
        if ip.is_unspecified:
            raise SSRFError(f"Unspecified address not allowed: {hostname}")
        # Reject multicast
        if ip.is_multicast:
            raise SSRFError(f"Multicast address not allowed: {hostname}")
        # Reject reserved (240.0.0.0/4, 255.255.255.255/32 etc.)
        # Must check before is_private (Python treats some reserved as private too)
        if ip.is_reserved:
            raise SSRFError(f"Reserved address not allowed: {hostname}")
        # Reject private (10.x, 172.16-31.x, 192.168.x, fc00::/7)
        # Checked last as a catch-all, since Python's is_private overlaps with
        # link-local, unspecified, and reserved ranges.
        if ip.is_private:
            raise SSRFError(f"Private address not allowed: {hostname}")

    # 3. Cloud metadata endpoint check
    #    (169.254.169.254 is link-local, already caught above)


__all__ = ["SSRFError", "validate_webhook_url"]