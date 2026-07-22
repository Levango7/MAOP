"""MAOP Knowledge Extractor — Extract structured knowledge from conversations.

Bridges the gap between DreamConsolidator (which only compresses) and
true long-term memory (which should store reusable knowledge).

Pipeline:
  1. Ingest conversation exchanges (Q&A pairs)
  2. Extract entities, relations, and facts via pattern matching + LLM
  3. Store extracted knowledge to KnowledgeGraph for cross-session retrieval

Usage::

    from maop.core.knowledge_extractor import KnowledgeExtractor

    ext = KnowledgeExtractor(root_dir="/path/to/MAOP")
    facts = ext.extract_from_text("The auth module uses JWT tokens with 24h expiry")
    ext.store_facts(facts)
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


class Entity(BaseModel):
    name: str = ""
    entity_type: str = "concept"
    attributes: dict[str, str] = Field(default_factory=dict)
    confidence: float = 1.0


class Relation(BaseModel):
    source: str = ""
    target: str = ""
    relation_type: str = "related_to"
    context: str = ""
    confidence: float = 1.0


class Fact(BaseModel):
    id: str = ""
    subject: str = ""
    predicate: str = ""
    object_value: str = ""
    source_exchange: str = ""
    topic: str = ""
    confidence: float = 1.0
    created_at: str = ""
    access_count: int = 0


class ExtractionResult(BaseModel):
    entities: list[Entity] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)


class KnowledgeExtractor:
    """Extract structured knowledge from text using pattern matching.

    Pattern-based extraction covers common programming/project knowledge:
      - "X uses Y" → dependency relation
      - "X is Y" → type/classification fact
      - "X has Y" → composition relation
      - "X does Y" → capability fact
      - File paths, class names, function names
      - Configuration values (key=value patterns)
    """

    _RELATION_PATTERNS: list[tuple[str, str]] = [
        (r"(\w[\w.\-]+)\s+uses?\s+(\w[\w.\-]+)", "uses"),
        (r"(\w[\w.\-]+)\s+depends?\s+on\s+(\w[\w.\-]+)", "depends_on"),
        (r"(\w[\w.\-]+)\s+extends?\s+(\w[\w.\-]+)", "extends"),
        (r"(\w[\w.\-]+)\s+implements?\s+(\w[\w.\-]+)", "implements"),
        (r"(\w[\w.\-]+)\s+calls?\s+(\w[\w.\-]+)", "calls"),
        (r"(\w[\w.\-]+)\s+imports?\s+(\w[\w.\-]+)", "imports"),
        (r"(\w[\w.\-]+)\s+has\s+(?:a\s+)?(\w[\w.\-]+)", "has"),
        (r"(\w[\w.\-]+)\s+contains?\s+(\w[\w.\-]+)", "contains"),
    ]

    _FACT_PATTERNS: list[tuple[str, str]] = [
        (r"(\w[\w.\-]+)\s+is\s+(?:a\s+|an\s+)?(\w[\w.\-]+(?:\s+\w[\w.\-]+){0,3})", "is_a"),
        (r"(\w[\w.\-]+)\s+does\s+(\w[\w.\-]+(?:\s+\w[\w.\-]+){0,2})", "does"),
        (r"(\w[\w.\-]+)\s+returns?\s+(\w[\w.\-]+(?:\s+\w[\w.\-]+){0,2})", "returns"),
        (r"(\w[\w.\-]+)\s+throws?\s+(\w[\w.\-]+)", "throws"),
        (r"(\w[\w.\-]+)\s+requires?\s+(\w[\w.\-]+(?:\s+\w[\w.\-]+){0,2})", "requires"),
    ]

    _CONFIG_PATTERN = re.compile(r"(\w[\w.\-]+)\s*[=:]\s*([^\s,;]+)")
    _FILE_PATTERN = re.compile(r"[\w/\-]+\.\w{1,10}")
    _CLASS_PATTERN = re.compile(r"\b[A-Z]\w+(?:Error|Exception|Handler|Manager|Service|Controller|Model|Config|Builder|Factory|Provider|Store|Engine|Client)\b")
    _FUNC_PATTERN = re.compile(r"\b(?:def |function |async )(\w+)\s*\(")

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._db_path = get_db_path("knowledge_graph")
        self._ensure_db()

    def _ensure_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS facts (
                    id TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object_value TEXT NOT NULL,
                    source_exchange TEXT DEFAULT '',
                    topic TEXT DEFAULT '',
                    confidence REAL DEFAULT 1.0,
                    created_at TEXT NOT NULL,
                    access_count INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS entities (
                    name TEXT PRIMARY KEY,
                    entity_type TEXT DEFAULT 'concept',
                    attributes TEXT DEFAULT '{}',
                    confidence REAL DEFAULT 1.0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS relations (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    target TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    context TEXT DEFAULT '',
                    confidence REAL DEFAULT 1.0
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_predicate ON facts(predicate)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_topic ON facts(topic)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_type ON relations(relation_type)")

    def extract_from_text(
        self,
        text: str,
        *,
        source_exchange: str = "",
        topic: str = "",
    ) -> ExtractionResult:
        """Extract entities, relations, and facts from text."""
        result = ExtractionResult()

        result.entities = self._extract_entities(text)
        result.relations = self._extract_relations(text)
        result.facts = self._extract_facts(text, source_exchange=source_exchange, topic=topic)

        return result

    def extract_from_exchange(
        self,
        user_msg: str,
        assistant_msg: str,
        *,
        topic: str = "",
    ) -> ExtractionResult:
        """Extract knowledge from a Q&A exchange pair."""
        combined = f"{user_msg}\n{assistant_msg}"
        return self.extract_from_text(
            combined,
            source_exchange=user_msg[:100],
            topic=topic,
        )

    def store_facts(self, facts: list[Fact]) -> int:
        """Store extracted facts to the knowledge database."""
        stored = 0
        now = datetime.now(timezone.utc).isoformat()
        with sqlite_connect(self._db_path) as conn:
            for fact in facts:
                if not fact.subject or not fact.predicate:
                    continue
                fact_id = fact.id or f"fact-{uuid.uuid4().hex[:8]}"
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO facts
                           (id, subject, predicate, object_value, source_exchange,
                            topic, confidence, created_at, access_count)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (fact_id, fact.subject, fact.predicate, fact.object_value,
                         fact.source_exchange, fact.topic, fact.confidence,
                         fact.created_at or now, fact.access_count),
                    )
                    stored += 1
                except Exception as exc:
                    logger.debug("[knowledge] Failed to store fact: %s", exc)
        return stored

    def store_entities(self, entities: list[Entity]) -> int:
        """Store extracted entities to the knowledge database."""
        stored = 0
        with sqlite_connect(self._db_path) as conn:
            for entity in entities:
                if not entity.name:
                    continue
                try:
                    import json as _json
                    conn.execute(
                        """INSERT OR REPLACE INTO entities (name, entity_type, attributes, confidence)
                           VALUES (?,?,?,?)""",
                        (entity.name, entity.entity_type,
                         _json.dumps(entity.attributes), entity.confidence),
                    )
                    stored += 1
                except Exception as exc:
                    logger.debug("[knowledge] Failed to store entity: %s", exc)
        return stored

    def store_relations(self, relations: list[Relation]) -> int:
        """Store extracted relations to the knowledge database."""
        stored = 0
        with sqlite_connect(self._db_path) as conn:
            for rel in relations:
                if not rel.source or not rel.target:
                    continue
                try:
                    conn.execute(
                        """INSERT INTO relations (id, source, target, relation_type, context, confidence)
                           VALUES (?,?,?,?,?,?)""",
                        (f"rel-{uuid.uuid4().hex[:8]}", rel.source, rel.target,
                         rel.relation_type, rel.context, rel.confidence),
                    )
                    stored += 1
                except Exception as exc:
                    logger.debug("[knowledge] Failed to store relation: %s", exc)
        return stored

    def store_extraction(self, result: ExtractionResult) -> dict[str, int]:
        """Store a full extraction result."""
        return {
            "entities": self.store_entities(result.entities),
            "relations": self.store_relations(result.relations),
            "facts": self.store_facts(result.facts),
        }

    def query_facts(
        self,
        subject: str = "",
        predicate: str = "",
        topic: str = "",
        top: int = 20,
    ) -> list[Fact]:
        """Query facts by subject, predicate, or topic."""
        conditions = []
        params: list[Any] = []
        if subject:
            conditions.append("subject LIKE ?")
            params.append(f"%{subject}%")
        if predicate:
            conditions.append("predicate = ?")
            params.append(predicate)
        if topic:
            conditions.append("topic = ?")
            params.append(topic)

        where = " AND ".join(conditions) if conditions else "1=1"
        with sqlite_connect(self._db_path) as conn:
            conn.execute(
                f"UPDATE facts SET access_count = access_count + 1 WHERE {where}",
                params,
            )
            rows = conn.execute(
                f"SELECT * FROM facts WHERE {where} ORDER BY confidence DESC, created_at DESC LIMIT ?",
                params + [top],
            ).fetchall()

        return [Fact(
            id=r["id"], subject=r["subject"], predicate=r["predicate"],
            object_value=r["object_value"], source_exchange=r["source_exchange"],
            topic=r["topic"], confidence=r["confidence"],
            created_at=r["created_at"], access_count=r["access_count"],
        ) for r in rows]

    def query_relations(
        self,
        source: str = "",
        target: str = "",
        relation_type: str = "",
        top: int = 20,
    ) -> list[Relation]:
        """Query relations by source, target, or type."""
        conditions = []
        params: list[Any] = []
        if source:
            conditions.append("source LIKE ?")
            params.append(f"%{source}%")
        if target:
            conditions.append("target LIKE ?")
            params.append(f"%{target}%")
        if relation_type:
            conditions.append("relation_type = ?")
            params.append(relation_type)

        where = " AND ".join(conditions) if conditions else "1=1"
        with sqlite_connect(self._db_path) as conn:
            rows = conn.execute(
                f"SELECT * FROM relations WHERE {where} ORDER BY confidence DESC LIMIT ?",
                params + [top],
            ).fetchall()

        return [Relation(
            source=r["source"], target=r["target"],
            relation_type=r["relation_type"], context=r["context"],
            confidence=r["confidence"],
        ) for r in rows]

    def get_entity(self, name: str) -> Entity | None:
        """Get an entity by name."""
        with sqlite_connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM entities WHERE name = ?", (name,),
            ).fetchone()
        if not row:
            return None
        import json as _json
        return Entity(
            name=row["name"], entity_type=row["entity_type"],
            attributes=_json.loads(row["attributes"]) if row["attributes"] else {},
            confidence=row["confidence"],
        )

    def stats(self) -> dict[str, int]:
        """Get knowledge base statistics."""
        with sqlite_connect(self._db_path) as conn:
            facts_count = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
            entities_count = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
            relations_count = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
        return {
            "facts": facts_count,
            "entities": entities_count,
            "relations": relations_count,
        }

    def _extract_entities(self, text: str) -> list[Entity]:
        """Extract named entities from text."""
        entities: list[Entity] = []
        seen: set[str] = set()

        for m in self._CLASS_PATTERN.finditer(text):
            name = m.group(0)
            if name not in seen:
                seen.add(name)
                entities.append(Entity(name=name, entity_type="class", confidence=0.9))

        for m in self._FILE_PATTERN.finditer(text):
            name = m.group(0)
            if name not in seen and "/" in name or "." in name:
                seen.add(name)
                entities.append(Entity(name=name, entity_type="file", confidence=0.8))

        for m in self._FUNC_PATTERN.finditer(text):
            name = m.group(1)
            if name not in seen:
                seen.add(name)
                entities.append(Entity(name=name, entity_type="function", confidence=0.85))

        for m in self._CONFIG_PATTERN.finditer(text):
            key = m.group(1)
            if key not in seen and not key.startswith(("the", "this", "that")):
                seen.add(key)
                entities.append(Entity(name=key, entity_type="config_key", confidence=0.7))

        return entities

    def _extract_relations(self, text: str) -> list[Relation]:
        """Extract relations from text using pattern matching."""
        relations: list[Relation] = []
        for pattern, rel_type in self._RELATION_PATTERNS:
            for m in re.finditer(pattern, text, re.IGNORECASE):
                relations.append(Relation(
                    source=m.group(1),
                    target=m.group(2),
                    relation_type=rel_type,
                    context=m.group(0),
                    confidence=0.8,
                ))
        return relations

    def _extract_facts(
        self,
        text: str,
        *,
        source_exchange: str = "",
        topic: str = "",
    ) -> list[Fact]:
        """Extract facts from text using pattern matching."""
        facts: list[Fact] = []
        now = datetime.now(timezone.utc).isoformat()

        for pattern, pred in self._FACT_PATTERNS:
            for m in re.finditer(pattern, text, re.IGNORECASE):
                subject = m.group(1)
                obj = m.group(2).strip().rstrip(".,;:")
                if len(obj) < 2 or obj.lower() in ("a", "an", "the", "is", "are"):
                    continue
                facts.append(Fact(
                    id=f"fact-{uuid.uuid4().hex[:8]}",
                    subject=subject,
                    predicate=pred,
                    object_value=obj,
                    source_exchange=source_exchange,
                    topic=topic,
                    confidence=0.75,
                    created_at=now,
                ))

        for m in self._CONFIG_PATTERN.finditer(text):
            facts.append(Fact(
                id=f"fact-{uuid.uuid4().hex[:8]}",
                subject=m.group(1),
                predicate="configured_as",
                object_value=m.group(2),
                source_exchange=source_exchange,
                topic=topic,
                confidence=0.85,
                created_at=now,
            ))

        return facts
