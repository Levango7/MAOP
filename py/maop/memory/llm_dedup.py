"""MAOP LLM Semantic Dedup — 方案 A：LLM 语义去重判定器。

背景
----
L1 原子事实层默认用 SHA-256 语义指纹去重（逐字规范化，精确匹配）。
"user likes coffee" 与 "user prefers coffee" 指纹不同，会被存成两条——
这是正则/哈希去重的固有盲区。

方案 A：在指纹未命中时，把新事实与同 subject/predicate 的候选事实交给
LLM 判定"是否同一事实"，命中则合并。本模块提供：

  - ``LLMJudge`` 契约：``Callable[[dict, dict], bool | None]``
      True  = 两个事实语义相同（应合并）
      False = 语义不同（应插入新）
      None  = 无法判断（调用方降级为插入新）
  - ``build_llm_semantic_judge()``：从 models.yaml 读取配置，构造一个
    **同步** httpx 判定器（OpenAI 兼容 /chat/completions）。

设计要点（对齐方案 A 的"低代价 + 失败安全"）：
  - 同步调用：MAOP 的 ``LLMProvider`` 是 async，而 ``atoms.ingest()``
    在同步链路上（``add_exchange`` → ``ingest``），因此判定器用同步
    httpx 直接调 OpenAI 兼容端点，不引入异步改造。
  - 短超时：默认 8s，LLM 判定不应阻塞记忆写入太久。
  - 失败降级：任何异常/解析失败/超时都返回 None，调用方按"插入新"
    处理——LLM 去重是锦上添花，绝不能让记忆链路挂掉。
  - 默认关闭：``AtomFactStore(llm_dedup=False)`` 时完全不构造判定器。

Usage::

    from maop.memory.atoms import AtomFactStore
    from maop.memory.llm_dedup import build_llm_semantic_judge

    judge = build_llm_semantic_judge(root_dir="/path/to/MAOP", model="step-3.7-flash")
    atoms = AtomFactStore(root_dir, llm_dedup=True, llm_judge=judge)
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 判定器契约：两个事实 dict → bool(同一) / None(无法判断)
LLMJudge = Callable[[dict[str, Any], dict[str, Any]], bool | None]

# LLM 判定请求超时（秒）。判定器应快，慢则降级。
DEFAULT_TIMEOUT_S = 8.0

# 判定 prompt：要求 LLM 只输出 {"same": true|false}，便于解析。
_JUDGE_SYSTEM_PROMPT = (
    "You are a fact-deduplication judge for a knowledge base. "
    "Two facts are the SAME if they express the same meaning, even if the "
    'wording differs (e.g. "user likes coffee" vs "the user prefers coffee"). '
    'Reply with ONLY a JSON object: {"same": true} or {"same": false}. '
    "Do not add any other text."
)


def _format_fact(fact: dict[str, Any]) -> str:
    """把事实 dict 格式化为一行文本。"""
    subject = str(fact.get("subject") or "").strip()
    predicate = str(fact.get("predicate") or "").strip()
    obj = str(fact.get("object_value") or "").strip()
    return " ".join(p for p in (subject, predicate, obj) if p) or "(empty)"


def build_llm_semantic_judge(
    root_dir: str | Path,
    *,
    model: str = "",
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> LLMJudge | None:
    """从 models.yaml 构造同步 LLM 语义去重判定器。

    Parameters
    ----------
    root_dir : str | Path
        MAOP 项目根目录（定位 ``config/models.yaml``）。
    model : str
        模型名（models.yaml 中的 key）。为空时自动选第一个 enabled 模型。
    timeout_s : float
        单次判定请求超时（秒）。

    Returns
    -------
    LLMJudge | None
        构造失败（无配置 / 无 key / 无可用模型）时返回 None，调用方按
        未启用处理。返回的判定器本身永不抛异常（内部捕获并返回 None）。
    """
    try:
        cfg = _load_model_config(root_dir, model)
        if cfg is None:
            return None
        base_url, api_key, model_id = cfg
        if not base_url or not api_key or not model_id:
            logger.warning("[llm_dedup] 配置不完整，判定器不可用")
            return None
    except Exception as exc:
        logger.warning("[llm_dedup] 配置加载失败: %s", exc)
        return None

    endpoint = base_url.rstrip("/") + "/chat/completions"

    def judge(fact_a: dict[str, Any], fact_b: dict[str, Any]) -> bool | None:
        """判定两条事实是否语义相同。任何失败返回 None（降级）。"""
        try:
            import httpx

            a_text = _format_fact(fact_a)
            b_text = _format_fact(fact_b)
            if not a_text or not b_text:
                return None
            user_prompt = (
                f"Fact A: {a_text}\nFact B: {b_text}\n"
                'Are they the same fact? Reply {"same": true/false}.'
            )
            resp = httpx.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model_id,
                    "messages": [
                        {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.0,
                    "max_tokens": 16,
                },
                timeout=timeout_s,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            verdict = _parse_same(content)
            logger.debug("[llm_dedup] %r vs %r -> %s", a_text[:40], b_text[:40], verdict)
            return verdict
        except Exception as exc:
            logger.warning("[llm_dedup] 判定失败（降级为插入新）: %s", exc)
            return None

    return judge


def _parse_same(content: str) -> bool | None:
    """从 LLM 输出中解析 {"same": true/false}。失败返回 None。"""
    if not content:
        return None
    text = content.strip()
    # 尝试直接 JSON 解析
    try:
        data = json.loads(text)
        if isinstance(data, dict) and isinstance(data.get("same"), bool):
            return bool(data["same"])
    except (ValueError, TypeError):
        pass
    # 兜底：正则提取 true/false
    import re

    m = re.search(r'"same"\s*:\s*(true|false)', text, re.IGNORECASE)
    if m:
        return m.group(1).lower() == "true"
    if re.search(r"\btrue\b", text, re.IGNORECASE):
        return True
    if re.search(r"\bfalse\b", text, re.IGNORECASE):
        return False
    return None


def _load_model_config(
    root_dir: str | Path,
    model_name: str,
) -> tuple[str, str, str] | None:
    """从 config/models.yaml 读取 (base_url, api_key, model_id)。

    兼容 models.yaml 两种形态：
      - 新形态：``models: {name: {provider, model_id, ...}}`` + ``providers: {...}``
      - 直连形态：模型条目里直接带 ``base_url`` / ``api_key_env``
    """
    root = Path(root_dir)
    # 兼容 py/ 子目录：config 可能在 root 或 root/py
    config_dir = root / "config"
    if not (config_dir / "models.yaml").exists():
        config_dir = root / "py" / "config"
    models_path = config_dir / "models.yaml"
    if not models_path.exists():
        logger.warning("[llm_dedup] 找不到 models.yaml: %s", models_path)
        return None

    try:
        import yaml

        data = yaml.safe_load(models_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.warning("[llm_dedup] models.yaml 解析失败: %s", exc)
        return None

    providers = data.get("providers") or {}
    models = data.get("models") or {}

    # 选择模型
    if model_name and model_name in models:
        model_cfg = models[model_name]
    else:
        # 自动选第一个 enabled 模型
        model_cfg = None
        for cfg in models.values():
            if isinstance(cfg, dict) and cfg.get("enabled", True):
                model_cfg = cfg
                break
    if not isinstance(model_cfg, dict):
        return None

    model_id = str(model_cfg.get("model_id") or "")

    # 直连形态：条目自带 base_url/api_key_env
    base_url = str(model_cfg.get("base_url") or "")
    api_key_env = str(model_cfg.get("api_key_env") or "")

    # 引用形态：通过 provider 名查 providers 段
    provider_name = str(model_cfg.get("provider") or "")
    if not base_url and provider_name and provider_name in providers:
        pcfg = providers[provider_name]
        if isinstance(pcfg, dict):
            base_url = str(pcfg.get("base_url") or "")
            api_key_env = str(pcfg.get("api_key_env") or "")

    if not base_url:
        logger.warning("[llm_dedup] 模型 %r 无 base_url", model_name or "(auto)")
        return None

    # API key：环境变量 > 直接配置
    api_key = ""
    if api_key_env:
        api_key = os.environ.get(api_key_env, "")
    if not api_key:
        api_key = str(model_cfg.get("api_key") or "")
    if not api_key:
        logger.warning("[llm_dedup] 无可用 API key（环境变量 %r 未设置）", api_key_env)
        return None

    return (base_url, api_key, model_id)
