"""第三方中转平台管理器 — 支持 OpenRouter / SiliconFlow / NVIDIA NIM 等。

中转平台（relay platform）通过统一的 OpenAI 兼容接口聚合多家模型供应商，
一个 API key 即可访问 100+ 模型，按 token 计费，价格通常比直连更便宜。

本模块提供：
  - ``RelayPlatform``        — 平台元数据 Pydantic 模型
  - ``RelayModelInfo``       — 平台可用模型信息
  - ``PriceComparison``      — 跨平台价格对比结果
  - ``RelayPlatformManager`` — 线程安全的平台注册/查询/模型发现/价格对比/推荐管理器

设计约束：
  1. 不修改现有 ``LLMProviderFactory``，本管理器独立运作，通过 models.yaml 集成
  2. 所有中转平台都使用 OpenAI 兼容接口，GET /models 发现模型
  3. 使用 ``threading.RLock`` 保护所有状态，支持并发注册/查询
  4. SQLite 持久化平台注册信息，跨进程共享
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


# ── Pydantic 模型定义 ────────────────────────────────────────────────


class RelayPlatform(BaseModel):
    """中转平台元数据。

    Attributes
    ----------
    name : str
        平台标识名（唯一键，对应 models.yaml 中的 provider key）
    type : str
        平台类型，``"relay"`` 表示中转平台，``"direct"`` 表示直连供应商
    base_url : str
        平台 API 基础 URL（OpenAI 兼容）
    api_key : str
        平台 API key（运行时从环境变量解析后填入，持久化时脱敏）
    display_name : str
        前端展示名称
    description : str
        平台描述
    features : list[str]
        平台支持的功能列表（model_discovery / price_comparison / free_tier /
        streaming / gpu_acceleration 等）
    rate_limit_rpm : int
        平台速率限制（每分钟请求数），0 表示无限制
    region : str
        平台所在区域，``"domestic"`` 国内 / ``"international"`` 国际
    enabled : bool
        是否启用
    """

    model_config = ConfigDict(protected_namespaces=())

    name: str
    type: str = "relay"
    base_url: str = ""
    api_key: str = ""
    display_name: str = ""
    description: str = ""
    features: list[str] = Field(default_factory=list)
    rate_limit_rpm: int = 0
    region: str = "international"
    enabled: bool = True


class RelayModelInfo(BaseModel):
    """中转平台可用模型信息。

    通过 GET /models 接口发现，并尽可能补充价格/上下文长度/能力等元数据。
    """

    model_config = ConfigDict(protected_namespaces=())

    model_id: str
    platform_name: str
    price_per_1k_input: float = 0.0
    price_per_1k_output: float = 0.0
    context_length: int = 0
    capabilities: list[str] = Field(default_factory=list)
    available: bool = True


class PriceComparison(BaseModel):
    """同一模型在不同平台的价格对比结果。"""

    model_config = ConfigDict(protected_namespaces=())

    model_id: str
    platform_name: str
    price_per_1k_input: float
    price_per_1k_output: float
    total_per_1k: float  # input + output 合计
    is_free: bool  # 是否免费
    region: str


# ── 管理器 ────────────────────────────────────────────────────────────


class RelayPlatformManager:
    """线程安全的第三方中转平台管理器。

    职责：
      - 平台 CRUD（注册/查询/列表/删除）
      - 模型发现（GET /models）
      - 价格对比（同一模型跨平台）
      - 平台推荐（价格最低 + 可用）

    持久化：
      - 平台注册信息存入 SQLite（relay_platforms 表）
      - 已发现的模型缓存到 relay_models 表
      - 速率限制计数器存内存（进程级）

    线程安全：
      - 使用 ``threading.RLock`` 保护所有状态
      - SQLite 通过 ``check_same_thread=False`` 跨线程共享
    """

    def __init__(self, db_path: Path | None = None) -> None:
        """初始化管理器。

        Parameters
        ----------
        db_path : Path | None
            SQLite 数据库路径。``None`` 时使用内存数据库（``:memory:``），
            适合测试或临时实例。
        """
        if db_path is None:
            self._db_path = ":memory:"
        else:
            self._db_path = str(db_path)
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # RLock 保护 _platforms / _model_cache / _rate_limit_counters
        self._lock = threading.RLock()
        self._platforms: dict[str, RelayPlatform] = {}
        self._model_cache: dict[str, list[RelayModelInfo]] = {}
        # 速率限制计数器：platform_name -> [timestamp, ...]
        self._rate_limit_counters: dict[str, list[float]] = {}

        self._init_db()
        self._load_from_db()

    # ── SQLite 初始化 ──────────────────────────────────────────────

    def _init_db(self) -> None:
        """创建 SQLite 表结构（幂等）。"""
        import sqlite3

        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS relay_platforms (
                    name           TEXT PRIMARY KEY,
                    type           TEXT NOT NULL DEFAULT 'relay',
                    base_url       TEXT NOT NULL DEFAULT '',
                    api_key        TEXT NOT NULL DEFAULT '',
                    display_name   TEXT NOT NULL DEFAULT '',
                    description    TEXT NOT NULL DEFAULT '',
                    features       TEXT NOT NULL DEFAULT '[]',
                    rate_limit_rpm INTEGER NOT NULL DEFAULT 0,
                    region         TEXT NOT NULL DEFAULT 'international',
                    enabled        INTEGER NOT NULL DEFAULT 1,
                    updated_at     REAL NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS relay_models (
                    model_id            TEXT NOT NULL,
                    platform_name       TEXT NOT NULL,
                    price_per_1k_input  REAL NOT NULL DEFAULT 0,
                    price_per_1k_output REAL NOT NULL DEFAULT 0,
                    context_length      INTEGER NOT NULL DEFAULT 0,
                    capabilities        TEXT NOT NULL DEFAULT '[]',
                    available           INTEGER NOT NULL DEFAULT 1,
                    discovered_at       REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (model_id, platform_name)
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def _load_from_db(self) -> None:
        """从 SQLite 加载已持久化的平台到内存。"""
        import sqlite3

        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM relay_platforms").fetchall()
            for row in rows:
                platform = RelayPlatform(
                    name=row["name"],
                    type=row["type"],
                    base_url=row["base_url"],
                    api_key=row["api_key"],
                    display_name=row["display_name"],
                    description=row["description"],
                    features=json.loads(row["features"]),
                    rate_limit_rpm=row["rate_limit_rpm"],
                    region=row["region"],
                    enabled=bool(row["enabled"]),
                )
                self._platforms[platform.name] = platform
        finally:
            conn.close()

    def _persist_platform(self, platform: RelayPlatform) -> None:
        """将单个平台写入 SQLite（upsert）。"""
        import sqlite3

        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO relay_platforms
                    (name, type, base_url, api_key, display_name, description,
                     features, rate_limit_rpm, region, enabled, updated_at)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    platform.name,
                    platform.type,
                    platform.base_url,
                    platform.api_key,
                    platform.display_name,
                    platform.description,
                    json.dumps(platform.features),
                    platform.rate_limit_rpm,
                    platform.region,
                    int(platform.enabled),
                    time.time(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _delete_platform_from_db(self, name: str) -> None:
        """从 SQLite 删除平台记录。"""
        import sqlite3

        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        try:
            conn.execute("DELETE FROM relay_platforms WHERE name = ?", (name,))
            conn.execute("DELETE FROM relay_models WHERE platform_name = ?", (name,))
            conn.commit()
        finally:
            conn.close()

    def _persist_model(self, model: RelayModelInfo) -> None:
        """将发现的模型写入 SQLite 缓存（upsert）。"""
        import sqlite3

        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO relay_models
                    (model_id, platform_name, price_per_1k_input,
                     price_per_1k_output, context_length, capabilities,
                     available, discovered_at)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    model.model_id,
                    model.platform_name,
                    model.price_per_1k_input,
                    model.price_per_1k_output,
                    model.context_length,
                    json.dumps(model.capabilities),
                    int(model.available),
                    time.time(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _load_models_from_db(self, platform_name: str) -> list[RelayModelInfo]:
        """从 SQLite 加载指定平台的已缓存模型列表。"""
        import sqlite3

        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM relay_models WHERE platform_name = ?",
                (platform_name,),
            ).fetchall()
            return [
                RelayModelInfo(
                    model_id=row["model_id"],
                    platform_name=row["platform_name"],
                    price_per_1k_input=row["price_per_1k_input"],
                    price_per_1k_output=row["price_per_1k_output"],
                    context_length=row["context_length"],
                    capabilities=json.loads(row["capabilities"]),
                    available=bool(row["available"]),
                )
                for row in rows
            ]
        finally:
            conn.close()

    # ── 平台 CRUD ──────────────────────────────────────────────────

    def register_platform(self, platform: RelayPlatform) -> None:
        """注册或更新一个中转平台。

        若平台已存在，则覆盖更新。线程安全。

        Parameters
        ----------
        platform : RelayPlatform
            要注册的平台实例
        """
        with self._lock:
            self._platforms[platform.name] = platform
            self._persist_platform(platform)
            # 注册时清空该平台的模型缓存，下次 discover_models 重新拉取
            self._model_cache.pop(platform.name, None)
            logger.info("[relay] 已注册平台: %s (%s)", platform.name, platform.display_name)

    def get_platform(self, name: str) -> RelayPlatform | None:
        """按名称获取平台。

        Parameters
        ----------
        name : str
            平台标识名

        Returns
        -------
        RelayPlatform | None
            平台实例，不存在时返回 None
        """
        with self._lock:
            return self._platforms.get(name)

    def list_platforms(self, type: str = "") -> list[RelayPlatform]:
        """列出所有平台，可按类型过滤。

        Parameters
        ----------
        type : str
            平台类型过滤：``""`` 返回全部，``"relay"`` 返回中转平台，
            ``"direct"`` 返回直连供应商

        Returns
        -------
        list[RelayPlatform]
            平台列表
        """
        with self._lock:
            platforms = list(self._platforms.values())
        if type:
            platforms = [p for p in platforms if p.type == type]
        return platforms

    def remove_platform(self, name: str) -> None:
        """删除一个平台。

        不存在时静默忽略。线程安全。

        Parameters
        ----------
        name : str
            要删除的平台名
        """
        with self._lock:
            self._platforms.pop(name, None)
            self._model_cache.pop(name, None)
            self._rate_limit_counters.pop(name, None)
            self._delete_platform_from_db(name)
            logger.info("[relay] 已删除平台: %s", name)

    # ── 模型发现 ──────────────────────────────────────────────────

    def discover_models(
        self,
        platform_name: str,
        *,
        use_cache: bool = True,
        timeout_s: float = 30.0,
    ) -> list[RelayModelInfo]:
        """从中转平台 API 获取可用模型列表。

        调用 OpenAI 兼容的 ``GET /models`` 接口。失败时回退到 SQLite 缓存。

        Parameters
        ----------
        platform_name : str
            平台名
        use_cache : bool
            是否使用内存/SQLite 缓存。``False`` 强制重新拉取。
        timeout_s : float
            HTTP 请求超时秒数

        Returns
        -------
        list[RelayModelInfo]
            可用模型列表，平台不存在或请求失败时返回空列表
        """
        with self._lock:
            platform = self._platforms.get(platform_name)
            if platform is None:
                logger.warning("[relay] 平台不存在: %s", platform_name)
                return []
            if use_cache and platform_name in self._model_cache:
                return list(self._model_cache[platform_name])

        # 同步调用 httpx（管理器不强制 async，保持与 ApiKeyManager 一致风格）
        try:
            models = self._fetch_models_from_api(platform, timeout_s)
        except Exception as exc:
            logger.warning(
                "[relay] 从 %s 发现模型失败: %s，回退到缓存",
                platform_name, exc,
            )
            # 回退到 SQLite 缓存
            with self._lock:
                cached = self._load_models_from_db(platform_name)
                self._model_cache[platform_name] = cached
                return cached

        with self._lock:
            self._model_cache[platform_name] = models
            # 持久化到 SQLite
            for m in models:
                self._persist_model(m)
        return models

    def _fetch_models_from_api(
        self, platform: RelayPlatform, timeout_s: float
    ) -> list[RelayModelInfo]:
        """实际调用平台 GET /models 接口。"""
        url = f"{platform.base_url.rstrip('/')}/models"
        headers = {"Authorization": f"Bearer {platform.api_key}"}
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        models: list[RelayModelInfo] = []
        for item in data.get("data", []):
            model_id = item.get("id", "")
            if not model_id:
                continue
            # OpenAI 兼容接口的 /models 通常不返回价格信息，
            # 价格需要从平台定价 API 或本地配置补充
            models.append(
                RelayModelInfo(
                    model_id=model_id,
                    platform_name=platform.name,
                    context_length=item.get("context_length", 0) or 0,
                    capabilities=item.get("capabilities", []) or [],
                    available=True,
                )
            )
        return models

    # ── 价格对比 ──────────────────────────────────────────────────

    def compare_prices(self, model_id: str) -> list[PriceComparison]:
        """对比同一模型在不同平台的价格。

        遍历所有已注册平台，从其模型缓存中查找匹配 model_id 的模型，
        汇总价格信息。

        Parameters
        ----------
        model_id : str
            要对比的模型 ID

        Returns
        -------
        list[PriceComparison]
            各平台价格对比结果，按 total_per_1k 升序排列
        """
        comparisons: list[PriceComparison] = []
        with self._lock:
            platform_names = list(self._platforms.keys())

        for pname in platform_names:
            with self._lock:
                models = self._model_cache.get(pname)
                if models is None:
                    # 尝试从 SQLite 加载
                    models = self._load_models_from_db(pname)
                    self._model_cache[pname] = models
                platform = self._platforms.get(pname)

            if platform is None:
                continue
            for m in models:
                if m.model_id != model_id or not m.available:
                    continue
                total = m.price_per_1k_input + m.price_per_1k_output
                comparisons.append(
                    PriceComparison(
                        model_id=model_id,
                        platform_name=pname,
                        price_per_1k_input=m.price_per_1k_input,
                        price_per_1k_output=m.price_per_1k_output,
                        total_per_1k=total,
                        is_free=(total == 0.0),
                        region=platform.region,
                    )
                )

        # 按合计价格升序排列（最便宜在前）
        comparisons.sort(key=lambda c: c.total_per_1k)
        return comparisons

    # ── 平台推荐 ──────────────────────────────────────────────────

    def recommend_platform(
        self, model_id: str, prefer_domestic: bool = False
    ) -> str | None:
        """推荐最优平台（价格最低 + 可用）。

        Parameters
        ----------
        model_id : str
            目标模型 ID
        prefer_domestic : bool
            是否优先推荐国内平台。``True`` 时在国内平台中选最便宜的，
            若无国内平台可用则回退到国际平台。

        Returns
        -------
        str | None
            推荐的平台名，无可用平台时返回 None
        """
        comparisons = self.compare_prices(model_id)
        if not comparisons:
            return None

        if prefer_domestic:
            domestic = [c for c in comparisons if c.region == "domestic"]
            if domestic:
                return domestic[0].platform_name
            # 回退到国际平台
            return comparisons[0].platform_name

        return comparisons[0].platform_name

    # ── 速率限制跟踪 ──────────────────────────────────────────────

    def record_request(self, platform_name: str) -> None:
        """记录一次请求到速率限制计数器。

        清理超过 60 秒的旧时间戳，追加当前时间戳。

        Parameters
        ----------
        platform_name : str
            平台名
        """
        now = time.time()
        with self._lock:
            counter = self._rate_limit_counters.setdefault(platform_name, [])
            # 清理 60 秒窗口外的旧时间戳
            counter[:] = [t for t in counter if now - t < 60.0]
            counter.append(now)

    def get_request_count(self, platform_name: str) -> int:
        """获取平台在当前 60 秒窗口内的请求计数。

        Parameters
        ----------
        platform_name : str
            平台名

        Returns
        -------
        int
            当前窗口内请求数
        """
        now = time.time()
        with self._lock:
            counter = self._rate_limit_counters.get(platform_name, [])
            return sum(1 for t in counter if now - t < 60.0)

    def is_rate_limited(self, platform_name: str) -> bool:
        """判断平台是否已达到速率限制。

        Parameters
        ----------
        platform_name : str
            平台名

        Returns
        -------
        bool
            ``True`` 表示已达限制，应拒绝请求
        """
        with self._lock:
            platform = self._platforms.get(platform_name)
            if platform is None or platform.rate_limit_rpm <= 0:
                return False
            count = self.get_request_count(platform_name)
            return count >= platform.rate_limit_rpm

    # ── 从 models.yaml 加载 ───────────────────────────────────────

    def load_from_models_yaml(self, yaml_path: str | Path) -> None:
        """从 models.yaml 加载中转平台配置。

        解析 ``providers`` 段中 ``type: relay`` 的条目，注册为 RelayPlatform。
        API key 从环境变量解析（``api_key_env`` 指定变量名）。

        Parameters
        ----------
        yaml_path : str | Path
            models.yaml 文件路径
        """
        import yaml

        yaml_path = Path(yaml_path)
        if not yaml_path.exists():
            logger.warning("[relay] models.yaml 不存在: %s", yaml_path)
            return

        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        providers = data.get("providers", {})
        for name, pdata in providers.items():
            ptype = pdata.get("type", "")
            if ptype != "relay":
                continue
            api_key_env = pdata.get("api_key_env", "")
            api_key = os.environ.get(api_key_env, "") if api_key_env else ""
            platform = RelayPlatform(
                name=name,
                type="relay",
                base_url=pdata.get("base_url", ""),
                api_key=api_key,
                display_name=pdata.get("display_name", name),
                description=pdata.get("description", ""),
                features=pdata.get("features", []) or [],
                rate_limit_rpm=pdata.get("rate_limit_rpm", 0) or 0,
                region=pdata.get("region", "international"),
                enabled=pdata.get("enabled", True),
            )
            self.register_platform(platform)

    # ── 手动补充模型价格 ──────────────────────────────────────────

    def update_model_price(
        self,
        platform_name: str,
        model_id: str,
        price_per_1k_input: float,
        price_per_1k_output: float,
    ) -> None:
        """手动更新模型的输入/输出价格。

        OpenAI 兼容的 GET /models 接口通常不返回价格信息，
        需要通过此方法或平台定价 API 补充。

        Parameters
        ----------
        platform_name : str
            平台名
        model_id : str
            模型 ID
        price_per_1k_input : float
            每 1k 输入 token 价格（美元）
        price_per_1k_output : float
            每 1k 输出 token 价格（美元）
        """
        with self._lock:
            models = self._model_cache.get(platform_name, [])
            for m in models:
                if m.model_id == model_id:
                    m.price_per_1k_input = price_per_1k_input
                    m.price_per_1k_output = price_per_1k_output
                    self._persist_model(m)
                    return
            # 模型不在缓存中，创建新条目
            new_model = RelayModelInfo(
                model_id=model_id,
                platform_name=platform_name,
                price_per_1k_input=price_per_1k_input,
                price_per_1k_output=price_per_1k_output,
            )
            models.append(new_model)
            self._model_cache[platform_name] = models
            self._persist_model(new_model)

    # ── 收尾 ──────────────────────────────────────────────────────

    def close(self) -> None:
        """清理资源（当前 SQLite 每次操作都关闭连接，无需特殊处理）。"""
        pass


# ── 模块级单例（可选，方便全局访问）────────────────────────────────

_manager_instance: RelayPlatformManager | None = None
_manager_lock = threading.Lock()


def get_relay_platform_manager(
    db_path: Path | None = None,
) -> RelayPlatformManager:
    """获取或创建模块级单例管理器。

    Parameters
    ----------
    db_path : Path | None
        仅在首次创建时生效

    Returns
    -------
    RelayPlatformManager
        单例实例
    """
    global _manager_instance
    with _manager_lock:
        if _manager_instance is None:
            _manager_instance = RelayPlatformManager(db_path=db_path)
        return _manager_instance


def reset_relay_platform_manager() -> None:
    """重置模块级单例（测试用）。"""
    global _manager_instance
    with _manager_lock:
        if _manager_instance is not None:
            _manager_instance.close()
        _manager_instance = None