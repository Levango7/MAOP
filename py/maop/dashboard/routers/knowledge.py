"""MAOP Dashboard — Knowledge & Vector Search API.

Endpoints:
  GET  /api/knowledge/stats       — Knowledge base statistics
  GET  /api/knowledge/facts        — Query facts
  GET  /api/knowledge/entities     — Query entities
  GET  /api/knowledge/relations    — Query relations
  GET  /api/knowledge/graph        — Get subgraph for visualization
  GET  /api/knowledge/context      — Build LLM context for an entity
  POST /api/knowledge/extract      — Extract knowledge from text
  GET  /api/knowledge/vector/stats — Vector search statistics
  POST /api/knowledge/vector/search — Semantic vector search
  POST /api/knowledge/vector/index — Trigger vector indexing
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

MAOP_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent


class ExtractRequest(BaseModel):
    text: str
    topic: str = ""
    source_exchange: str = ""


class VectorSearchRequest(BaseModel):
    query: str
    top: int = 10


# ── Knowledge Graph Endpoints ────────────────────────────────────

@router.get("/stats")
@handle_api_errors("knowledge stats")
async def knowledge_stats() -> dict[str, Any]:
    """Get knowledge base statistics."""
    from maop.core.memory.knowledge_extractor import KnowledgeExtractor
    ext = KnowledgeExtractor(root_dir=str(MAOP_ROOT))
    return {"status": "ok", "data": ext.stats()}


@router.get("/facts")
@handle_api_errors("knowledge facts")
async def query_facts(
    subject: str = "",
    predicate: str = "",
    topic: str = "",
    top: int = 20,
) -> dict[str, Any]:
    """Query facts from the knowledge base."""
    from maop.core.memory.knowledge_extractor import KnowledgeExtractor
    ext = KnowledgeExtractor(root_dir=str(MAOP_ROOT))
    facts = ext.query_facts(subject=subject, predicate=predicate, topic=topic, top=top)
    return {"status": "ok", "data": [f.model_dump() for f in facts]}


@router.get("/entities/{name}")
@handle_api_errors("knowledge entity")
async def get_entity(name: str) -> dict[str, Any]:
    """Get a specific entity by name."""
    from maop.core.memory.knowledge_extractor import KnowledgeExtractor
    ext = KnowledgeExtractor(root_dir=str(MAOP_ROOT))
    entity = ext.get_entity(name)
    if entity:
        return {"status": "ok", "data": entity.model_dump()}
    return {"status": "not_found", "data": None}


@router.get("/relations")
@handle_api_errors("knowledge relations")
async def query_relations(
    source: str = "",
    target: str = "",
    relation_type: str = "",
    top: int = 20,
) -> dict[str, Any]:
    """Query relations from the knowledge base."""
    from maop.core.memory.knowledge_extractor import KnowledgeExtractor
    ext = KnowledgeExtractor(root_dir=str(MAOP_ROOT))
    relations = ext.query_relations(source=source, target=target, relation_type=relation_type, top=top)
    return {"status": "ok", "data": [r.model_dump() for r in relations]}


@router.get("/graph")
@handle_api_errors("knowledge graph")
async def get_graph(
    center: str = "",
    topic: str = "",
    max_nodes: int = 50,
) -> dict[str, Any]:
    """Get graph data for visualization."""
    from maop.core.memory.knowledge_graph import KnowledgeGraph
    kg = KnowledgeGraph(root_dir=str(MAOP_ROOT))
    if center:
        subgraph = kg.get_neighbors(center, max_depth=2)
        return {"status": "ok", "data": subgraph.model_dump()}
    if topic:
        subgraph = kg.get_subgraph_by_topic(topic, max_nodes=max_nodes)
        return {"status": "ok", "data": subgraph.model_dump()}
    data = kg.export_for_visualization(max_nodes=max_nodes)
    return {"status": "ok", "data": data}


@router.get("/context")
@handle_api_errors("knowledge context")
async def build_context(
    entity: str = "",
    max_depth: int = 2,
) -> dict[str, Any]:
    """Build LLM context for an entity from the knowledge graph."""
    from maop.core.memory.knowledge_graph import KnowledgeGraph
    kg = KnowledgeGraph(root_dir=str(MAOP_ROOT))
    context = kg.build_context(entity, max_depth=max_depth)
    return {"status": "ok", "data": {"entity": entity, "context": context}}


@router.post("/extract")
@handle_api_errors("knowledge extract")
async def extract_knowledge(request_body: ExtractRequest, request: Request) -> dict[str, Any]:
    """Extract knowledge from text and store to the knowledge base."""
    require_admin(request)
    from maop.core.memory.knowledge_extractor import KnowledgeExtractor
    ext = KnowledgeExtractor(root_dir=str(MAOP_ROOT))
    result = ext.extract_from_text(
        request_body.text,
        source_exchange=request_body.source_exchange,
        topic=request_body.topic,
    )
    counts = ext.store_extraction(result)
    return {"status": "ok", "data": counts}


# ── Vector Search Endpoints ──────────────────────────────────────

@router.get("/vector/stats")
@handle_api_errors("vector stats")
async def vector_stats() -> dict[str, Any]:
    """Get vector search statistics."""
    from maop.memory.vector_search import VectorSearch
    vs = VectorSearch(root_dir=str(MAOP_ROOT))
    return {"status": "ok", "data": vs.stats()}


@router.post("/vector/search")
@handle_api_errors("vector search")
async def vector_search(request_body: VectorSearchRequest, request: Request) -> dict[str, Any]:
    """Perform semantic vector search."""
    require_admin(request)
    from maop.memory.vector_search import VectorSearch
    vs = VectorSearch(root_dir=str(MAOP_ROOT))
    results = vs.search(request_body.query, top=request_body.top)
    return {"status": "ok", "data": [r.model_dump() for r in results]}


@router.post("/vector/index")
@handle_api_errors("vector index")
async def vector_index(request: Request) -> dict[str, Any]:
    """Trigger vector indexing of all memory entries."""
    require_admin(request)
    from maop.memory.vector_search import VectorSearch
    vs = VectorSearch(root_dir=str(MAOP_ROOT))
    count = vs.index_all()
    return {"status": "ok", "data": {"indexed": count, "is_semantic": vs.is_semantic}}


# ── Knowledge Graph v2 router (/api/knowledge-graph) ──────────────

kg_router = APIRouter(prefix="/api/knowledge-graph", tags=["knowledge-graph"])


@kg_router.get("")
async def get_knowledge_graph_v2(
    limit: int = Query(500),
    type: str = Query(""),  # shadows builtin intentionally for API
    time_range: str = Query(""),
) -> dict[str, Any]:
    """Return the full knowledge graph with optional type/time/limit filtering.

    Query params (spec 6.4):
      - limit: 1–10000 (default 500)
      - type: comma-separated node types to filter (e.g. "agent,task")
      - time_range: "start,end" ISO-8601 lexicographic comparison
    """
    # ── Parameter validation ──
    if limit < 1 or limit > 10000:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 10000")
    if time_range:
        parts = time_range.split(",")
        if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
            raise HTTPException(status_code=400, detail="Invalid time_range format")
        start, end = parts[0].strip(), parts[1].strip()
        if start > end:
            raise HTTPException(status_code=400, detail="time_range start must be <= end")

    from maop.core.memory.knowledge_graph import KnowledgeGraph, KnowledgeGraphQuery

    kg = KnowledgeGraph(root_dir=str(MAOP_ROOT))
    query = KnowledgeGraphQuery(type=type, time_range=time_range, limit=limit)
    response = kg.query_graph(query)
    data = response.model_dump()
    data["stats"] = {
        "node_count": len(response.nodes),
        "edge_count": len(response.edges),
    }
    return {"status": "ok", "data": data}
