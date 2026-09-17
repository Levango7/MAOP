"""Knowledge graph and vector search business logic for MAOP Dashboard.

Encapsulates knowledge extraction, graph queries, and vector search
operations. Extracted from the knowledge router so the router layer
only does parameter parsing + service call + response.

The service is framework-agnostic: it does not import FastAPI and can be
unit-tested or invoked from CLI/CI without an HTTP context.
``MAOP_ROOT`` is resolved from the dashboard state module to stay
consistent with the rest of the dashboard package.
"""

from __future__ import annotations

import logging
from typing import Any

from maop.dashboard.routers.state import MAOP_ROOT

logger = logging.getLogger(__name__)


# ── Knowledge Graph ────────────────────────────────────────────────


def get_knowledge_stats() -> dict[str, Any]:
    """获取知识库统计信息。"""
    from maop.core.memory.knowledge_extractor import KnowledgeExtractor

    ext = KnowledgeExtractor(root_dir=str(MAOP_ROOT))
    return ext.stats()


def query_facts(
    subject: str = "",
    predicate: str = "",
    topic: str = "",
    top: int = 20,
) -> list[dict[str, Any]]:
    """查询知识库中的事实，返回 dict 列表。"""
    from maop.core.memory.knowledge_extractor import KnowledgeExtractor

    ext = KnowledgeExtractor(root_dir=str(MAOP_ROOT))
    facts = ext.query_facts(subject=subject, predicate=predicate, topic=topic, top=top)
    return [f.model_dump() for f in facts]


def get_entity(name: str) -> dict[str, Any] | None:
    """按名称获取实体，返回 dict 或 None。"""
    from maop.core.memory.knowledge_extractor import KnowledgeExtractor

    ext = KnowledgeExtractor(root_dir=str(MAOP_ROOT))
    entity = ext.get_entity(name)
    if entity:
        return entity.model_dump()
    return None


def query_relations(
    source: str = "",
    target: str = "",
    relation_type: str = "",
    top: int = 20,
) -> list[dict[str, Any]]:
    """查询知识库中的关系，返回 dict 列表。"""
    from maop.core.memory.knowledge_extractor import KnowledgeExtractor

    ext = KnowledgeExtractor(root_dir=str(MAOP_ROOT))
    relations = ext.query_relations(
        source=source, target=target, relation_type=relation_type, top=top
    )
    return [r.model_dump() for r in relations]


def get_graph(center: str = "", topic: str = "", max_nodes: int = 50) -> dict[str, Any]:
    """获取可视化图数据。

    - 若提供 center，返回 center 的邻居子图。
    - 若提供 topic，返回按 topic 过滤的子图。
    - 否则返回全图导出。
    """
    from maop.core.memory.knowledge_graph import KnowledgeGraph

    kg = KnowledgeGraph(root_dir=str(MAOP_ROOT))
    if center:
        subgraph = kg.get_neighbors(center, max_depth=2)
        return subgraph.model_dump()
    if topic:
        subgraph = kg.get_subgraph_by_topic(topic, max_nodes=max_nodes)
        return subgraph.model_dump()
    return kg.export_for_visualization(max_nodes=max_nodes)


def build_context(entity: str, max_depth: int = 2) -> str:
    """为实体构建 LLM 上下文。"""
    from maop.core.memory.knowledge_graph import KnowledgeGraph

    kg = KnowledgeGraph(root_dir=str(MAOP_ROOT))
    return kg.build_context(entity, max_depth=max_depth)


def extract_knowledge(
    text: str, topic: str = "", source_exchange: str = ""
) -> dict[str, Any]:
    """从文本中提取知识并存入知识库，返回计数 dict。"""
    from maop.core.memory.knowledge_extractor import KnowledgeExtractor

    ext = KnowledgeExtractor(root_dir=str(MAOP_ROOT))
    result = ext.extract_from_text(
        text, source_exchange=source_exchange, topic=topic
    )
    return ext.store_extraction(result)


# ── Vector Search ──────────────────────────────────────────────────


def get_vector_stats() -> dict[str, Any]:
    """获取向量搜索统计信息。"""
    from maop.memory.vector_search import VectorSearch

    vs = VectorSearch(root_dir=str(MAOP_ROOT))
    return vs.stats()


def vector_search(query: str, top: int = 10) -> list[dict[str, Any]]:
    """执行语义向量搜索，返回 dict 列表。"""
    from maop.memory.vector_search import VectorSearch

    vs = VectorSearch(root_dir=str(MAOP_ROOT))
    results = vs.search(query, top=top)
    return [r.model_dump() for r in results]


def vector_index() -> dict[str, Any]:
    """触发所有记忆条目的向量索引。

    返回 ``{"indexed": count, "is_semantic": bool}``。
    """
    from maop.memory.vector_search import VectorSearch

    vs = VectorSearch(root_dir=str(MAOP_ROOT))
    count = vs.index_all()
    return {"indexed": count, "is_semantic": vs.is_semantic}


# ── Knowledge Graph v2 ─────────────────────────────────────────────


def query_knowledge_graph_v2(
    limit: int = 500, type: str = "", time_range: str = ""
) -> dict[str, Any]:
    """查询知识图 v2，返回含 stats 的 data dict。"""
    from maop.core.memory.knowledge_graph import KnowledgeGraph, KnowledgeGraphQuery

    kg = KnowledgeGraph(root_dir=str(MAOP_ROOT))
    query = KnowledgeGraphQuery(type=type, time_range=time_range, limit=limit)
    response = kg.query_graph(query)
    data = response.model_dump()
    data["stats"] = {
        "node_count": len(response.nodes),
        "edge_count": len(response.edges),
    }
    return data