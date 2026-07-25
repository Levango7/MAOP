"""MAOP TLS - SSL/TLS context management for HTTPS support.

Provides:
  1. create_ssl_context: Build ssl.SSLContext from cert/key paths
  2. generate_self_signed: Dev-only self-signed cert generation
  3. TLSSettings: Pydantic model for TLS configuration
"""

from __future__ import annotations

import logging
import ssl
import subprocess
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class TLSSettings(BaseModel):
    """TLS/HTTPS configuration."""
    enabled: bool = False
    cert_file: str = ""          # Path to PEM certificate file
    key_file: str = ""           # Path to PEM private key file
    ca_file: str = ""            # Optional CA bundle for client verification
    min_version: str = "TLSv1_2"  # ssl.TLSVersion name
    verify_client: bool = False   # Require client certificate


def create_ssl_context(settings: TLSSettings) -> ssl.SSLContext | None:
    """Create an ssl.SSLContext from TLSSettings.

    Returns None if TLS is not enabled.
    Raises FileNotFoundError if cert/key files are missing.
    """
    if not settings.enabled:
        return None

    # Determine minimum TLS version
    version_map = {
        "TLSv1_2": ssl.TLSVersion.TLSv1_2,
        "TLSv1_3": ssl.TLSVersion.TLSv1_3,
    }
    # Deprecated versions — only available with explicit opt-in and a warning
    if settings.min_version in ("TLSv1", "TLSv1_1"):
        logger.warning(
            "[tls] %s is deprecated and insecure. Use TLSv1_2 or TLSv1_3 instead.",
            settings.min_version,
        )
        _deprecated_map = {
            "TLSv1": ssl.TLSVersion.TLSv1,
            "TLSv1_1": ssl.TLSVersion.TLSv1_1,
        }
        version_map.update(_deprecated_map)
    min_ver = version_map.get(settings.min_version, ssl.TLSVersion.TLSv1_2)

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = min_ver
    ctx.options |= ssl.OP_NO_SSLv2 | ssl.OP_NO_SSLv3
    ctx.options |= ssl.OP_NO_COMPRESSION

    # Load cert and key
    cert_path = Path(settings.cert_file)
    key_path = Path(settings.key_file)
    if not cert_path.is_file():
        raise FileNotFoundError(f"TLS cert file not found: {cert_path}")
    if not key_path.is_file():
        raise FileNotFoundError(f"TLS key file not found: {key_path}")

    try:
        first_line = cert_path.read_text(encoding="utf-8").split("\n")[0].strip()
        if first_line.startswith("#") or "placeholder" in first_line.lower():
            raise ValueError(f"TLS cert file is a placeholder, not a real certificate: {cert_path}")
    except ValueError:
        raise
    except Exception:
        pass

    ctx.load_cert_chain(str(cert_path), str(key_path))

    # Optional CA for client verification
    if settings.verify_client:
        if settings.ca_file and Path(settings.ca_file).is_file():
            ctx.load_verify_locations(str(settings.ca_file))
            ctx.verify_mode = ssl.CERT_REQUIRED
        else:
            ctx.verify_mode = ssl.CERT_OPTIONAL
    else:
        ctx.verify_mode = ssl.CERT_NONE

    logger.info("TLS enabled: min_version=%s, verify_client=%s", settings.min_version, settings.verify_client)
    return ctx


def generate_self_signed(
    output_dir: str | Path,
    *,
    common_name: str = "MAOP Dashboard",
    days: int = 365,
    overwrite: bool = False,
) -> tuple[Path, Path]:
    """Generate a self-signed certificate for development.

    Returns (cert_path, key_path).
    Uses openssl if available, otherwise creates a minimal cert via Python.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    cert_path = out / "MAOP-dev.crt"
    key_path = out / "MAOP-dev.key"

    if cert_path.exists() and key_path.exists() and not overwrite:
        logger.info("Self-signed cert already exists, skipping generation")
        return cert_path, key_path

    # Try openssl first
    try:
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-keyout", str(key_path), "-out", str(cert_path),
                "-days", str(days), "-nodes",
                "-subj", f"/CN={common_name}/O=MAOP",
            ],
            capture_output=True, check=True, timeout=30,
        )
        logger.info("Self-signed cert generated via openssl: %s", cert_path)
        return cert_path, key_path
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: write a note that openssl is not available
    # M1 fix (Phase R5): 不再创建占位符文件，直接抛错。
    # 占位符不是有效证书，会导致 TLS 启动失败但错误信息不明确。
    # 要求用户安装 OpenSSL 或提供真实证书。
    raise RuntimeError(
        "openssl not available; cannot generate self-signed certificate. "
        "Please install OpenSSL or provide real certificate files via "
        "MAOP_TLS_CERT / MAOP_TLS_KEY environment variables."
    )
