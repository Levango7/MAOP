"""Tests for maop.core.security.url_validator — SSRF 防护校验.

覆盖：
  - 合法 URL（http/https + 公网 IP/域名）通过
  - 非法 scheme（ftp、file、gopher、空）拒绝
  - 缺少 hostname 拒绝
  - IPv4 内网/回环/链路本地/多播/未指定/保留 拒绝
  - IPv6 内网/回环/链路本地 拒绝
  - IPv4 映射的 IPv6 回环地址 拒绝
  - 已知内网域名别名（localhost、metadata.google.internal 等）拒绝
  - 公网域名通过
  - 空 URL / 非 str 拒绝
"""
from __future__ import annotations

import pytest

from maop.core.security.url_validator import SSRFError, validate_webhook_url


class TestValidUrls:
    """合法 URL 应通过校验。"""

    @pytest.mark.parametrize("url", [
        "http://example.com/hook",
        "https://example.com/hook",
        "https://api.example.com:8443/webhook",
        "http://93.184.216.34/hook",          # 公网 IPv4（example.com 真实 IP）
        "https://2606:2800:220:1:248:1893:25c8:194/hook",  # 公网 IPv6
        "http://sub.domain.example.org/path?query=1",
    ])
    def test_valid_url_passes(self, url):
        # 不抛异常即通过
        validate_webhook_url(url)

    def test_http_scheme_allowed(self):
        validate_webhook_url("http://public.example.com/hook")

    def test_https_scheme_allowed(self):
        validate_webhook_url("https://public.example.com/hook")


class TestInvalidScheme:
    """非法 scheme 应拒绝。"""

    @pytest.mark.parametrize("url", [
        "ftp://example.com/hook",
        "file:///etc/passwd",
        "gopher://127.0.0.1/",
        "dict://127.0.0.1:6379/",
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "ws://example.com/ws",
        "wss://example.com/ws",
    ])
    def test_invalid_scheme_rejected(self, url):
        with pytest.raises(SSRFError, match="scheme"):
            validate_webhook_url(url)

    def test_empty_scheme_rejected(self):
        with pytest.raises(SSRFError, match="scheme"):
            validate_webhook_url("//example.com/hook")


class TestMissingHostname:
    """缺少 hostname 应拒绝。"""

    @pytest.mark.parametrize("url", [
        "http://",
        "https:///path",
        "http:///path",
    ])
    def test_missing_hostname_rejected(self, url):
        with pytest.raises(SSRFError, match="hostname"):
            validate_webhook_url(url)


class TestIPv4InternalAddresses:
    """IPv4 内网/特殊地址应拒绝。"""

    @pytest.mark.parametrize("url,kind", [
        ("http://127.0.0.1/hook", "Loopback"),
        ("http://127.1.2.3/hook", "Loopback"),
        ("http://127.255.255.255/hook", "Loopback"),
        ("http://10.0.0.1/hook", "Private"),
        ("http://10.255.255.255/hook", "Private"),
        ("http://172.16.0.1/hook", "Private"),
        ("http://172.31.255.255/hook", "Private"),
        ("http://192.168.0.1/hook", "Private"),
        ("http://192.168.1.100/hook", "Private"),
        ("http://169.254.169.254/hook", "Link-local"),  # 云元数据端点
        ("http://169.254.1.1/hook", "Link-local"),
        ("http://224.0.0.1/hook", "Multicast"),
        ("http://239.255.255.255/hook", "Multicast"),
        ("http://0.0.0.0/hook", "Unspecified"),
        ("http://240.0.0.1/hook", "Reserved"),
        ("http://255.255.255.255/hook", "Reserved"),  # broadcast
    ])
    def test_internal_ipv4_rejected(self, url, kind):
        with pytest.raises(SSRFError, match=kind):
            validate_webhook_url(url)


class TestIPv6InternalAddresses:
    """IPv6 内网/特殊地址应拒绝。"""

    @pytest.mark.parametrize("url,kind", [
        ("http://[::1]/hook", "Loopback"),
        ("http://[fc00::1]/hook", "Private"),       # ULA
        ("http://[fd00::1]/hook", "Private"),       # ULA
        ("http://[fe80::1]/hook", "Link-local"),
        ("http://[ff02::1]/hook", "Multicast"),
        ("http://[::]/hook", "Unspecified"),
    ])
    def test_internal_ipv6_rejected(self, url, kind):
        with pytest.raises(SSRFError, match=kind):
            validate_webhook_url(url)

    def test_ipv4_mapped_ipv6_loopback_rejected(self):
        """IPv4 映射的 IPv6 回环地址应拒绝。"""
        with pytest.raises(SSRFError, match="Loopback"):
            validate_webhook_url("http://[::ffff:127.0.0.1]/hook")

    def test_ipv4_mapped_ipv6_private_rejected(self):
        """IPv4 映射的 IPv6 私有地址应拒绝。"""
        with pytest.raises(SSRFError, match="Private"):
            validate_webhook_url("http://[::ffff:10.0.0.1]/hook")


class TestInternalHostnames:
    """已知内网域名别名应拒绝。"""

    @pytest.mark.parametrize("hostname", [
        "localhost",
        "metadata.google.internal",
        "metadata",
        "instance-data",
    ])
    def test_internal_hostname_rejected(self, hostname):
        with pytest.raises(SSRFError, match="Internal hostname"):
            validate_webhook_url(f"http://{hostname}/hook")

    def test_internal_hostname_case_insensitive(self):
        """内网域名匹配应大小写不敏感。"""
        with pytest.raises(SSRFError, match="Internal hostname"):
            validate_webhook_url("http://LOCALHOST/hook")


class TestPublicDomainPasses:
    """公网域名应通过。"""

    @pytest.mark.parametrize("hostname", [
        "example.com",
        "api.github.com",
        "hooks.slack.com",
        "discord.com",
        "sub.domain.example.org",
    ])
    def test_public_domain_passes(self, hostname):
        validate_webhook_url(f"https://{hostname}/webhook")


class TestEmptyAndInvalidInput:
    """空 URL / 非 str 应拒绝。"""

    def test_empty_string_rejected(self):
        with pytest.raises(SSRFError, match="non-empty"):
            validate_webhook_url("")

    def test_non_string_rejected(self):
        with pytest.raises(SSRFError, match="non-empty"):
            validate_webhook_url(None)  # type: ignore[arg-type]

    def test_whitespace_only_rejected(self):
        with pytest.raises(SSRFError):
            validate_webhook_url("   ")


class TestSSRFErrorSubclass:
    """SSRFError 应为 ValueError 子类。"""

    def test_ssrf_error_is_value_error(self):
        assert issubclass(SSRFError, ValueError)

    def test_ssrf_error_message(self):
        try:
            validate_webhook_url("http://127.0.0.1/hook")
        except SSRFError as exc:
            assert "127.0.0.1" in str(exc)
            assert "Loopback" in str(exc)