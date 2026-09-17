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
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import knowledge_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

# P2-24: 统一使用 state.MAOP_ROOT，避免路径计算层数不一致
from .state import MAOP_ROOT  # noqa: E402


class ExtractRequest(BaseModel):
    text: str
    topic: str = ""
    source_exchange: str = ""


class VectorSearchRequest(BaseModel):
    query: str
    # M-3 fix: 添加值域约束。
    top: int = Field(default=10, ge=1, le=100)


# ── Knowledge Graph Endpoints ────────────────────────────────────

@router.get("/stats")
@handle_api_errors("knowledge stats")
async def knowledge_stats(request: Request) -> dict[str, Any]:
    """Get knowledge base statistics."""
    require_admin(request)
    return {"status": "ok", "data": knowledge_service.get_knowledge_stats()}


@router.get("/facts")
@handle_api_errors("knowledge facts")
async def query_facts(
    request: Request,
    subject: str = "",
    predicate: str = "",
    topic: str = "",
    top: int = Query(20, ge=1, le=1000),
) -> dict[str, Any]:
    """Query facts from the knowledge base."""
    require_admin(request)
    facts = knowledge_service.query_facts(subject=subject, predicate=predicate, topic=topic, top=top)
    return {"status": "ok", "data": facts}


@router.get("/entities/{name}")
@handle_api_errors("knowledge entity")
async def get_entity(request: Request, name: str) -> dict[str, Any]:
    """Get a specific entity by name."""
    require_admin(request)
    entity = knowledge_service.get_entity(name)
    if entity:
        return {"status": "ok", "data": entity}
    raise HTTPException(status_code=404, detail="Entity not found")


@router.get("/relations")
@handle_api_errors("knowledge relations")
async def query_relations(
    request: Request,
    source: str = "",
    target: str = "",
    relation_type: str = "",
    top: int = Query(20, ge=1, le=1000),
) -> dict[str, Any]:
    """Query relations from the knowledge base."""
    require_admin(request)
    relations = knowledge_service.query_relations(
        source=source, target=target, relation_type=relation_type, top=top
    )
    return {"status": "ok", "data": relations}


@router.get("/graph")
@handle_api_errors("knowledge graph")
async def get_graph(
    request: Request,
    center: str = "",
    topic: str = "",
    max_nodes: int = Query(50, ge=1, le=10000),
) -> dict[str, Any]:
    """Get graph data for visualization."""
    require_admin(request)
    data = knowledge_service.get_graph(center=center, topic=topic, max_nodes=max_nodes)
    return {"status": "ok", "data": data}


@router.get("/context")
@handle_api_errors("knowledge context")
async def build_context(
    request: Request,
    entity: str = "",
    max_depth: int = Query(2, ge=1, le=10),
) -> dict[str, Any]:
    """Build LLM context for an entity from the knowledge graph."""
    require_admin(request)
    context = knowledge_service.build_context(entity, max_depth=max_depth)
    return {"status": "ok", "data": {"entity": entity, "context": context}}


@router.post("/extract")
@handle_api_errors("knowledge extract")
async def extract_knowledge(request_body: ExtractRequest, request: Request) -> dict[str, Any]:
    """Extract knowledge from text and store to the knowledge base."""
    require_admin(request)
    counts = knowledge_service.extract_knowledge(
        request_body.text,
        topic=request_body.topic,
        source_exchange=request_body.source_exchange,
    )
    return {"status": "ok", "data": counts}


# ── Vector Search Endpoints ──────────────────────────────────────

@router.get("/vector/stats")
@handle_api_errors("vector stats")
async def vector_stats(request: Request) -> dict[str, Any]:
    """Get vector search statistics."""
    require_admin(request)
    return {"status": "ok", "data": knowledge_service.get_vector_stats()}


@router.post("/vector/search")
@handle_api_errors("vector search")
async def vector_search(request_body: VectorSearchRequest, request: Request) -> dict[str, Any]:
    """Perform semantic vector search."""
    require_admin(request)
    results = knowledge_service.vector_search(request_body.query, top=request_body.top)
    return {"status": "ok", "data": results}


@router.post("/vector/index")
@handle_api_errors("vector index")
async def vector_index(request: Request) -> dict[str, Any]:
    """Trigger vector indexing of all memory entries."""
    require_admin(request)
    data = knowledge_service.vector_index()
    return {"status": "ok", "data": data}


# ── Knowledge Graph v2 router (/api/knowledge-graph) ──────────────

kg_router = APIRouter(prefix="/api/knowledge-graph", tags=["knowledge-graph"])


@kg_router.get("")
@handle_api_errors("knowledge graph v2")
async def get_knowledge_graph_v2(
    request: Request,
    limit: int = Query(500, ge=1, le=1000),
    type: str = Query(""),  # shadows builtin intentionally for API
    time_range: str = Query(""),
) -> dict[str, Any]:
    """Return the full knowledge graph with optional type/time/limit filtering.

    Query params (spec 6.4):
      - limit: 1–10000 (default 500)
      - type: comma-separated node types to filter (e.g. "agent,task")
      - time_range: "start,end" ISO-8601 lexicographic comparison
    """
    require_admin(request)
    # ── Parameter validation ──
    # P2-5: limit 范围已由 Query(ge=1, le=1000) 约束，移除冗余的手动检查
    if time_range:
        parts = time_range.split(",")
        if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
            raise HTTPException(status_code=400, detail="Invalid time_range format")
        start, end = parts[0].strip(), parts[1].strip()
        if start > end:
            raise HTTPException(status_code=400, detail="time_range start must be <= end")

    data = knowledge_service.query_knowledge_graph_v2(
        limit=limit, type=type, time_range=time_range
    )
    return {"status": "ok", "data": data}
