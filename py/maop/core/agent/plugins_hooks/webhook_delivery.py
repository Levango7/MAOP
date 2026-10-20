"""HTTP delivery for webhook hooks (SSRF-checked, retry-aware).

Kept separate from ``hook_manager`` so registration/persistence logic stays
within the file-size gate; the delivery concern is independently testable.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def deliver_webhook(
    *,
    url: str,
    hook_id: str,
    headers: dict[str, str] | None,
    timeout_s: float,
    retry_count: int,
    payload: dict[str, Any],
) -> tuple[bool, str, int, str]:
    """POST *payload* to *url*, retrying network errors, 429 and 5xx.

    Returns ``(success, response, response_code, error)`` where
    ``response`` is ``"HTTP <code>"`` when a response arrived (else ``""``)
    and ``response_code`` is the last observed status (0 = no response).
    """
    # SSRF 防御性校验（defense-in-depth）：即使绕过 API 层校验，
    # 在实际发送 HTTP 请求前再次拒绝内网/云元数据端点。
    try:
        from maop.core.security.url_validator import validate_webhook_url
        validate_webhook_url(url)
    except ImportError:
        # url_validator 不可用时 fail-open（不阻塞已有 hook 链）
        logger.warning("[hook] url_validator unavailable, skipping SSRF check for '%s'", hook_id)
    except ValueError as exc:
        logger.warning("[hook] SSRF validation failed for hook '%s' url='%s': %s", hook_id, url, exc)
        return False, "", 0, f"SSRF blocked: {exc}"

    try:
        import httpx
    except ImportError:
        return False, "", 0, "httpx not installed"

    attempts = max(0, int(retry_count)) + 1
    timeout = timeout_s if timeout_s and timeout_s > 0 else 10.0
    last_error = ""
    last_status = 0
    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload, headers=headers or None)
            last_status = resp.status_code
            # 只对网络错误、429 与 5xx 重试；4xx 是接收方明确拒绝，重试无意义。
            if not (last_status == 429 or 500 <= last_status < 600):
                return 200 <= last_status < 300, f"HTTP {last_status}", last_status, ""
            last_error = f"HTTP {last_status}"
        except Exception as exc:
            last_status = 0
            last_error = str(exc)
        if attempt + 1 < attempts:
            await asyncio.sleep(min(0.5, 0.1 * (attempt + 1)))
    return (
        False,
        f"HTTP {last_status}" if last_status else "",
        last_status,
        f"{last_error} (after {attempts} attempts)",
    )
