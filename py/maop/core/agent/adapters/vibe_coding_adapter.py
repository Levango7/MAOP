"""Vibe Coding 平台适配器 — 对接 Bolt.new / v0 / Lovable / Replit Agent 等
基于 Web 的项目生成平台。

这类平台通过 Web 界面接收自然语言描述，生成完整项目（而非代码片段），
并支持导出为 zip / git / 文件列表。本适配器继承 :class:`WebAdapter`，
复用其 HTTP 通信能力，在此基础上增加项目生成、状态轮询、导出与下载逻辑。

支持的平台：
  - ``bolt-new`` — Bolt.new
  - ``v0`` — v0.dev
  - ``lovable`` — Lovable
  - ``replit-agent`` — Replit Agent

生成状态流转：``queued`` → ``generating`` → ``completed`` / ``failed``

线程安全：所有公共方法通过可重入锁保护共享状态；项目生成轮询期间
在 sleep 时释放锁，允许其他线程执行健康检查等只读操作。
"""
from __future__ import annotations

import logging
import threading
from time import monotonic, sleep
from typing import Any

import httpx
from pydantic import BaseModel, field_validator

from maop.core.agent.adapters.web_adapter import WebAdapter, WebAdapterConfig

logger = logging.getLogger(__name__)

# 各平台默认 URL
_PLATFORM_DEFAULT_URLS: dict[str, str] = {
    "bolt-new": "https://bolt.new",
    "v0": "https://v0.dev",
    "lovable": "https://lovable.dev",
    "replit-agent": "https://replit.com",
}

# 合法导出格式
_VALID_EXPORT_FORMATS: frozenset[str] = frozenset({"zip", "git", "files"})

# 合法生成状态
_VALID_STATUSES: frozenset[str] = frozenset(
    {"queued", "generating", "completed", "failed"}
)


class VibeCodingConfig(BaseModel):
    """Vibe Coding 平台适配器配置。

    Parameters
    ----------
    platform : str
        平台名：``"bolt-new"`` / ``"v0"`` / ``"lovable"`` / ``"replit-agent"``。
    base_url : str
        平台 URL。留空时按 ``platform`` 自动填充默认值。
    api_key : str
        API 密钥（部分平台需要，注入 ``X-API-Key`` 头）。
    auth_token : str
        OAuth token（注入 ``Authorization: Bearer`` 头）。
    project_template : str
        项目模板，如 ``"nextjs"`` / ``"react"`` / ``"vue"``。
    export_format : str
        导出格式：``"zip"`` / ``"git"`` / ``"files"``。
    max_generation_time_s : float
        最大生成时间（秒），轮询超时保护上限。
    poll_interval_s : float
        轮询间隔（秒）。
    """

    platform: str
    base_url: str = ""
    api_key: str = ""
    auth_token: str = ""
    project_template: str = ""
    export_format: str = "zip"
    max_generation_time_s: float = 300.0
    poll_interval_s: float = 5.0

    model_config = {"extra": "forbid"}

    @field_validator("export_format")
    @classmethod
    def _validate_export_format(cls, v: str) -> str:
        """验证导出格式合法。"""
        if v not in _VALID_EXPORT_FORMATS:
            raise ValueError(
                f"不支持的导出格式: {v}（合法: {sorted(_VALID_EXPORT_FORMATS)}）"
            )
        return v

    @field_validator("max_generation_time_s", "poll_interval_s")
    @classmethod
    def _validate_positive(cls, v: float) -> float:
        """验证时间参数为正数。"""
        if v <= 0:
            raise ValueError(f"时间参数必须为正数，收到: {v}")
        return v


class VibeCodingAdapter(WebAdapter):
    """Vibe Coding 平台适配器 — 对接项目生成型 Web 平台。

    继承 :class:`WebAdapter` 复用 HTTP 通信逻辑，增加项目生成、
    状态轮询、导出与下载能力。所有方法线程安全。

    Parameters
    ----------
    config : VibeCodingConfig
        Vibe Coding 平台配置。
    """

    def __init__(self, config: VibeCodingConfig) -> None:
        # 填充默认 URL（不修改原始配置对象）
        if not config.base_url:
            config = config.model_copy(
                update={
                    "base_url": _PLATFORM_DEFAULT_URLS.get(config.platform, "")
                }
            )
        # 构建父类 WebAdapterConfig 用于 HTTP 通信
        web_config = self._build_web_config(config)
        super().__init__(web_config)
        # 保存 Vibe Coding 专属配置
        self.vibe_config: VibeCodingConfig = config
        # 线程安全锁（可重入，允许同线程嵌套调用）
        self._lock = threading.RLock()
        # 注入 auth_token 到父类，供 _build_headers 使用
        self._auth_value = config.auth_token

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _build_web_config(vibe: VibeCodingConfig) -> WebAdapterConfig:
        """将 :class:`VibeCodingConfig` 转换为父类 :class:`WebAdapterConfig`。"""
        headers: dict[str, str] = {}
        if vibe.api_key:
            headers["X-API-Key"] = vibe.api_key
        return WebAdapterConfig(
            api_url=vibe.base_url,
            auth_method="token",
            timeout_s=vibe.max_generation_time_s,
            headers=headers,
        )

    def _api_base(self) -> str:
        """返回平台 API 基础 URL（去除尾部斜杠）。"""
        return self.vibe_config.base_url.rstrip("/")

    def _generate_url(self) -> str:
        """项目生成端点 URL。"""
        return f"{self._api_base()}/api/projects"

    def _status_url(self, project_id: str) -> str:
        """状态轮询端点 URL。"""
        return f"{self._api_base()}/api/projects/{project_id}/status"

    def _export_url(self, project_id: str) -> str:
        """项目导出端点 URL。"""
        return f"{self._api_base()}/api/projects/{project_id}/export"

    # ------------------------------------------------------------------
    # AgentAdapter 实现
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """验证平台可达性并创建 HTTP 客户端。

        创建 ``httpx.Client`` 并对 ``base_url`` 发起 GET 请求验证可达。
        连接错误返回 ``False``，成功返回 ``True``。
        """
        with self._lock:
            if not self.vibe_config.base_url:
                logger.error("[vibe_coding] base_url 为空，无法连接")
                return False
            try:
                self._client = httpx.Client(
                    timeout=self.vibe_config.max_generation_time_s,
                    headers=self._build_headers(),
                )
                # 验证平台可达（容忍非 2xx，拒绝连接错误）
                try:
                    self._client.get(self.vibe_config.base_url, timeout=5.0)
                except httpx.ConnectError:
                    raise
                self._connected = True
                logger.info(
                    "[vibe_coding] 已连接到 %s (%s)",
                    self.vibe_config.base_url,
                    self.vibe_config.platform,
                )
                return True
            except Exception as exc:
                logger.error("[vibe_coding] 连接失败: %s", exc)
                self._connected = False
                if self._client is not None:
                    self._client.close()
                    self._client = None
                return False

    def execute(self, task: str, **kwargs: Any) -> str:
        """生成项目 — 提交描述并轮询直到完成，返回项目 URL/ID。

        ``kwargs`` 可传入 ``template`` 覆盖配置中的项目模板。
        """
        with self._lock:
            if not self._connected or self._client is None:
                if not self.connect():
                    raise RuntimeError(
                        "VibeCodingAdapter 未连接 — connect() 失败"
                    )
            template = kwargs.get(
                "template", self.vibe_config.project_template
            )
            return self._generate_project(task, template)

    def health_check(self) -> bool:
        """检查平台可用性（GET base_url 返回状态码 < 500）。"""
        with self._lock:
            if self._client is None:
                return False
            try:
                resp = self._client.get(
                    self.vibe_config.base_url, timeout=5.0
                )
                return resp.status_code < 500
            except Exception:
                return False

    def sync_config(self, config: dict[str, Any]) -> None:
        """用新配置替换当前配置并重连。

        ``config`` 字段对应 :class:`VibeCodingConfig`。
        """
        with self._lock:
            was_connected = self._connected
            self.disconnect()
            new_vibe = VibeCodingConfig(**config)
            # 填充默认 URL
            if not new_vibe.base_url:
                new_vibe = new_vibe.model_copy(
                    update={
                        "base_url": _PLATFORM_DEFAULT_URLS.get(
                            new_vibe.platform, ""
                        )
                    }
                )
            self.vibe_config = new_vibe
            # 同步更新父类配置
            self.config = self._build_web_config(new_vibe)
            self._auth_value = new_vibe.auth_token
            if was_connected:
                self.connect()

    def disconnect(self) -> None:
        """关闭 HTTP 客户端并断开连接。"""
        with self._lock:
            super().disconnect()

    # ------------------------------------------------------------------
    # 项目生成流程
    # ------------------------------------------------------------------

    def _generate_project(
        self, description: str, template: str = ""
    ) -> str:
        """提交生成请求并轮询直到完成，返回项目 URL/ID。

        轮询超时保护：最大生成时间上限为 ``max_generation_time_s``。
        超时抛出 :class:`TimeoutError`；生成失败抛出 :class:`RuntimeError`。

        Parameters
        ----------
        description : str
            项目描述（自然语言）。
        template : str
            项目模板，留空时使用配置中的 ``project_template``。
        """
        if self._client is None:
            raise RuntimeError("HTTP 客户端未初始化")

        # 构建生成请求负载
        payload: dict[str, Any] = {
            "description": description,
            "platform": self.vibe_config.platform,
        }
        effective_template = template or self.vibe_config.project_template
        if effective_template:
            payload["template"] = effective_template

        # 提交生成请求
        try:
            resp = self._client.post(
                self._generate_url(),
                json=payload,
                timeout=self.vibe_config.max_generation_time_s,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise RuntimeError(f"提交生成请求失败: {exc}") from exc

        project_id = data.get("project_id") or data.get("id")
        if not project_id:
            raise RuntimeError(f"生成响应缺少 project_id: {data}")

        logger.info(
            "[vibe_coding] 已提交生成请求，project_id=%s", project_id
        )

        # 轮询直到完成或超时
        deadline = monotonic() + self.vibe_config.max_generation_time_s
        while monotonic() < deadline:
            status_info = self._poll_status(project_id)
            status = status_info.get("status", "")

            if status == "completed":
                project_url = status_info.get(
                    "project_url",
                    f"{self._api_base()}/projects/{project_id}",
                )
                logger.info(
                    "[vibe_coding] 项目 %s 生成完成", project_id
                )
                return project_url

            if status == "failed":
                error = status_info.get("error", "未知错误")
                raise RuntimeError(f"项目生成失败: {error}")

            # queued / generating — 释放锁后 sleep，允许其他线程操作
            self._lock.release()
            try:
                sleep(self.vibe_config.poll_interval_s)
            finally:
                self._lock.acquire()

        # 超时
        raise TimeoutError(
            f"项目生成超时（{self.vibe_config.max_generation_time_s}s）"
        )

    def _poll_status(self, project_id: str) -> dict[str, Any]:
        """轮询项目生成状态，返回状态信息字典。

        返回的字典至少包含 ``status`` 字段，值为
        ``"queued"`` / ``"generating"`` / ``"completed"`` / ``"failed"``。
        """
        client = self._client
        if client is None:
            raise RuntimeError("HTTP 客户端未初始化")
        try:
            resp = client.get(self._status_url(project_id), timeout=10.0)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            raise RuntimeError(f"轮询状态失败: {exc}") from exc

    def _export_project(self, project_id: str, format: str) -> str:
        """导出项目，返回下载 URL 或文件路径。

        Parameters
        ----------
        project_id : str
            项目 ID。
        format : str
            导出格式：``"zip"`` / ``"git"`` / ``"files"``。
        """
        if format not in _VALID_EXPORT_FORMATS:
            raise ValueError(
                f"不支持的导出格式: {format}"
                f"（合法: {sorted(_VALID_EXPORT_FORMATS)}）"
            )
        client = self._client
        if client is None:
            raise RuntimeError("HTTP 客户端未初始化")
        try:
            resp = client.post(
                self._export_url(project_id),
                json={"format": format},
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise RuntimeError(f"导出项目失败: {exc}") from exc

        export_url = data.get("download_url") or data.get("url")
        if not export_url:
            raise RuntimeError(f"导出响应缺少 download_url: {data}")
        return export_url

    def _download_project(self, export_url: str) -> bytes:
        """下载项目文件，返回文件字节内容。"""
        client = self._client
        if client is None:
            raise RuntimeError("HTTP 客户端未初始化")
        try:
            resp = client.get(export_url, timeout=60.0)
            resp.raise_for_status()
            return resp.content
        except Exception as exc:
            raise RuntimeError(f"下载项目失败: {exc}") from exc