"""MAOP L1 Atom Fact Layer — 原子事实抽取与语义指纹去重合并。

漏斗式记忆的 L1 层：把 L0 原始对话/工具结果提炼为**原子事实**
（subject - predicate - object 三元组），并通过**语义指纹**去重合并：

  - 同一事实（"用户喜欢咖啡"）在多个会话出现时只保留一条，
    ``access_count`` 递增、``last_seen_at`` 更新、confidence 提升。
  - 抽取复用 ``KnowledgeExtractor`` 的模式匹配（uses/is/has/returns/
    requires 等关系 + 配置键值对 + 文件/类/函数名识别），不依赖 LLM，
    零额外成本、确定性可测试。
  - 高频事实（access_count 达到阈值）可晋升到 L3 长期记忆
    （``promote_facts`` 写入向量索引）。

设计动机（对齐 TencentDB Agent Memory 的漏斗哲学）：
  - **结构事实要抽取**：扁平向量堆无法表达"谁和谁是什么关系"，
    原子事实保留结构化语义，检索更精准。
  - **去重合并**：避免同一事实重复入库污染检索结果。

Usage::

    from maop.memory.atoms import AtomFactStore

    atoms = AtomFactStore(root_dir="/path/to/MAOP")
    report = atoms.ingest("auth module uses JWT tokens with 24h expiry",
                          source_ref="ev-xxx", topic="authentication")
    hits = atoms.search_facts("JWT")
    promoted = atoms.promote_facts(min_access=3)
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from maop.core.backends.db_utils import sqlite_connect
from maop.core.memory.knowledge_extractor import Fact, KnowledgeExtractor
from maop.memory.shared_db import get_memory_db_path

logger = logging.getLogger(__name__)

# 置信度递增幅度：事实每多出现一次，confidence 向 1.0 靠近。
_CONFIDENCE_STEP = 0.1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def semantic_fingerprint(subject: str, predicate: str, object_value: str) -> str:
    """计算事实的语义指纹（SHA-256，规范化后）。

    规范化：小写、去空白、去尾标点。相同语义的事实即使措辞略有差异
    （如 "User likes coffee" vs "the user likes coffee"）也会命中同一指纹。
    """
    def norm(s: str) -> str:
        s = re.sub(r"\s+", " ", s).strip().lower()
        return s.rstrip(".,;!?")
    raw = f"{norm(subject)}|{norm(predicate)}|{norm(object_value)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class AtomFactStore:
    """L1 原子事实存储：抽取 + 指纹去重 + 检索 + 晋升 L3。

    Parameters
    ----------
    root_dir : str | Path
        MAOP 项目根目录。
    knowledge_extractor : KnowledgeExtractor | None
        复用模式匹配抽取器（默认懒加载共享实例）。
    llm_dedup : bool
        是否启用 LLM 语义去重（方案 A）。默认 False——此时仅用 SHA-256
        语义指纹做精确去重，行为与旧版完全一致。启用后，指纹未命中时
        会调用 ``llm_judge`` 判定新事实与同 subject/predicate 候选是否
        语义相同，相同则合并。
    llm_judge : LLMJudge | None
        语义判定器（``Callable[[dict, dict], bool | None]``）。``llm_dedup
        =True`` 但未提供时，懒加载构造默认判定器（见
        ``maop.memory.llm_dedup.build_llm_semantic_judge``）；构造失败则
        静默降级为纯指纹去重。
    """

    def __init__(
        self,
        root_dir: str | Path,
        *,
        knowledge_extractor: KnowledgeExtractor | None = None,
        llm_dedup: bool = False,
        llm_judge: Any = None,
    ) -> None:
        self._root = Path(root_dir)
        self._extractor = knowledge_extractor
        self._llm_dedup = bool(llm_dedup)
        self._llm_judge = llm_judge
        self._db_path = get_memory_db_path()
        self._fts5_available: bool = False
        self._ensure_db()

    # ── LLM 语义去重（方案 A） ────────────────────────────────

    @property
    def llm_dedup(self) -> bool:
        """是否启用 LLM 语义去重。"""
        return self._llm_dedup

    @property
    def llm_judge(self):
        """LLM 语义判定器（懒加载）。

        未显式提供时，尝试从 models.yaml 构造默认判定器；任何失败返回
        None，调用方按"未启用 LLM 去重"处理（纯指纹去重）。
        """
        if self._llm_judge is None and self._llm_dedup:
            try:
                from maop.memory.llm_dedup import build_llm_semantic_judge

                self._llm_judge = build_llm_semantic_judge(root_dir=self._root)
            except Exception as exc:
                logger.warning("[atoms] 默认 LLM 判定器构造失败: %s", exc)
                self._llm_judge = None
        return self._llm_judge

    def _llm_merge_candidates(self, subject: str, predicate: str, top: int = 5) -> list[dict[str, Any]]:
        """查询与新事实可能重复的候选（同 subject 或同 predicate）。

        仅用于 LLM 语义去重：指纹未命中时，缩小 LLM 判定范围到强相关的
        候选，避免把每条新事实都与全库比较。
        """
        if not subject and not predicate:
            return []
        sql = (
            "SELECT id, subject, predicate, object_value, confidence, access_count "
            "FROM atom_facts"
        )
        where: list[str] = []
        params: list[Any] = []
        if subject:
            where.append("subject = ?")
            params.append(subject)
        if predicate:
            where.append("predicate = ?")
            params.append(predicate)
        sql += " WHERE " + " OR ".join(where)
        sql += " ORDER BY access_count DESC, confidence DESC LIMIT ?"
        params.append(max(1, int(top)))
        try:
            with sqlite_connect(self._db_path) as conn:
                cursor = conn.execute(sql, params)
                cols = [d[0] for d in cursor.description] if cursor.description else []
                return [dict(zip(cols, row)) for row in cursor.fetchall()]
        except Exception as exc:
            logger.warning("[atoms] LLM 候选查询失败: %s", exc)
            return []

    # ── Schema ───────────────────────────────────────────────

    def _ensure_db(self) -> None:
        """初始化 atom_facts 表（幂等）。"""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS atom_facts (
                    id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL UNIQUE,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL DEFAULT '',
                    object_value TEXT NOT NULL DEFAULT '',
                    source_ref TEXT NOT NULL DEFAULT '',
                    topic TEXT NOT NULL DEFAULT 'general',
                    confidence REAL NOT NULL DEFAULT 0.5,
                    access_count INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_atom_subject ON atom_facts(subject)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_atom_topic ON atom_facts(topic)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_atom_fingerprint ON atom_facts(fingerprint)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_atom_last_seen ON atom_facts(last_seen_at)
            """)
            # FTS5 全文检索虚拟表（外部内容表，与 atom_facts 同步）
            try:
                conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS atom_facts_fts USING fts5(
                        subject,
                        predicate,
                        object_value,
                        content='atom_facts',
                        content_rowid='rowid'
                    )
                """)
                conn.execute("""
                    CREATE TRIGGER IF NOT EXISTS atom_facts_ai AFTER INSERT ON atom_facts BEGIN
                        INSERT INTO atom_facts_fts(rowid, subject, predicate, object_value)
                        VALUES (new.rowid, new.subject, new.predicate, new.object_value);
                    END
                """)
                conn.execute("""
                    CREATE TRIGGER IF NOT EXISTS atom_facts_ad AFTER DELETE ON atom_facts BEGIN
                        INSERT INTO atom_facts_fts(atom_facts_fts, rowid, subject, predicate, object_value)
                        VALUES ('delete', old.rowid, old.subject, old.predicate, old.object_value);
                    END
                """)
                conn.execute("""
                    CREATE TRIGGER IF NOT EXISTS atom_facts_au AFTER UPDATE ON atom_facts BEGIN
                        INSERT INTO atom_facts_fts(atom_facts_fts, rowid, subject, predicate, object_value)
                        VALUES ('delete', old.rowid, old.subject, old.predicate, old.object_value);
                        INSERT INTO atom_facts_fts(rowid, subject, predicate, object_value)
                        VALUES (new.rowid, new.subject, new.predicate, new.object_value);
                    END
                """)
                self._fts5_available = True
            except Exception as exc:
                logger.warning("[atoms] FTS5 不可用，降级到 LIKE 查询: %s", exc)
                self._fts5_available = False

    @property
    def extractor(self) -> KnowledgeExtractor:
        """共享的 KnowledgeExtractor 实例（懒加载）。"""
        if self._extractor is None:
            self._extractor = KnowledgeExtractor(root_dir=self._root)
        return self._extractor

    # ── 抽取 + 写入 ──────────────────────────────────────────

    def ingest(
        self,
        text: str,
        *,
        source_ref: str = "",
        topic: str = "",
    ) -> dict[str, int]:
        """从一段文本抽取原子事实并去重入库。

        Returns
        -------
        dict[str, int]
            ``{"extracted": 抽取条数, "new": 新入库, "merged": 命中指纹合并}``
        """
        if not text or not text.strip():
            return {"extracted": 0, "new": 0, "merged": 0}

        extraction = self.extractor.extract_from_text(
            text, source_exchange=source_ref[:100], topic=topic,
        )
        facts = list(extraction.facts)
        # 关系（source - relation_type - target）也折叠为原子事实：
        # "auth module uses JWT" → fact(module, uses, JWT)。
        # 关系类事实初始置信度略低（0.7），避免噪声污染高频晋升。
        for rel in extraction.relations:
            if rel.source and rel.target and rel.relation_type:
                facts.append(Fact(
                    subject=rel.source,
                    predicate=rel.relation_type,
                    object_value=rel.target,
                    topic=topic,
                    confidence=min(rel.confidence, 0.7),
                ))
        if not facts:
            return {"extracted": 0, "new": 0, "merged": 0}

        new_count = 0
        merged_count = 0
        for fact in facts:
            if not fact.subject or not fact.predicate:
                continue
            outcome = self._upsert_fact(
                subject=fact.subject,
                predicate=fact.predicate,
                object_value=fact.object_value,
                source_ref=source_ref,
                topic=topic or fact.topic or "general",
                confidence=fact.confidence,
            )
            if outcome == "new":
                new_count += 1
            elif outcome == "merged":
                merged_count += 1

        logger.info(
            "[atoms] ingest: extracted=%d new=%d merged=%d",
            len(facts), new_count, merged_count,
        )
        return {"extracted": len(facts), "new": new_count, "merged": merged_count}

    def _upsert_fact(
        self,
        *,
        subject: str,
        predicate: str,
        object_value: str,
        source_ref: str,
        topic: str,
        confidence: float,
    ) -> str:
        """按语义指纹 upsert 一条事实：new / merged / skipped。

        LLM 语义去重（方案 A）：指纹未命中且 ``llm_dedup`` 启用时，
        把新事实与同 subject/predicate 的候选交给 ``llm_judge`` 判定；
        判定为同一事实则合并（merged），否则按原逻辑插入（new）。
        判定器不可用 / 失败 / 返回 None 时，一律降级为插入新事实——
        LLM 去重是增强，绝不阻断记忆写入。
        """
        fp = semantic_fingerprint(subject, predicate, object_value)
        now = _now_iso()
        try:
            with sqlite_connect(self._db_path) as conn:
                existing = conn.execute(
                    "SELECT id, access_count, confidence FROM atom_facts WHERE fingerprint = ?",
                    (fp,),
                ).fetchone()

                if existing is None:
                    # 指纹未命中：若启用 LLM 语义去重，先尝试语义合并
                    if self._llm_dedup:
                        merged_id = self._try_llm_semantic_merge(
                            subject=subject, predicate=predicate,
                            object_value=object_value, source_ref=source_ref,
                            topic=topic, confidence=confidence, now=now,
                        )
                        if merged_id:
                            return "merged"
                    conn.execute(
                        """INSERT INTO atom_facts
                           (id, fingerprint, subject, predicate, object_value,
                            source_ref, topic, confidence, access_count,
                            created_at, last_seen_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (f"fact-{uuid.uuid4().hex[:12]}", fp, subject, predicate,
                         object_value, source_ref, topic, _bounded_confidence(confidence),
                         1, now, now),
                    )
                    return "new"

                # 合并：access_count +1，last_seen 更新，confidence 微升
                new_conf = min(1.0, existing["confidence"] + _CONFIDENCE_STEP)
                conn.execute(
                    """UPDATE atom_facts
                       SET access_count = access_count + 1,
                           last_seen_at = ?,
                           confidence = ?,
                           source_ref = ?
                       WHERE fingerprint = ?""",
                    (now, new_conf, source_ref, fp),
                )
                return "merged"
        except Exception as exc:
            logger.warning("[atoms] upsert 失败 %s: %s", fp[:12], exc)
            return "skipped"

    def _try_llm_semantic_merge(
        self,
        *,
        subject: str,
        predicate: str,
        object_value: str,
        source_ref: str,
        topic: str,
        confidence: float,
        now: str,
    ) -> str:
        """LLM 语义去重：判定新事实与候选是否同一，命中则合并。

        Returns
        -------
        str
            被合并的候选事实 ID；未命中或降级时返回空字符串。
        """
        judge = self.llm_judge
        if judge is None:
            return ""
        new_fact = {
            "subject": subject, "predicate": predicate,
            "object_value": object_value,
        }
        try:
            for cand in self._llm_merge_candidates(subject, predicate):
                try:
                    verdict = judge(new_fact, cand)
                except Exception as exc:
                    logger.warning("[atoms] LLM 判定异常（降级插入新）: %s", exc)
                    return ""
                if verdict is True:
                    new_conf = min(1.0, float(cand.get("confidence", 0.5)) + _CONFIDENCE_STEP)
                    with sqlite_connect(self._db_path) as conn:
                        conn.execute(
                            """UPDATE atom_facts
                               SET access_count = access_count + 1,
                                   last_seen_at = ?,
                                   confidence = ?,
                                   source_ref = ?
                               WHERE id = ?""",
                            (now, new_conf, source_ref, cand["id"]),
                        )
                    logger.info(
                        "[atoms] LLM 语义合并: %r/%r -> %s",
                        _fmt_fact_short(new_fact), _fmt_fact_short(cand), cand["id"],
                    )
                    return str(cand["id"])
                # verdict False / None → 继续下一个候选；全不命中则插入新
        except Exception as exc:
            logger.warning("[atoms] LLM 语义合并失败（降级插入新）: %s", exc)
            return ""
        return ""

    # ── 检索 ─────────────────────────────────────────────────

    def search_facts(
        self,
        query: str = "",
        *,
        topic: str = "",
        top: int = 10,
        min_access: int = 1,
    ) -> list[dict[str, Any]]:
        """检索原子事实（按 subject/predicate/object 关键词匹配）。

        检索命中会递增 ``access_count``（同 KnowledgeExtractor.query_facts
        的计数语义），高频事实自然浮现。

        FTS5 可用时使用 MATCH 全文检索（性能优于 LIKE），不可用时降级
        到 LIKE 查询。
        """
        # FTS5 快速路径：有查询词且 FTS5 可用时使用 MATCH
        if self._fts5_available and query:
            fts_query = _sanitize_fts5_query(query)
            if fts_query:
                fts_sql = (
                    "SELECT a.id, a.subject, a.predicate, a.object_value, a.source_ref, "
                    "a.topic, a.confidence, a.access_count, a.created_at, a.last_seen_at "
                    "FROM atom_facts a "
                    "JOIN atom_facts_fts f ON a.rowid = f.rowid "
                    "WHERE atom_facts_fts MATCH ?"
                )
                fts_params: list[Any] = [fts_query]
                if topic:
                    fts_sql += " AND a.topic = ?"
                    fts_params.append(topic)
                if min_access > 1:
                    fts_sql += " AND a.access_count >= ?"
                    fts_params.append(min_access)
                fts_sql += " ORDER BY rank LIMIT ?"
                fts_params.append(max(1, int(top)))
                try:
                    with sqlite_connect(self._db_path) as conn:
                        cursor = conn.execute(fts_sql, fts_params)
                        cols = [d[0] for d in cursor.description] if cursor.description else []
                        results = [dict(zip(cols, row)) for row in cursor.fetchall()]
                        if not topic and min_access <= 1 and results:
                            ids = [r["id"] for r in results]
                            placeholders = ",".join("?" for _ in ids)
                            conn.execute(
                                f"UPDATE atom_facts SET access_count = access_count + 1 "
                                f"WHERE id IN ({placeholders})",
                                ids,
                            )
                        return results
                except Exception as exc:
                    logger.warning("[atoms] FTS5 搜索失败，降级到 LIKE: %s", exc)

        # LIKE 降级路径（原有逻辑）
        sql = (
            "SELECT id, subject, predicate, object_value, source_ref, topic, "
            "confidence, access_count, created_at, last_seen_at FROM atom_facts"
        )
        where: list[str] = []
        params: list[Any] = []
        if query:
            like = f"%{query}%"
            where.append("(subject LIKE ? OR predicate LIKE ? OR object_value LIKE ?)")
            params.extend([like, like, like])
        if topic:
            where.append("topic = ?")
            params.append(topic)
        if min_access > 1:
            where.append("access_count >= ?")
            params.append(min_access)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY confidence DESC, access_count DESC, last_seen_at DESC LIMIT ?"
        params.append(max(1, int(top)))

        try:
            with sqlite_connect(self._db_path) as conn:
                # 先查询得到结果列表（按 LIMIT top 已截断）
                cursor = conn.execute(sql, params)
                cols = [d[0] for d in cursor.description] if cursor.description else []
                results = [dict(zip(cols, row)) for row in cursor.fetchall()]
                # 命中计数：只对返回的 top N 条目递增 access_count，
                # 避免单字符 query 命中大量行导致全表 access_count+1
                # （与 query_facts 一致：只对关键词检索计数）
                if query and not topic and min_access <= 1 and results:
                    ids = [r["id"] for r in results]
                    placeholders = ",".join("?" for _ in ids)
                    conn.execute(
                        f"UPDATE atom_facts SET access_count = access_count + 1 "
                        f"WHERE id IN ({placeholders})",
                        ids,
                    )
                return results
        except Exception as exc:
            logger.warning("[atoms] search_facts 失败: %s", exc)
            return []

    def get_fact(self, fact_id: str) -> dict[str, Any] | None:
        """按 ID 读取单条事实。"""
        try:
            with sqlite_connect(self._db_path) as conn:
                row = conn.execute(
                    "SELECT * FROM atom_facts WHERE id = ?", (fact_id,),
                ).fetchone()
        except Exception as exc:
            logger.warning("[atoms] get_fact 失败: %s", exc)
            return None
        return dict(row) if row is not None else None

    # ── 晋升 L3 / 维护 ───────────────────────────────────────

    def promote_facts(
        self,
        *,
        min_access: int = 3,
        top: int = 50,
        vector_index_fn: Any = None,
    ) -> dict[str, int]:
        """把高频原子事实晋升到 L3 长期记忆。

        命中 ``access_count >= min_access`` 的事实，通过
        ``vector_index_fn(doc_id, text)`` 写入向量索引（默认不注入，
        由上层传 ``MemoryManager.long_term_index``）。晋升后重置
        ``access_count`` 防止重复晋升。

        Returns
        -------
        dict[str, int]
            ``{"promoted": 晋升条数}``
        """
        try:
            with sqlite_connect(self._db_path) as conn:
                rows = conn.execute(
                    "SELECT * FROM atom_facts WHERE access_count >= ? "
                    "ORDER BY access_count DESC LIMIT ?",
                    (max(1, int(min_access)), max(1, int(top))),
                ).fetchall()
                for row in rows:
                    text = f"{row['subject']} {row['predicate']} {row['object_value']}"
                    if vector_index_fn is not None:
                        try:
                            vector_index_fn(row["id"], text, {
                                "topic": row["topic"],
                                "source_ref": row["source_ref"],
                                "layer": "atom_fact",
                            })
                        except Exception as exc:
                            logger.debug("[atoms] 晋升向量索引失败 %s: %s", row["id"], exc)
                    # 重置 access_count，避免下次重复晋升
                    conn.execute(
                        "UPDATE atom_facts SET access_count = 0 WHERE id = ?",
                        (row["id"],),
                    )
        except Exception as exc:
            logger.warning("[atoms] promote_facts 失败: %s", exc)
            return {"promoted": 0}

        logger.info("[atoms] promote: %d 条高频事实晋升 L3", len(rows))
        return {"promoted": len(rows)}

    def stats(self) -> dict[str, Any]:
        """L1 原子事实层统计。"""
        try:
            with sqlite_connect(self._db_path) as conn:
                total = conn.execute("SELECT COUNT(*) AS c FROM atom_facts").fetchone()["c"]
                by_topic_rows = conn.execute(
                    "SELECT topic, COUNT(*) AS c FROM atom_facts GROUP BY topic"
                ).fetchall()
                top_facts = conn.execute(
                    "SELECT subject, predicate, object_value, access_count "
                    "FROM atom_facts ORDER BY access_count DESC LIMIT 5"
                ).fetchall()
        except Exception as exc:
            logger.warning("[atoms] stats 失败: %s", exc)
            return {"total": 0}
        return {
            "total": total,
            "by_topic": {r["topic"]: r["c"] for r in by_topic_rows},
            "top_facts": [dict(r) for r in top_facts],
        }

    def rebuild_fts_index(self) -> bool:
        """重建 FTS5 全文索引。

        当 FTS5 表与 atom_facts 不同步（如批量导入后触发器遗漏）时调用。
        FTS5 不可用时返回 False。
        """
        if not self._fts5_available:
            return False
        try:
            with sqlite_connect(self._db_path) as conn:
                conn.execute("INSERT INTO atom_facts_fts(atom_facts_fts) VALUES('rebuild')")
            logger.info("[atoms] FTS5 索引重建完成")
            return True
        except Exception as exc:
            logger.warning("[atoms] FTS5 索引重建失败: %s", exc)
            return False


def _fmt_fact_short(fact: dict[str, Any]) -> str:
    """把事实 dict 格式化为简短的诊断文本。"""
    subject = str(fact.get("subject") or "").strip()
    predicate = str(fact.get("predicate") or "").strip()
    obj = str(fact.get("object_value") or "").strip()
    return " ".join(p for p in (subject, predicate, obj) if p) or "(empty)"


def _bounded_confidence(conf: float) -> float:
    """把置信度夹在 [0.1, 1.0]."""
    try:
        value = float(conf)
    except (TypeError, ValueError):
        return 0.5
    return max(0.1, min(1.0, value))


def _sanitize_fts5_query(query: str) -> str:
    """把用户查询转换为安全的 FTS5 MATCH 表达式。

    FTS5 MATCH 语法中 ``" * ( ) : ^ -`` 等是特殊字符，直接传入会
    报错。本函数：
      1. 按空白拆分为多个词；
      2. 每个词用双引号包裹（FTS5 字符串字面量），转义内部双引号；
      3. 多词用 `` OR `` 连接（任一命中即可）；
      4. 空查询返回空字符串（调用方据此跳过 FTS5 路径）。
    """
    if not query or not query.strip():
        return ""
    tokens = query.strip().split()
    if not tokens:
        return ""
    safe_tokens: list[str] = []
    for token in tokens:
        escaped = token.replace('"', '""')
        safe_tokens.append(f'"{escaped}"')
    return " OR ".join(safe_tokens)
