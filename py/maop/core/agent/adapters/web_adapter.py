"""Web 端适配器 — 对接只有 Web 界面、无公开 API 的 Agent。

通过 ``httpx`` 模拟浏览器 HTTP 请求（带 Cookie / Token / Session 认证）
调用目标 Web 接口，适用于仅有 Web UI 的遗留系统或内部工具。

认证方式：
  - ``cookie``：将凭证放入 ``Cookie`` 请求头。
  - ``token``：将凭证作为 ``Authorization: Bearer <token>``。
  - ``session``：将凭证放入 ``X-Session-Id`` 请求头。

请求模板 ``request_template`` 支持 ``{task}`` 占位符，在执行时替换为
实际任务文本。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx
from pydantic import BaseModel, Field

from maop.core.agent.delegation.agent_proxy import AgentAdapter

logger = logging.getLogger(__name__)


class WebAdapterConfig(BaseModel):
    """Web 适配器配置。

    Parameters
    ----------
    login_url : str
        登录页面 URL（用于验证可达性，不实际执行登录流程）。
    api_url : str
        API 接口 URL（执行任务的目标端点）。
    auth_method : str
        认证方式：``"cookie"``、``"token"`` 或 ``"session"``。
    auth_credentials_ref : str
        存放认证凭证的环境变量名。
    request_template : dict[str, Any]
        请求体模板。支持 ``{task}`` 占位符，在执行时替换为任务文本。
    response_path : str
        点分路径从响应 JSON 中提取结果。
    timeout_s : float
        请求超时秒数。
    headers : dict[str, str]
        额外请求头。
    """

    login_url: str = ""
    api_url: str = ""
    auth_method: str = "token"  # cookie | token | session
    auth_credentials_ref: str = ""  # 环境变量名
    request_template: dict[str, Any] = Field(default_factory=dict)
    response_path: str = ""
    timeout_s: float = 30.0
    headers: dict[str, str] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class WebAdapter(AgentAdapter):
    """Web 端适配器 — 模拟 HTTP 请求调用 Web 接口。

    Parameters
    ----------
    config : WebAdapterConfig | None
        适配器配置。
    """

    def __init__(self, config: WebAdapterConfig | None = None) -> None:
        self.config: WebAdapterConfig = config or WebAdapterConfig()
        self._client: httpx.Client | None = None
        self._connected: bool = False
        self._auth_value: str = ""

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _load_credentials(self) -> str:
        """从环境变量加载认证凭证。"""
        if not self.config.auth_credentials_ref:
            return ""
        return os.environ.get(self.config.auth_credentials_ref, "")

    def _build_headers(self) -> dict[str, str]:
        """构建请求头，注入认证凭证。"""
        headers = dict(self.config.headers)
        auth = self._auth_value or self._load_credentials()
        if not auth:
            return headers
        if self.config.auth_method == "cookie":
            headers["Cookie"] = auth
        elif self.config.auth_method == "token":
            headers["Authorization"] = f"Bearer {auth}"
        elif self.config.auth_method == "session":
            headers["X-Session-Id"] = auth
        return headers

    def _build_body(self, task: str) -> dict[str, Any]:
        """构建请求体，替换 ``{task}`` 占位符。"""
        body: dict[str, Any] = {}
        for key, val in self.config.request_template.items():
            if isinstance(val, str) and "{task}" in val:
                body[key] = val.replace("{task}", task)
            else:
                body[key] = val
        body.setdefault("task", task)
        return body

    def _extract_response(self, data: Any) -> str:
        """按 ``response_path`` 点分路径从响应中提取结果。"""
        if not self.config.response_path:
            if isinstance(data, str):
                return data
            return json.dumps(data, ensure_ascii=False)
        current: Any = data
        for key in self.config.response_path.split("."):
            if isinstance(current, list):
                # 修复: 捕获 ValueError（key 非整数）和 IndexError（索引越界），抛出清晰配置错误
                try:
                    current = current[int(key)]
                except ValueError:
                    raise ValueError(
                        f"response_path 配置错误: 路径段 '{key}' 不是有效整数索引，"
                        f"无法在 list 上遍历 (response_path='{self.config.response_path}')"
                    ) from None
                except IndexError:
                    raise IndexError(
                        f"response_path 配置错误: 索引 '{key}' 越界，"
                        f"列表长度为 {len(current)} "
                        f"(response_path='{self.config.response_path}')"
                    ) from None
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
        """创建 HTTP 客户端并验证 API URL 可达。"""
        try:
            self._auth_value = self._load_credentials()
            self._client = httpx.Client(
                timeout=self.config.timeout_s,
                headers=self._build_headers(),
            )
            # 验证 API URL 可达（容忍非 2xx，拒绝连接错误）
            # TRY203：原 try/except 仅做裸 raise，等价于不捕获。
            self._client.get(self.config.api_url, timeout=5.0)
            self._connected = True
            logger.info("[web_adapter] Connected to %s", self.config.api_url)
            return True
        except Exception as exc:
            logger.error("[web_adapter] Connect failed: %s", exc)
            self._connected = False
            if self._client is not None:
                self._client.close()
                self._client = None
            return False

    def execute(self, task: str, **kwargs: Any) -> str:
        """发送 HTTP POST 请求并返回提取后的响应字符串。"""
        if (not self._connected or self._client is None) and not self.connect():
            raise RuntimeError("WebAdapter not connected — connect() failed")

        body = self._build_body(task)
        body.update(kwargs)

        try:
            resp = self._client.post(
                self.config.api_url,
                json=body,
                timeout=self.config.timeout_s,
            )
            resp.raise_for_status()
            data = resp.json()
            return self._extract_response(data)
        except Exception as exc:
            raise RuntimeError(f"WebAdapter request failed: {exc}") from exc

    def health_check(self) -> bool:
        """检查 API URL 是否可达（GET 返回 < 500）。"""
        if self._client is None:
            return False
        try:
            resp = self._client.get(self.config.api_url, timeout=5.0)
            return resp.status_code < 500
        except Exception:
            return False

    def sync_config(self, config: dict[str, Any]) -> None:
        """用新配置替换当前配置并重连。"""
        was_connected = self._connected
        self.disconnect()
        self.config = WebAdapterConfig(**config)
        if was_connected:
            self.connect()

    def disconnect(self) -> None:
        """关闭 HTTP 客户端并断开连接。"""
        if self._client is not None:
            self._client.close()
            self._client = None
        self._connected = False
        self._auth_value = ""