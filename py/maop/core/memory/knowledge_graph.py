"""MAOP Knowledge Graph — Entity-Relation graph with traversal and inference.

Built on top of KnowledgeExtractor's SQLite tables, provides:
  - Graph traversal (neighbors, paths, subgraphs)
  - Inference (transitive closure for "uses", "depends_on", "extends")
  - Context assembly for LLM injection
  - Visualization data export
  - v4.5.0: query_graph — type/time_range/limit filtered query for visualization

Usage::

    from maop.core.memory.knowledge_graph import KnowledgeGraph

    kg = KnowledgeGraph(root_dir="/path/to/MAOP")
    neighbors = kg.get_neighbors("AuthService")
    context = kg.build_context("AuthService", max_depth=2)

    # v4.5.0: filtered query for the /api/knowledge-graph endpoint
    from maop.core.memory.knowledge_graph import KnowledgeGraphQuery
    result = kg.query_graph(KnowledgeGraphQuery(type="agent", limit=200))
"""

from __future__ import annotations

import json as _json
import logging
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path, sqlite_connect, validate_identifier

logger = logging.getLogger(__name__)


class GraphNode(BaseModel):
    name: str = ""
    entity_type: str = "concept"
    attributes: dict[str, str] = Field(default_factory=dict)
    in_degree: int = 0
    out_degree: int = 0


class GraphEdge(BaseModel):
    source: str = ""
    target: str = ""
    relation_type: str = "related_to"
    context: str = ""
    confidence: float = 1.0


class Subgraph(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    center: str = ""


# ── v4.5.0: Visualization-oriented models (align with spec 6.2/6.3/6.4) ──

class GraphNodeV2(BaseModel):
    """Knowledge graph node for visualization (spec 6.2).

    Fields align with the spec-defined schema and are forward-compatible:
    existing entities table rows (name/entity_type/attributes/confidence)
    map onto these fields without schema migration.
    """
    id: str
    type: str = "concept"           # agent | task | memory | concept
    label: str = ""
    timestamp: str = ""             # ISO 8601 or empty when unavailable
    properties: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0


class GraphEdgeV2(BaseModel):
    """Knowledge graph edge for visualization (spec 6.3)."""
    id: str
    source: str
    target: str
    type: str = "related_to"        # delegates | remembers | produces | depends_on
    timestamp: str = ""
    properties: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0


class KnowledgeGraphQuery(BaseModel):
    """Query parameters for KnowledgeGraph.query_graph (spec 6.4).

    ``type`` accepts a comma-separated string ("agent,task") or a list
    of type names. ``time_range`` is "start,end" where each value is
    either an ISO 8601 timestamp or a Unix timestamp string; comparison
    is lexicographic on the canonical string form.
    """
    type: str | list[str] = ""
    time_range: str = ""            # "start,end"
    limit: int = 500

    def parse_types(self) -> list[str]:
        """Return the list of node types to filter by (empty = no filter)."""
        if isinstance(self.type, list):
            return [t.strip() for t in self.type if t and t.strip()]
        if not self.type:
            return []
        return [t.strip() for t in self.type.split(",") if t.strip()]

    def parse_time_range(self) -> tuple[str, str] | None:
        """Return (start, end) for time filtering, or None when unset/invalid."""
        if not self.time_range:
            return None
        parts = self.time_range.split(",")
        if len(parts) != 2:
            return None
        start, end = parts[0].strip(), parts[1].strip()
        if not start or not end:
            return None
        return start, end


class KnowledgeGraphResponse(BaseModel):
    """Response wrapper for KnowledgeGraph.query_graph."""
    nodes: list[GraphNodeV2] = Field(default_factory=list)
    edges: list[GraphEdgeV2] = Field(default_factory=list)


class KnowledgeGraph:
    """Entity-Relation knowledge graph with traversal capabilities.

    Reads from the same SQLite database as KnowledgeExtractor.
    Provides graph operations for context assembly and inference.
    """

    TRANSITIVE_TYPES: ClassVar[set[str]] = {"uses", "depends_on", "extends", "implements", "imports"}

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._db_path = get_db_path("knowledge_graph")

    def get_neighbors(
        self,
        entity_name: str,
        *,
        direction: str = "both",
        relation_type: str = "",
        max_depth: int = 1,
    ) -> Subgraph:
        """Get neighboring nodes and edges for an entity.

        Parameters
        ----------
        direction : str
            "outgoing", "incoming", or "both"
        max_depth : int
            Traversal depth (1 = direct neighbors only)
        """
        visited_nodes: set[str] = {entity_name}
        all_nodes: list[GraphNode] = []
        all_edges: list[GraphEdge] = []

        current_frontier = {entity_name}

        for _depth in range(max_depth):
            next_frontier: set[str] = set()
            for node_name in current_frontier:
                edges = self._get_edges_for(node_name, direction=direction, relation_type=relation_type)
                for edge in edges:
                    other = edge.target if edge.source == node_name else edge.source
                    all_edges.append(edge)
                    if other not in visited_nodes:
                        visited_nodes.add(other)
                        next_frontier.add(other)
                        node = self._get_node(other)
                        if node:
                            all_nodes.append(node)
            current_frontier = next_frontier
            if not current_frontier:
                break

        center_node = self._get_node(entity_name)
        if center_node:
            all_nodes.insert(0, center_node)

        return Subgraph(nodes=all_nodes, edges=all_edges, center=entity_name)

    def find_path(
        self,
        source: str,
        target: str,
        max_depth: int = 5,
    ) -> list[GraphEdge]:
        """Find a path between two entities using BFS."""
        if source == target:
            return []

        visited: set[str] = {source}
        queue: list[tuple[str, list[GraphEdge]]] = [(source, [])]

        while queue:
            current, path = queue.pop(0)
            if len(path) >= max_depth:
                continue

            edges = self._get_edges_for(current, direction="outgoing")
            for edge in edges:
                neighbor = edge.target
                new_path = path + [edge]
                if neighbor == target:
                    return new_path
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, new_path))

        return []

    def build_context(
        self,
        entity_name: str,
        *,
        max_depth: int = 2,
        max_facts: int = 10,
    ) -> str:
        """Build a natural language context string for LLM injection.

        Traverses the graph from entity_name and assembles:
          1. Direct facts about the entity
          2. Related entities and their relationships
          3. Inferred transitive relationships
        """
        parts: list[str] = []

        subgraph = self.get_neighbors(entity_name, max_depth=max_depth)
        facts = self._get_facts_for(entity_name, limit=max_facts)

        if facts:
            parts.append(f"[Knowledge about {entity_name}]")
            for f in facts:
                parts.append(f"  - {f['subject']} {f['predicate']} {f['object_value']}")

        if subgraph.edges:
            parts.append(f"[Relationships of {entity_name}]")
            for edge in subgraph.edges[:15]:
                parts.append(f"  - {edge.source} --[{edge.relation_type}]--> {edge.target}")

        inferred = self._infer_transitive(entity_name)
        if inferred:
            parts.append(f"[Inferred dependencies of {entity_name}]")
            for item in inferred[:5]:
                parts.append(f"  - {item}")

        return "\n".join(parts) if parts else ""

    @staticmethod
    def _validate_table(name: str) -> None:
        validate_identifier(name, "table")

    def get_subgraph_by_topic(self, topic: str, max_nodes: int = 50) -> Subgraph:
        """Get a subgraph of all entities related to a topic."""
        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []

        self._validate_table("facts")
        self._validate_table("relations")
        with sqlite_connect(self._db_path) as conn:
            try:
                fact_entities = conn.execute(
                    "SELECT DISTINCT subject FROM facts WHERE topic = ? LIMIT ?",
                    (topic, max_nodes),
                ).fetchall()
                entity_names = [r["subject"] for r in fact_entities]

                for name in entity_names[:max_nodes]:
                    node = self._get_node(name)
                    if node:
                        nodes.append(node)

                if entity_names:
                    placeholders = ",".join("?" * len(entity_names))
                    rel_rows = conn.execute(
                        f"SELECT * FROM relations WHERE source IN ({placeholders}) OR target IN ({placeholders}) LIMIT ?",
                        entity_names + entity_names + [max_nodes * 2],
                    ).fetchall()
                    for r in rel_rows:
                        edges.append(GraphEdge(
                            source=r["source"], target=r["target"],
                            relation_type=r["relation_type"],
                            context=r["context"], confidence=r["confidence"],
                        ))
            except Exception as exc:
                logger.debug("[kg] Subgraph query failed: %s", exc)

        return Subgraph(nodes=nodes, edges=edges, center=topic)

    def export_for_visualization(self, center: str = "", max_nodes: int = 100) -> dict[str, Any]:
        """Export graph data in a format suitable for D3.js/Cytoscape visualization."""
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []

        self._validate_table("entities")
        self._validate_table("relations")
        with sqlite_connect(self._db_path) as conn:
            try:
                entity_rows = conn.execute(
                    "SELECT * FROM entities ORDER BY confidence DESC LIMIT ?", (max_nodes,),
                ).fetchall()
                for r in entity_rows:
                    import json
                    nodes.append({
                        "id": r["name"],
                        "type": r["entity_type"],
                        "attributes": json.loads(r["attributes"]) if r["attributes"] else {},
                        "confidence": r["confidence"],
                    })

                rel_rows = conn.execute(
                    "SELECT * FROM relations ORDER BY confidence DESC LIMIT ?", (max_nodes * 2,),
                ).fetchall()
                for r in rel_rows:
                    edges.append({
                        "source": r["source"],
                        "target": r["target"],
                        "type": r["relation_type"],
                        "context": r["context"],
                        "confidence": r["confidence"],
                    })
            except Exception as exc:
                logger.debug("[kg] Export failed: %s", exc)

        return {"nodes": nodes, "edges": edges}

    # ── v4.5.0: Filtered query for /api/knowledge-graph ─────────────

    def query_graph(self, query: KnowledgeGraphQuery) -> KnowledgeGraphResponse:
        """Query graph with type/time_range/limit filters (v4.5.0).

        Maps existing ``entities``/``relations``/``facts`` tables onto
        ``GraphNodeV2``/``GraphEdgeV2`` without schema changes. Node
        ``timestamp`` is derived from the earliest ``facts.created_at``
        for that subject (empty string when no facts exist). Edge
        ``timestamp`` is empty (relations table has no created_at).

        Parameters
        ----------
        query : KnowledgeGraphQuery
            Filters: ``type`` (node-type whitelist), ``time_range``
            (start,end lexicographic compare), ``limit`` (max nodes).

        Returns
        -------
        KnowledgeGraphResponse
            ``nodes`` sorted by confidence DESC; ``edges`` limited to
            those whose both endpoints appear in ``nodes`` (spec 5.3.1
            rule 13).
        """
        types = query.parse_types()
        time_range = query.parse_time_range()
        limit = max(0, min(int(query.limit), 5000))  # hard cap 5000

        nodes: list[GraphNodeV2] = []
        edges: list[GraphEdgeV2] = []

        self._validate_table("entities")
        self._validate_table("relations")
        self._validate_table("facts")

        with sqlite_connect(self._db_path) as conn:
            try:
                # ── Node query with type/time filters ──
                # timestamp comes from the earliest facts.created_at for
                # the entity (LEFT JOIN + MIN aggregate). When the
                # entities table has no timestamp column this is the
                # best available proxy; entities without facts get "".
                conditions: list[str] = []
                params: list[Any] = []

                if types:
                    placeholders = ",".join("?" * len(types))
                    conditions.append(f"e.entity_type IN ({placeholders})")
                    params.extend(types)

                if time_range:
                    start, end = time_range
                    # Filter on the derived timestamp (facts.created_at).
                    # Use a HAVING clause on the aggregated column.
                    conditions.append("ts.min_created >= ?")
                    params.append(start)
                    conditions.append("ts.min_created <= ?")
                    params.append(end)

                where = " AND ".join(conditions) if conditions else "1=1"

                node_rows = conn.execute(
                    f"""
                    SELECT e.name           AS name,
                           e.entity_type    AS entity_type,
                           e.attributes     AS attributes,
                           e.confidence     AS confidence,
                           ts.min_created   AS timestamp
                    FROM entities AS e
                    LEFT JOIN (
                        SELECT subject, MIN(created_at) AS min_created
                        FROM facts
                        GROUP BY subject
                    ) AS ts ON ts.subject = e.name
                    WHERE {where}
                    ORDER BY e.confidence DESC
                    LIMIT ?
                    """,
                    params + [limit],
                ).fetchall()

                node_ids: set[str] = set()
                for r in node_rows:
                    name = r["name"]
                    try:
                        attrs = _json.loads(r["attributes"]) if r["attributes"] else {}
                    except (ValueError, TypeError):
                        attrs = {}
                    nodes.append(GraphNodeV2(
                        id=name,
                        type=r["entity_type"] or "concept",
                        label=name,
                        timestamp=r["timestamp"] or "",
                        properties=attrs,
                        confidence=float(r["confidence"] if r["confidence"] is not None else 1.0),
                    ))
                    node_ids.add(name)

                # ── Edge query: both endpoints must be in node_ids ──
                if node_ids:
                    id_list = list(node_ids)
                    placeholders = ",".join("?" * len(id_list))
                    edge_rows = conn.execute(
                        f"""
                        SELECT id, source, target, relation_type, context, confidence
                        FROM relations
                        WHERE source IN ({placeholders})
                          AND target IN ({placeholders})
                        ORDER BY confidence DESC
                        LIMIT ?
                        """,
                        id_list + id_list + [limit * 2],
                    ).fetchall()
                    for r in edge_rows:
                        src, tgt = r["source"], r["target"]
                        edges.append(GraphEdgeV2(
                            id=r["id"] or f"{src}->{tgt}",
                            source=src,
                            target=tgt,
                            type=r["relation_type"] or "related_to",
                            timestamp="",
                            properties={"context": r["context"]} if r["context"] else {},
                            confidence=float(r["confidence"] if r["confidence"] is not None else 1.0),
                        ))
            except Exception as exc:
                logger.debug("[kg] query_graph failed: %s", exc, exc_info=True)

        return KnowledgeGraphResponse(nodes=nodes, edges=edges)

    def _get_edges_for(
        self,
        entity_name: str,
        *,
        direction: str = "both",
        relation_type: str = "",
    ) -> list[GraphEdge]:
        """Get edges connected to an entity."""
        conditions = []
        params: list[Any] = []

        if direction in ("outgoing", "both"):
            conditions.append("source = ?")
            params.append(entity_name)
        if direction in ("incoming", "both"):
            if conditions:
                conditions.append("target = ?")
            else:
                conditions.append("target = ?")
            params.append(entity_name)

        where = " OR ".join(conditions) if len(conditions) > 1 else conditions[0] if conditions else "1=1"

        if relation_type:
            where += " AND relation_type = ?"
            params.append(relation_type)

        self._validate_table("relations")
        with sqlite_connect(self._db_path) as conn:
            try:
                rows = conn.execute(
                    f"SELECT * FROM relations WHERE {where} ORDER BY confidence DESC LIMIT 50",
                    params,
                ).fetchall()
            except Exception as exc:
                logger.warning(
                    "[knowledge_graph] Failed to query relations, returning empty: %s",
                    exc, exc_info=True,
                )
                return []

        return [GraphEdge(
            source=r["source"], target=r["target"],
            relation_type=r["relation_type"],
            context=r["context"], confidence=r["confidence"],
        ) for r in rows]

    def _get_node(self, name: str) -> GraphNode | None:
        """Get a graph node with degree counts."""
        self._validate_table("entities")
        self._validate_table("relations")
        with sqlite_connect(self._db_path) as conn:
            try:
                row = conn.execute(
                    "SELECT * FROM entities WHERE name = ?", (name,),
                ).fetchone()
                if not row:
                    in_deg = conn.execute(
                        "SELECT COUNT(*) FROM relations WHERE target = ?", (name,),
                    ).fetchone()[0]
                    out_deg = conn.execute(
                        "SELECT COUNT(*) FROM relations WHERE source = ?", (name,),
                    ).fetchone()[0]
                    return GraphNode(name=name, in_degree=in_deg, out_degree=out_deg)

                import json
                in_deg = conn.execute(
                    "SELECT COUNT(*) FROM relations WHERE target = ?", (name,),
                ).fetchone()[0]
                out_deg = conn.execute(
                    "SELECT COUNT(*) FROM relations WHERE source = ?", (name,),
                ).fetchone()[0]
                return GraphNode(
                    name=row["name"], entity_type=row["entity_type"],
                    attributes=json.loads(row["attributes"]) if row["attributes"] else {},
                    in_degree=in_deg, out_degree=out_deg,
                )
            except Exception as exc:
                logger.warning(
                    "[knowledge_graph] Failed to get node %s, returning None: %s",
                    name, exc, exc_info=True,
                )
                return None

    def _get_facts_for(self, entity_name: str, limit: int = 10) -> list[dict[str, Any]]:
        """Get facts about an entity."""
        self._validate_table("facts")
        with sqlite_connect(self._db_path) as conn:
            try:
                rows = conn.execute(
                    "SELECT * FROM facts WHERE subject LIKE ? ORDER BY confidence DESC LIMIT ?",
                    (f"%{entity_name}%", limit),
                ).fetchall()
            except Exception as exc:
                logger.warning(
                    "[knowledge_graph] Failed to query facts for %s, returning empty: %s",
                    entity_name, exc, exc_info=True,
                )
                return []
        return [dict(r) for r in rows]

    def _infer_transitive(self, entity_name: str) -> list[str]:
        """Infer transitive relationships (e.g., A uses B, B uses C → A transitively uses C)."""
        inferred: list[str] = []
        visited: set[str] = {entity_name}

        def _traverse(current: str, path: list[str], depth: int) -> None:
            if depth > 3:
                return
            edges = self._get_edges_for(current, direction="outgoing")
            for edge in edges:
                if edge.relation_type not in self.TRANSITIVE_TYPES:
                    continue
                if edge.target in visited:
                    continue
                visited.add(edge.target)
                new_path = path + [f"--[{edge.relation_type}]--> {edge.target}"]
                inferred.append(f"{entity_name} {' '.join(new_path)}")
                _traverse(edge.target, new_path, depth + 1)

        _traverse(entity_name, [], 0)
        return inferred
