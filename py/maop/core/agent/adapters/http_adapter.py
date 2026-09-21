"""通用 HTTP API 适配器 — 通过 ``httpx`` 调用外部 AI 服务。

支持 OpenAI 兼容 API（``/v1/chat/completions``）、DeepSeek API、
Ollama API（``/api/generate``）等任何基于 HTTP 的 AI 服务。

特性：
  - **API Key 从环境变量读取**：避免硬编码密钥。
  - **点分响应路径提取**：如 ``"choices.0.message.content"`` 从嵌套
    JSON 中提取目标字段。
  - **自动重试**：请求失败时按 ``max_retries`` 重试。
  - **超时保护**：连接和请求均有超时。
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import httpx
from pydantic import BaseModel, Field

from maop.core.agent.delegation.agent_proxy import AgentAdapter

logger = logging.getLogger(__name__)


class HTTPAdapterConfig(BaseModel):
    """HTTP 适配器配置。

    Parameters
    ----------
    base_url : str
        服务基础 URL（如 ``"https://api.openai.com"``）。
    api_key_env : str
        存放 API Key 的环境变量名（如 ``"OPENAI_API_KEY"``）。
        为空则不添加 Authorization 头。
    headers : dict[str, str]
        额外请求头。
    method : str
        HTTP 方法（默认 ``"POST"``）。
    path : str
        请求路径（追加到 ``base_url`` 之后，如 ``"/v1/chat/completions"``）。
    task_field : str
        请求体中存放任务文本的字段名（默认 ``"prompt"``）。
    extra_body : dict[str, Any]
        额外请求体字段（如 ``{"model": "gpt-4o"}``）。
    response_path : str
        点分路径从响应 JSON 中提取结果（如
        ``"choices.0.message.content"``）。为空则返回整个 JSON。
    timeout_s : float
        请求超时秒数。
    max_retries : int
        最大重试次数（含首次请求）。
    """

    base_url: str = ""
    api_key_env: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    method: str = "POST"
    path: str = ""
    task_field: str = "prompt"
    extra_body: dict[str, Any] = Field(default_factory=dict)
    response_path: str = ""  # 点分路径，如 "choices.0.message.content"
    timeout_s: float = 30.0
    max_retries: int = 3

    model_config = {"extra": "forbid"}


class HTTPAdapter(AgentAdapter):
    """通用 HTTP API 适配器。

    Parameters
    ----------
    config : HTTPAdapterConfig | None
        适配器配置。为 ``None`` 时使用默认配置。
    """

    def __init__(self, config: HTTPAdapterConfig | None = None) -> None:
        self.config: HTTPAdapterConfig = config or HTTPAdapterConfig()
        self._client: httpx.Client | None = None
        self._connected: bool = False

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _build_headers(self) -> dict[str, str]:
        """构建请求头，注入 API Key。"""
        headers = dict(self.config.headers)
        if self.config.api_key_env:
            api_key = os.environ.get(self.config.api_key_env, "")
            if api_key:
                headers.setdefault("Authorization", f"Bearer {api_key}")
        return headers

    def _build_url(self) -> str:
        """构建完整请求 URL。"""
        base = self.config.base_url.rstrip("/")
        path = self.config.path.lstrip("/")
        if path:
            return f"{base}/{path}"
        return base

    def _build_body(self, task: str, **kwargs: Any) -> dict[str, Any]:
        """构建请求体。"""
        body = dict(self.config.extra_body)
        body[self.config.task_field] = task
        body.update(kwargs)
        return body

    def _extract_response(self, data: Any) -> str:
        """按 ``response_path`` 点分路径从响应中提取结果。

        支持字典键和列表索引（用数字字符串表示索引）。
        """
        if not self.config.response_path:
            if isinstance(data, str):
                return data
            return json.dumps(data, ensure_ascii=False)
        current: Any = data
        for key in self.config.response_path.split("."):
            if isinstance(current, list):
                current = current[int(key)]
            elif isinstance(current, dict):
                current = current[key]
            else:
                raise KeyError(
                    f"Cannot traverse '{key}' on {type(current).__name__}"
                )
        return str(current)

    # ------------------------------------------------------------------
    # AgentAdapter 实现
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """创建 ``httpx.Client`` 并验证服务可达性。"""
        try:
            self._client = httpx.Client(
                base_url=self.config.base_url,
                timeout=self.config.timeout_s,
                headers=self._build_headers(),
            )
            # 发送轻量请求验证可达性（容忍 404，拒绝连接错误）
            # /health 不存在但服务可达时可能返回 404，
            # 只有连接级别错误（ConnectError）才算不可达——直接向上传播。
            # TRY203：原 try/except 仅做裸 raise，等价于不捕获。
            self._client.get("/health", timeout=5.0)
            self._connected = True
            logger.info("[http_adapter] Connected to %s", self.config.base_url)
            return True
        except Exception as exc:
            logger.error("[http_adapter] Connect failed: %s", exc)
            self._connected = False
            if self._client is not None:
                self._client.close()
                self._client = None
            return False

    def execute(self, task: str, **kwargs: Any) -> str:
        """发送 HTTP 请求并返回提取后的响应字符串。

        请求失败时按 ``max_retries`` 重试，全部失败后抛出
        ``RuntimeError``。
        """
        if (not self._connected or self._client is None) and not self.connect():
            raise RuntimeError("HTTPAdapter not connected — connect() failed")

        url = self._build_url()
        body = self._build_body(task, **kwargs)
        last_exc: Exception | None = None

        for attempt in range(1, self.config.max_retries + 1):
            try:
                resp = self._client.request(
                    self.config.method,
                    url,
                    json=body,
                    timeout=self.config.timeout_s,
                )
                resp.raise_for_status()
                data = resp.json()
                return self._extract_response(data)
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "[http_adapter] Attempt %d/%d failed: %s",
                    attempt,
                    self.config.max_retries,
                    exc,
                )
                if attempt < self.config.max_retries:
                    time.sleep(0.5 * attempt)  # 简单退避

        raise RuntimeError(
            f"HTTPAdapter failed after {self.config.max_retries} retries: "
            f"{last_exc}"
        )

    def health_check(self) -> bool:
        """检查服务是否可达（GET ``base_url/health`` 返回 < 500）。"""
        if self._client is None:
            return False
        try:
            resp = self._client.get("/health", timeout=5.0)
            return resp.status_code < 500
        except Exception:
            return False

    def sync_config(self, config: dict[str, Any]) -> None:
        """用新配置替换当前配置并重连。"""
        was_connected = self._connected
        self.disconnect()
        self.config = HTTPAdapterConfig(**config)
        if was_connected:
            self.connect()

    def disconnect(self) -> None:
        """关闭 HTTP 客户端并断开连接。"""
        if self._client is not None:
            self._client.close()
            self._client = None
        self._connected = False