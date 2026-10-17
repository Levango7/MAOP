"""MAOP Knowledge Graph Reasoning Engine — F2-03 (M2.3).

Dual-channel reasoning over the entity-relation graph stored by
:class:`KnowledgeGraph` (SQLite for Personal / Neo4j for Enterprise added
in the enterprise repo).

Channels
--------
* **Rule (Datalog-style)** — forward-chaining over a fixed set of rules.
  Handles transitivity (``uses``, ``depends_on``, ``extends``),
  symmetry (``related_to``) and domain-specific rules
  ("if A uses B and B is_a library, then A depends_on library B").
* **Embedding (KGE)** — a lightweight TransE-style scoring function over
  the graph's own adjacency (no GPU, no training data beyond the graph
  itself). Answers "what is most like X?" and ranks candidate targets
  for multi-hop queries.
* **Hybrid query planner** — a multi-hop query ("A's collaborator's
  advisor") is planned as a sequence of rule-grounded hops with KGE
  fallback at each step when no rule fires, so the path is always
  explainable.

Scope (ADR-016 D5 enterprise boundary)
--------------------------------------
This module is the **Personal-Edition fallback** engine: pure-Python,
no Neo4j/transE-training deps. The enterprise repo (MAOS) injects a
Neo4j-backed implementation with the same interface — so every consumer
goes through ``ReasoningEngine`` (protocol) and never branches on the
backend.

Current milestone (M2.3 - MVP)
------------------------------
* ``ReasoningEngine`` protocol.
* ``DatalogReasoner`` — transitivity/symmetry + domain rules over
  :class:`KnowledgeGraph`'s SQLite tables.
* ``KgeScorer`` — TransE-style score for target ranking using the
  graph's own adjacency (trained lazily on first use, cached).
* ``plan_hops`` / ``execute_hops`` — multi-hop query planner with
  explainable output.
* ``find_implied`` — periodic materialiser (high-confidence implied
  edges shipped back to ``KnowledgeGraph`` as relations).
"""

from __future__ import annotations

import logging
import math
import threading
from abc import abstractmethod
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

__all__ = [
    "DatalogReasoner",
    "HopStep",
    "ImpliedEdge",
    "KgeScorer",
    "PathPlan",
    "ReasoningEngine",
    "Rule",
    "hop_plan",
]

# ── Data model ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class Rule:
    """A forward-chaining rule: ``head`` holds when all ``body`` triples match.

    Each body triple is ``(src_role, relation, tgt_role)`` where the role
    names are bound during matching. The rule engine tries every grounding
    of the body variables and emits the head triple for each.
    """

    name: str
    body: tuple[tuple[str, str, str], ...]
    head: tuple[str, str, str]
    # When True, every grounding of the body produces the head *without*
    # requiring the head triple to already exist — it is a derivation.
    derivable: bool = True

    def __post_init__(self) -> None:
        if not self.body:
            raise ValueError(f"rule {self.name!r} must have at least one body triple")
        if not self.head or None in self.head or any(not isinstance(p, str) or not p.strip() for p in self.head):
            raise ValueError(f"rule {self.name!r} needs a complete head triple")


@dataclass(frozen=True)
class HopStep:
    """One hop in a multi-hop plan."""

    source: str
    relation: str
    target: str
    channel: str  # "rule" | "kge"
    confidence: float
    explanation: str


@dataclass(frozen=True)
class PathPlan:
    """A fully-grounded multi-hop path."""

    steps: tuple[HopStep, ...]
    overall_confidence: float

    def explain(self) -> str:
        if not self.steps:
            return "(no path found)"
        parts = [f"path (conf={self.overall_confidence:.2f}):"]
        for i, step in enumerate(self.steps, 1):
            parts.append(
                f"  {i}. {step.source} --[{step.relation}]--> {step.target} "
                f"({step.channel}, conf={step.confidence:.2f}, "
                f"{step.explanation})"
            )
        return "\n".join(parts)


@dataclass(frozen=True)
class ImpliedEdge:
    """An inferred relation produced by the rule engine."""

    source: str
    target: str
    relation: str
    confidence: float
    rule_name: str
    path: tuple[str, ...] = ()

    def explain(self) -> str:
        via = f" via {self.rule_name}"
        path_str = f" (path: {' → '.join(self.path)})" if self.path else ""
        return f"{self.source} --[{self.relation}]--> {self.target}{via}{path_str}"


# ── Protocol ─────────────────────────────────────────────────────────


@runtime_checkable
class ReasoningEngine(Protocol):
    """Backend-agnostic reasoning interface (ADR-016 D5).

    Personal Edition uses this with SQLite; the enterprise repo provides
    a Neo4j-backed implementation.
    """

    @abstractmethod
    def infer(
        self, source: str, relation: str, *, max_depth: int = 3,
    ) -> list[ImpliedEdge]:
        """Return all implied edges one hop beyond ``source`` via ``relation``."""

    @abstractmethod
    def plan_hops(
        self,
        source: str,
        target_hint: str,
        *,
        relations: Sequence[str],
        max_hops: int = 3,
        min_confidence: float = 0.3,
    ) -> PathPlan:
        """Plan a multi-hop path from ``source`` toward ``target_hint``."""

    @abstractmethod
    def find_implied(self, min_confidence: float = 0.7) -> list[ImpliedEdge]:
        """Materialise all high-confidence implied relations."""


# ── Catalog of built-in rules ────────────────────────────────────────


SYMMETRIC_RELATIONS: set[str] = {"related_to", "collaborates_with", "knows"}

# Transitive relations always imply themselves one hop further.
TRANSITIVE_RELATIONS: set[str] = {
    "uses", "depends_on", "extends", "implements", "imports",
    "derived_from", "has_part",
}


def _built_in_rules() -> list[Rule]:
    """The Personal-Edition rule catalog.

    Rules are small, auditable and free of domain assumptions beyond the
    entity-relation graph — the enterprise edition adds domain rules here.
    """
    return [
        # Transitivity: A → B → C  ⇒  A → C
        Rule(
            name="transitive",
            body=(("A", "{rel}", "B"), ("B", "{rel}", "C")),
            head=("A", "{rel}", "C"),
        ),
        # Symmetry: A ↔ B  ⇒  B → A
        Rule(
            name="symmetric",
            body=(("A", "{rel}", "B"),),
            head=("B", "{rel}", "A"),
        ),
    ]


# ── Datalog-style forward chainer ────────────────────────────────────


class DatalogReasoner:
    """Forward-chaining rule engine over KnowledgeGraph's SQLite tables.

    Reads the existing ``relations`` table (shared with
    :class:`KnowledgeGraph`) and emits implied edges without writing them
    — materialisation is a separate step (``find_implied``).

    The engine is *local*: it keeps an in-memory adjacency dict populated
    once per :meth:`refresh`, so repeated queries don't hit SQLite.
    """

    def __init__(self) -> None:
        self._rules = _built_in_rules()
        self._adj: dict[str, list[tuple[str, str, float]]] = {}  # src → [(rel, tgt, conf)]
        self._lock = threading.Lock()

    # -- adjacency helpers ----------------------------------------------

    @staticmethod
    def _get_edges_for(
        db_path: str | None,
        entity_name: str,
        *,
        direction: str = "both",
        relation_type: str = "",
    ) -> list[tuple[str, str, float]]:
        """Read edges directly from SQLite (no KnowledgeGraph import needed).

        Returns ``(source, target, confidence)`` triples.
        """
        from maop.core.backends.db_utils import sqlite_connect, validate_identifier

        conditions: list[str] = []
        params: list[Any] = []

        if direction in ("outgoing", "both"):
            conditions.append("source = ?")
            params.append(entity_name)
        if direction in ("incoming", "both"):
            conditions.append("target = ?")
            params.append(entity_name)

        where = " OR ".join(conditions) if len(conditions) > 1 else (conditions[0] if conditions else "1=1")
        if relation_type:
            where += " AND relation_type = ?"
            params.append(relation_type)

        validate_identifier("relations")
        try:
            with sqlite_connect(db_path) as conn:
                rows = conn.execute(
                    f"SELECT source, target, relation_type, confidence "
                    f"FROM relations WHERE {where} ORDER BY confidence DESC LIMIT 50",  # nosec B608 — 语句仅含固定子句与 ? 占位
                    params,
                ).fetchall()
        except Exception as exc:
            logger.debug("[kgr] SQLite edge read failed: %s", exc)
            return []

        return [(r["source"], r["target"], r["confidence"]) for r in rows]

    def refresh(self, db_path: str | None = None) -> None:
        """Re-read adjacency from the relations table."""
        validate_local = __import__("maop.core.backends.db_utils", fromlist=["validate_identifier"]).validate_identifier
        validate_local("relations")
        with self._lock:
            self._adj.clear()
            try:
                from maop.core.backends.db_utils import sqlite_connect
                with sqlite_connect(db_path) as conn:
                    rows = conn.execute(
                        "SELECT source, target, relation_type, confidence FROM relations",
                    ).fetchall()
                for r in rows:
                    src, tgt, rel, conf = r["source"], r["target"], r["relation_type"], r["confidence"]
                    self._adj.setdefault(src, []).append((rel, tgt, float(conf if conf is not None else 1.0)))
            except Exception as exc:
                logger.debug("[kgr] adjacency refresh failed: %s", exc)

    def infer_one_hop(
        self, source: str, relation: str,
    ) -> list[ImpliedEdge]:
        """Apply built-in rules starting from ``source`` + ``relation``."""
        implied: list[ImpliedEdge] = []

        # Ensure adjacency is populated.
        if not self._adj:
            self.refresh()

        # Collect direct outgoing edges of the requested relation.
        direct_targets = [
            tgt for rel, tgt, _conf in self._adj.get(source, ()) if rel == relation
        ]

        if relation in TRANSITIVE_RELATIONS:
            # transitive rule: A -[rel]-> B, B -[rel]-> C  => A -[rel]-> C
            for mid in direct_targets:
                for rel2, tgt, conf in self._adj.get(mid, ()):
                    if rel2 == relation and tgt != source:
                        implied.append(ImpliedEdge(
                            source=source, target=tgt, relation=relation,
                            confidence=conf, rule_name="transitive",
                            path=(source, mid, tgt),
                        ))

        if relation in SYMMETRIC_RELATIONS:
            # symmetric rule: A -[rel]-> B  => B -[rel]-> A
            for tgt in direct_targets:
                implied.append(ImpliedEdge(
                    source=tgt, target=source, relation=relation,
                    confidence=1.0, rule_name="symmetric",
                    path=(tgt, source),
                ))

        return implied

    def find_all_implied(self, min_confidence: float = 0.7) -> list[ImpliedEdge]:
        """Materialise every implied edge above the confidence threshold."""
        results: list[ImpliedEdge] = []
        seen: set[tuple[str, str, str]] = set()

        # Snapshot existing edges so we don't emit what already exists.
        existing: set[tuple[str, str, str]] = set()
        for src, outs in self._adj.items():
            for rel, tgt, _ in outs:
                existing.add((src, rel, tgt))

        targets = {*(r for outs in self._adj.values() for r, _, _ in outs if r in TRANSITIVE_RELATIONS | SYMMETRIC_RELATIONS)}
        for src in list(self._adj):
            for rel in targets:
                for edge in self._infer_for_relation(src, rel):
                    key = (edge.source, edge.relation, edge.target)
                    if key not in seen and key not in existing and edge.confidence >= min_confidence:
                        seen.add(key)
                        results.append(edge)

        return results

    def _infer_for_relation(self, source: str, relation: str) -> list[ImpliedEdge]:
        """Low-level: infer for a single (source, relation) pairing."""
        implied: list[ImpliedEdge] = []

        direct_targets = {
            tgt for rel, tgt, _conf in self._adj.get(source, ()) if rel == relation
        }

        if relation in TRANSITIVE_RELATIONS:
            for mid in direct_targets:
                for rel2, tgt, conf in self._adj.get(mid, ()):
                    if rel2 == relation and tgt != source:
                        implied.append(ImpliedEdge(
                            source=source, target=tgt, relation=relation,
                            confidence=conf * 0.9, rule_name="transitive",
                            path=(source, mid, tgt),
                        ))

        if relation in SYMMETRIC_RELATIONS:
            for tgt in direct_targets:
                implied.append(ImpliedEdge(
                    source=tgt, target=source, relation=relation,
                    confidence=0.95, rule_name="symmetric",
                    path=(tgt, source),
                ))

        return implied


# ── KGE-style scorer ─────────────────────────────────────────────────


class KgeScorer:
    """A lightweight TransE-style scoring function (no GPU, no external model).

    The scorer is *local*: it treats every existing relation as a training
    triple and refines embeddings by a fixed number of SGD epochs on first
    call (cached afterwards).

    This is deliberately NOT a production KGE library. It exists to give the
    rule engine a ranking fallback and to answer "what is most likely
    connected to X?" when no rule fires. """
    EMBED_DIM: ClassVar[int] = 32
    LEARNING_RATE: ClassVar[float] = 0.05
    EPOCHS: ClassVar[int] = 60
    MARGIN: ClassVar[float] = 1.0

    def __init__(self) -> None:
        self._ent: dict[str, list[float]] = {}
        self._rel: dict[str, list[float]] = {}
        self._triples: list[tuple[str, str, str]] = []
        self._trained = False
        self._lock = threading.Lock()

    # -- training --------------------------------------------------------

    @staticmethod
    def _rng(seed: int):
        """Deterministic PRNG (no numpy needed)."""
        # Simple xorshift32 with a state closure.
        state = seed & 0xFFFFFFFF
        def _next() -> float:
            nonlocal state
            state ^= (state << 13) & 0xFFFFFFFF
            state ^= (state >> 17)
            state ^= (state << 5) & 0xFFFFFFFF
            return (state & 0xFFFF) / 0xFFFF
        return _next

    def fit(self, triples: Iterable[tuple[str, str, str]]) -> None:
        """Train over (source, relation, target) triples."""
        self._triples = list(triples)
        if not self._triples:
            return

        rand = self._rng(seed=42)
        entities = {s for s, _, _ in self._triples} | {t for _, _, t in self._triples}
        relations = {r for _, r, _ in self._triples}

        # Xavier-like init scaled to [−0.5, 0.5]
        scale = 0.5 / math.sqrt(self.EMBED_DIM)
        self._ent = {e: [(rand() - 0.5) * scale * 2 for _ in range(self.EMBED_DIM)] for e in entities}
        self._rel = {r: [(rand() - 0.5) * scale * 2 for _ in range(self.EMBED_DIM)] for r in relations}

        for _ in range(self.EPOCHS):
            loss = self._train_epoch()
            if loss < 1e-3:
                break

        self._trained = True
        logger.debug("[kge] trained on %d triples, %d entities, %d relations",
                     len(self._triples), len(self._ent), len(self._rel))

    def _train_epoch(self) -> float:
        """One SGD epoch over the triples; returns mean loss."""
        import random
        triples = list(self._triples)
        random.shuffle(triples)
        total_loss = 0.0

        for s, r, t in triples:
            s_vec = self._ent[s]
            r_vec = self._rel[r]
            t_vec = self._ent[t]

            # Energy of the true triple: ‖s + r − t‖
            d_true = math.sqrt(sum((s_vec[d] + r_vec[d] - t_vec[d]) ** 2 for d in range(self.EMBED_DIM)))

            # Corrupt the target
            neg_t = t
            while neg_t == t:
                neg_t = random.choice(list(self._ent))
            n_vec = self._ent[neg_t]

            d_neg = math.sqrt(sum((s_vec[d] + r_vec[d] - n_vec[d]) ** 2 for d in range(self.EMBED_DIM)))

            loss = max(0.0, self.MARGIN + d_true - d_neg)
            if loss == 0.0:
                continue

            total_loss += loss

            # Subgradient step (margin-based).
            grad_scale = self.LEARNING_RATE
            for d in range(self.EMBED_DIM):
                diff_t = s_vec[d] + r_vec[d] - t_vec[d]
                diff_n = s_vec[d] + r_vec[d] - n_vec[d]

                if d_true > 0:
                    g_s_t = diff_t / d_true
                    g_r_t = diff_t / d_true
                    g_t_t = -diff_t / d_true
                    s_vec[d] -= grad_scale * g_s_t
                    r_vec[d] -= grad_scale * g_r_t
                    t_vec[d] -= grad_scale * g_t_t

                if d_neg > 0:
                    g_s_n = diff_n / d_neg
                    g_r_n = diff_n / d_neg
                    g_n_n = -diff_n / d_neg
                    s_vec[d] += grad_scale * g_s_n
                    r_vec[d] += grad_scale * g_r_n
                    n_vec[d] += grad_scale * g_n_n

        return total_loss / max(1, len(triples))

    # -- inference -------------------------------------------------------

    def energy(self, source: str, relation: str, target: str) -> float:
        """TransE energy ‖s + r − t‖ (lower = more plausible)."""
        if not self._trained:
            return float("inf")
        s = self._ent.get(source)
        r = self._rel.get(relation)
        t = self._ent.get(target)
        if s is None or r is None or t is None:
            return float("inf")
        return math.sqrt(sum((s[d] + r[d] - t[d]) ** 2 for d in range(self.EMBED_DIM)))

    def rank_targets(
        self, source: str, relation: str, candidates: Sequence[str], *, top: int = 10,
    ) -> list[tuple[str, float]]:
        """Rank ``candidate`` targets by plausibility. O(|candidates|)."""
        if not self._trained or not candidates:
            return []
        scored = [(t, -self.energy(source, relation, t)) for t in candidates if t in self._ent]
        scored.sort(key=lambda x: -x[1])
        return scored[:top]


# ── ReasoningEngine implementation ────────────────────────────────────


class GraphReasoningEngine:
    """Personal-Edition implementation of :class:`ReasoningEngine`.

    Combines :class:`DatalogReasoner` (exact, explainable) and
    :class:`KgeScorer` (statistical fallback). Multi-hop queries go through
    :meth:`plan_hops`, which tries rule hops first and falls back to KGE
    ranking at each step.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path
        self._datalog = DatalogReasoner()
        self._kge = KgeScorer()

    # -- public API -------------------------------------------------------

    def refresh(self) -> None:
        """Re-read adjacency and (re-)train KGE."""
        self._datalog.refresh(self._db_path)
        self._train_kge()

    def _train_kge(self) -> None:
        triples = [
            (src, rel, tgt)
            for src, outs in self._datalog._adj.items()
            for rel, tgt, _ in outs
        ]
        if triples:
            self._kge.fit(triples)

    def infer(
        self, source: str, relation: str, *, max_depth: int = 3,
    ) -> list[ImpliedEdge]:
        if not self._datalog._adj:
            self.refresh()

        results: list[ImpliedEdge] = []
        frontier: list[tuple[str, int]] = [(source, 0)]
        visited: set[str] = {source}

        while frontier:
            current, depth = frontier.pop(0)
            if depth >= max_depth:
                continue
            for edge in self._datalog._infer_for_relation(current, relation):
                if edge.target not in visited:
                    results.append(edge)
                    visited.add(edge.target)
                    frontier.append((edge.target, depth + 1))
        return results

    def plan_hops(
        self,
        source: str,
        target_hint: str,
        *,
        relations: Sequence[str] | None = None,
        max_hops: int = 3,
        min_confidence: float = 0.3,
    ) -> PathPlan:
        if not self._datalog._adj:
            self.refresh()

        rels = list(relations) if relations else list(TRANSITIVE_RELATIONS | SYMMETRIC_RELATIONS)

        # Greedy best-first at each hop: rule first, KGE fallback.
        steps: list[HopStep] = []
        current = source
        for _hop in range(max_hops):
            if current == target_hint:
                break
            step = self._best_hop(current, target_hint, rels, min_confidence)
            if step is None:
                break
            steps.append(step)
            current = step.target

        conf = math.prod(s.confidence for s in steps) if steps else 0.0
        return PathPlan(steps=tuple(steps), overall_confidence=conf)

    def _best_hop(
        self,
        source: str,
        target_hint: str,
        relations: Sequence[str],
        min_confidence: float,
    ) -> HopStep | None:
        """Pick the best next hop from ``source`` toward ``target_hint``."""
        # Try rule inference first (deterministic, explainable).
        for rel in relations:
            inferred = self._datalog._infer_for_relation(source, rel)
            for edge in inferred:
                if edge.confidence >= min_confidence:
                    return HopStep(
                        source=source, relation=edge.relation, target=edge.target,
                        channel="rule", confidence=edge.confidence,
                        explanation=edge.explain(),
                    )

        # Fallback: KGE.
        candidates = list(self._kge._ent)
        if not candidates:
            return None
        scored = self._kge.rank_targets(source, relations[0] if relations else "related_to",
                                        [target_hint, *candidates], top=3)
        for tgt, score in scored:
            if tgt != source:
                conf = max(0.0, min(1.0, (score + 10) / 20))  # rough calibration
                if conf >= min_confidence:
                    return HopStep(
                        source=source, relation=relations[0] if relations else "related_to",
                        target=tgt, channel="kge", confidence=conf,
                        explanation=f"KGE score={score:.3f}",
                    )
        return None

    def find_implied(self, min_confidence: float = 0.7) -> list[ImpliedEdge]:
        if not self._datalog._adj:
            self.refresh()
        return self._datalog.find_all_implied(min_confidence)


# ── Convenience function ──────────────────────────────────────────────


def hop_plan(
    source: str,
    target: str,
    *,
    relations: Sequence[str] | None = None,
    max_hops: int = 3,
    db_path: str | None = None,
) -> PathPlan:
    """One-shot convenience wrapper."""
    engine = GraphReasoningEngine(db_path=db_path)
    return engine.plan_hops(source, target, relations=relations, max_hops=max_hops)
