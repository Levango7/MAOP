"""MAOP LLM 智能任务拆分器 — 自然语言 → 子任务 DAG。

将用户输入的一段自然语言任务描述（如"调研竞品并生成对比报告"）通过 LLM
自动拆分为多个子任务，并生成 DAG 依赖图，直接可用于 MAOP 的
:class:`~maop.core.reliability.dag_scheduler.DAGScheduler` 执行。

主要能力::

    splitter = TaskSplitter()
    result = await splitter.split("调研竞品并生成对比报告")
    # result = {
    #     "subtasks": [{"id": "t1", "name": "...", "description": "...", "depends_on": []}, ...],
    #     "edges": [["t1", "t2"], ...],
    #     "dag": {"nodes": [...], "edges": [...]},  # 兼容 DAGScheduler 的格式
    # }

容错策略:
  - LLM 不可用 → 返回单节点 DAG（fallback）
  - LLM 返回非法 JSON → 抛 TaskSplitError
  - LLM 返回有环/引用缺失 → 抛 TaskSplitError

Personal edition: 默认 LLM provider 通过 :class:`LLMProviderFactory` 加载，
当 ``models.yaml`` 缺失或所有 provider 都未配置时自动 fallback。
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from maop.core.llm_provider import BaseLLMProvider, LLMProviderFactory

logger = logging.getLogger(__name__)


# ── 异常 ──────────────────────────────────────────────────────────

class TaskSplitError(Exception):
    """任务拆分过程中发生的错误（LLM 调用失败、JSON 解析失败、DAG 验证失败等）。"""


# ── 默认 system prompt ───────────────────────────────────────────

_DEFAULT_SYSTEM_PROMPT = """你是一个任务拆分专家。将用户给定的自然语言任务描述拆分为多个可独立执行的子任务，并生成 DAG 依赖图。

要求:
1. 每个子任务必须有唯一 id（格式 t1, t2, t3...）、name（简短名称）、description（详细描述）、depends_on（依赖的子任务 id 列表）
2. depends_on 中的 id 必须是其他子任务的 id，不能引用不存在的 id
3. 依赖关系必须构成有向无环图（DAG），禁止循环依赖
4. 子任务数量不超过 {max_subtasks} 个
5. 拆分粒度合理：每个子任务应当是一个可独立完成的工作单元
6. 严格按 JSON 格式返回，不要包含任何额外说明文字

返回格式（必须是合法 JSON）:
{{
  "subtasks": [
    {{"id": "t1", "name": "子任务名称", "description": "详细描述", "depends_on": []}},
    {{"id": "t2", "name": "子任务名称", "description": "详细描述", "depends_on": ["t1"]}}
  ],
  "edges": [["t1", "t2"]]
}}

其中 edges 是显式的依赖边列表，每条边 [a, b] 表示 a → b（a 是 b 的前置）。
edges 与 subtasks[i].depends_on 应保持一致。"""


# ── TaskSplitter ─────────────────────────────────────────────────

class TaskSplitter:
    """LLM 驱动的自然语言任务拆分器。

    Parameters
    ----------
    llm_provider : BaseLLMProvider | None
        可选的 LLM provider 实例。当为 ``None`` 时， lazily 构造一个
        :class:`LLMProviderFactory` 并取默认 model 的 provider；若任何
        环节失败，:meth:`split` 将走 fallback 路径返回单节点 DAG。
    model_name : str
        使用的模型名（当 ``llm_provider`` 为 ``None`` 时用于 factory 查找）。
        空字符串表示使用 ``models.yaml`` 中配置的 ``default_model``。
    """

    def __init__(
        self,
        llm_provider: BaseLLMProvider | None = None,
        *,
        model_name: str = "",
    ) -> None:
        self._llm_provider: BaseLLMProvider | None = llm_provider
        self._model_name: str = model_name
        # factory 延迟构造，避免在 __init__ 阶段触发文件 IO / env 读取
        self._factory: LLMProviderFactory | None = None

    # ── 核心方法 ───────────────────────────────────────────────

    async def split(
        self,
        description: str,
        context: str = "",
        max_subtasks: int = 10,
    ) -> dict[str, Any]:
        """将自然语言任务描述拆分为子任务 DAG。

        Parameters
        ----------
        description : str
            用户的自然语言任务描述（非空）。
        context : str
            额外上下文信息（可选），会拼接到 prompt 中帮助 LLM 理解背景。
        max_subtasks : int
            最大子任务数量，默认 10。LLM 不应返回超过此数量的子任务。

        Returns
        -------
        dict
            ``{"subtasks": [...], "edges": [...], "dag": {"nodes": [...], "edges": [...]}}``
            其中 ``dag`` 字段格式兼容 :class:`DAGScheduler`。

        Raises
        ------
        TaskSplitError
            当 description 为空、LLM 响应非法 JSON、或生成的 DAG 有环/引用缺失时。
        """
        if not description or not description.strip():
            raise TaskSplitError("任务描述不能为空")

        # 限制 max_subtasks 合理范围
        max_subtasks = max(1, min(int(max_subtasks), 50))

        provider = self._resolve_provider()
        if provider is None:
            logger.warning("[task_splitter] LLM 不可用，使用 fallback 单节点 DAG")
            return self._fallback(description)

        prompt = self._build_prompt(description, context, max_subtasks)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _DEFAULT_SYSTEM_PROMPT.format(max_subtasks=max_subtasks)},
            {"role": "user", "content": prompt},
        ]

        try:
            response = await provider.chat(
                messages=messages,
                model=self._model_name or "",
                temperature=0.3,  # 低温度保证结构化输出稳定
                max_tokens=4096,
            )
        except Exception as exc:
            logger.warning("[task_splitter] LLM 调用失败: %s，使用 fallback", exc)
            return self._fallback(description)

        content = response.content if hasattr(response, "content") else str(response)
        if not content or not content.strip():
            logger.warning("[task_splitter] LLM 返回空内容，使用 fallback")
            return self._fallback(description)

        try:
            parsed = self._parse_response(content)
        except TaskSplitError:
            # JSON 解析失败 → fallback 而不是抛出，保证健壮性
            logger.warning("[task_splitter] LLM 响应 JSON 解析失败，使用 fallback")
            return self._fallback(description)

        subtasks = parsed.get("subtasks", [])
        edges = parsed.get("edges", [])

        # 规范化 edges 为 list[list[str]]
        normalized_edges: list[list[str]] = []
        for e in edges:
            if isinstance(e, (list, tuple)) and len(e) == 2:
                normalized_edges.append([str(e[0]), str(e[1])])

        if not subtasks:
            logger.warning("[task_splitter] LLM 返回空 subtasks，使用 fallback")
            return self._fallback(description)

        # 限制子任务数量
        if len(subtasks) > max_subtasks:
            subtasks = subtasks[:max_subtasks]
            # 同步过滤 edges，丢弃引用被截断节点的边
            valid_ids = {s.get("id") for s in subtasks}
            normalized_edges = [
                e for e in normalized_edges
                if e[0] in valid_ids and e[1] in valid_ids
            ]

        if not self._validate_dag(subtasks, normalized_edges):
            logger.warning("[task_splitter] DAG 验证失败，使用 fallback")
            return self._fallback(description)

        # 构造兼容 DAGScheduler 的 dag 字段
        dag = self._build_dag_dict(subtasks, normalized_edges)

        return {
            "subtasks": subtasks,
            "edges": normalized_edges,
            "dag": dag,
        }

    # ── prompt 构造 ───────────────────────────────────────────

    def _build_prompt(
        self,
        description: str,
        context: str,
        max_subtasks: int,
    ) -> str:
        """构造发送给 LLM 的 user prompt。"""
        parts: list[str] = [
            f"请将以下任务拆分为不超过 {max_subtasks} 个子任务，并生成 DAG 依赖图。",
            "",
            f"任务描述: {description}",
        ]
        if context and context.strip():
            parts.extend(["", f"上下文信息: {context}", ""])
        parts.extend([
            "",
            "请严格按指定 JSON 格式返回，不要包含 markdown 代码块标记或其他说明文字。",
        ])
        return "\n".join(parts)

    # ── 响应解析 ─────────────────────────────────────────────

    def _parse_response(self, response: str) -> dict[str, Any]:
        """解析 LLM 的 JSON 响应，容错处理 markdown code fence 包裹。

        支持以下格式:
          - 纯 JSON
          - ```json\\n{...}\\n``` （json code fence）
          - ```\\n{...}\\n``` （裸 code fence）

        Raises
        ------
        TaskSplitError
            当响应不是合法 JSON 时。
        """
        text = response.strip()
        if not text:
            raise TaskSplitError("LLM 响应为空")

        # 去除 markdown code fence
        # 匹配 ```json ... ``` 或 ``` ... ```
        fence_pattern = re.compile(r"^```(?:json)?\s*\n(.*?)\n```\s*$", re.DOTALL)
        match = fence_pattern.match(text)
        if match:
            text = match.group(1).strip()

        # 如果仍以 ``` 开头但没匹配到完整 fence，尝试更宽松的剥离
        if text.startswith("```"):
            lines = text.split("\n")
            # 去掉首行 ```xxx 和尾行 ```
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        # 尝试找到最外层的 { } 作为 JSON 边界（LLM 偶尔在 JSON 前后加解释文字）
        if not text.startswith("{"):
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                text = text[start : end + 1]

        try:
            result = json.loads(text)
        except json.JSONDecodeError as exc:
            raise TaskSplitError(f"LLM 响应不是合法 JSON: {exc}") from exc

        if not isinstance(result, dict):
            raise TaskSplitError("LLM 响应 JSON 顶层不是对象")

        return result

    # ── DAG 验证 ─────────────────────────────────────────────

    def _validate_dag(
        self,
        subtasks: list[dict[str, Any]],
        edges: list[list[str]],
    ) -> bool:
        """验证 DAG 合法性：依赖引用存在 + 无环。

        Returns
        -------
        bool
            合法返回 True，否则 False。
        """
        if not subtasks:
            return False

        # 收集所有节点 id
        node_ids: set[str] = set()
        for s in subtasks:
            sid = s.get("id")
            if not sid or not isinstance(sid, str):
                return False
            node_ids.add(sid)

        # 检查 edges 引用合法性
        for e in edges:
            if len(e) != 2:
                return False
            if e[0] not in node_ids or e[1] not in node_ids:
                return False

        # 检查 subtasks[i].depends_on 引用合法性
        for s in subtasks:
            deps = s.get("depends_on", [])
            if not isinstance(deps, list):
                return False
            for d in deps:
                if d not in node_ids:
                    return False

        # 拓扑排序验证无环
        try:
            self._topological_sort(subtasks, edges)
        except TaskSplitError:
            return False

        return True

    def _topological_sort(
        self,
        subtasks: list[dict[str, Any]],
        edges: list[list[str]],
    ) -> list[str]:
        """Kahn 算法拓扑排序，用于验证 DAG 无环。

        Returns
        -------
        list[str]
            拓扑顺序的节点 id 列表。

        Raises
        ------
        TaskSplitError
            当检测到环时。
        """
        node_ids = [s.get("id", "") for s in subtasks]
        # 构造邻接表 + 入度表
        adj: dict[str, list[str]] = {nid: [] for nid in node_ids}
        indegree: dict[str, int] = {nid: 0 for nid in node_ids}

        for e in edges:
            src, dst = e[0], e[1]
            if src in adj and dst in indegree:
                adj[src].append(dst)
                indegree[dst] += 1

        # 同时纳入 subtasks[i].depends_on
        for s in subtasks:
            sid = s.get("id", "")
            for dep in s.get("depends_on", []) or []:
                if dep in adj and sid in indegree:
                    adj[dep].append(sid)
                    indegree[sid] += 1

        # Kahn 算法
        from collections import deque
        queue: deque[str] = deque([nid for nid in node_ids if indegree[nid] == 0])
        sorted_ids: list[str] = []
        while queue:
            nid = queue.popleft()
            sorted_ids.append(nid)
            for neighbor in adj[nid]:
                indegree[neighbor] -= 1
                if indegree[neighbor] == 0:
                    queue.append(neighbor)

        if len(sorted_ids) != len(node_ids):
            raise TaskSplitError("DAG 存在循环依赖")

        return sorted_ids

    # ── 辅助方法 ─────────────────────────────────────────────

    def _resolve_provider(self) -> BaseLLMProvider | None:
        """解析可用的 LLM provider，失败返回 None。"""
        if self._llm_provider is not None:
            return self._llm_provider
        try:
            from maop.core.llm_provider import LLMProviderFactory
            if self._factory is None:
                self._factory = LLMProviderFactory()
            # 优先用指定 model，否则用 default_model
            model = self._model_name or self._factory._get_default_model()
            if not model:
                # 兜底：取第一个 enabled model
                models = self._factory.list_models(enabled_only=True)
                if not models:
                    return None
                model = models[0].name
            provider = self._factory.get_provider(model)
            if provider is not None and provider.is_configured:
                self._llm_provider = provider
                return provider
            return None
        except Exception as exc:
            logger.debug("[task_splitter] 解析 LLM provider 失败: %s", exc)
            return None

    def _fallback(self, description: str) -> dict[str, Any]:
        """LLM 不可用时的 fallback：返回单节点 DAG。

        返回结构与 :meth:`split` 正常路径完全一致，保证调用方无需特殊处理。
        """
        subtasks: list[dict[str, Any]] = [
            {
                "id": "t1",
                "name": description[:80] if description else "task",
                "description": description,
                "depends_on": [],
            }
        ]
        edges: list[list[str]] = []
        return {
            "subtasks": subtasks,
            "edges": edges,
            "dag": self._build_dag_dict(subtasks, edges),
        }

    @staticmethod
    def _build_dag_dict(
        subtasks: list[dict[str, Any]],
        edges: list[list[str]],
    ) -> dict[str, Any]:
        """构造兼容 :class:`DAGScheduler` 的 dag 字段。

        格式: ``{"nodes": [{"id": ..., "name": ..., "description": ..., "depends_on": [...]}, ...],
                "edges": [["a", "b"], ...]}``
        """
        nodes: list[dict[str, Any]] = []
        for s in subtasks:
            nodes.append({
                "id": s.get("id", ""),
                "name": s.get("name", ""),
                "description": s.get("description", ""),
                "depends_on": list(s.get("depends_on", []) or []),
            })
        return {"nodes": nodes, "edges": [list(e) for e in edges]}