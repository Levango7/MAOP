"""MAOP L0 Evidence Layer — 原始对话/工具结果存储与 refs 证据回查链路。

漏斗式记忆的 L0 层：保留**原始证据**（黑匣子），同时把大体积内容
外置到 ``<root>/data/refs/<ref_id>.md`` 文件，DB 只存摘要 + ref 指针，
支持按 ref 回查完整原文。

设计动机（对齐 TencentDB Agent Memory 的漏斗哲学）：
  - **原始证据要保留**：压缩/提炼后的记忆若丢失证据，回查链路就断了。
  - **压缩结果要能回查**：高层记忆（L2/L3）只放摘要，需要细节时按
    ``ref_id`` 回查 L0 原文。
  - **Token 治理**：工具结果/长对话全文不塞进上下文，只放摘要 + 引用号。

Usage::

    from maop.memory.evidence import EvidenceStore

    ev = EvidenceStore(root_dir="/path/to/MAOP")
    ref = ev.store_evidence(session_id="s1", kind="tool_result",
                            content="<long tool output>", source="grep")
    full = ev.get_evidence(ref)          # 回查原文
    hits = ev.search_evidence("auth")    # 按摘要/来源检索
"""

from __future__ import annotations

import logging
import re
import time
import types
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from maop.core.backends.db_utils import sqlite_connect
from maop.memory.shared_db import get_memory_db_path

logger = logging.getLogger(__name__)

# ref_id 只允许 [A-Za-z0-9_-]（与 memory_entries 的 _is_valid_id 同规则，
# 防止路径穿越）。前缀 ev- 便于与其他 ID 区分。
_REF_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")

# 摘要保留的最大字符数（DB 列 + 上下文注入共用）。
DEFAULT_SUMMARY_MAX_CHARS = 500

# 超过该字节数的内容外置到 refs/*.md，DB 只存摘要（防 DB 膨胀）。
DEFAULT_SPILL_THRESHOLD = 4000

# 支持的证据种类。
VALID_KINDS = frozenset({"conversation", "tool_result", "task_map", "document"})


def _new_ref_id() -> str:
    """生成证据 ref_id: ev-<timestamp>-<rand6>。"""
    rand = uuid.uuid4().hex[:6]
    return f"ev-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{rand}"


def _make_summary(content: str, max_chars: int = DEFAULT_SUMMARY_MAX_CHARS) -> str:
    """从原文生成摘要：压平换行 + 截断。"""
    flat = re.sub(r"\s+", " ", content).strip()
    if len(flat) <= max_chars:
        return flat
    return flat[: max_chars - 1] + "…"


class EvidenceStore:
    """L0 原始证据存储：SQLite 摘要索引 + refs/*.md 全文外置。

    Parameters
    ----------
    root_dir : str | Path
        MAOP 项目根目录（``data/refs/`` 目录在其中创建）。
    spill_threshold : int
        内容字节数超过该值时外置到文件，否则全文直接入库。
    """

    def __init__(
        self,
        root_dir: str | Path,
        *,
        spill_threshold: int = DEFAULT_SPILL_THRESHOLD,
    ) -> None:
        self._root = Path(root_dir)
        self._refs_dir = self._root / "data" / "refs"
        self._refs_dir.mkdir(parents=True, exist_ok=True)
        self._spill_threshold = int(spill_threshold)
        self._db_path = get_memory_db_path()
        self._ensure_db()

    # ── Schema ───────────────────────────────────────────────

    def _ensure_db(self) -> None:
        """初始化 l0_evidence 表（幂等）。"""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS l0_evidence (
                    ref_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL DEFAULT 'conversation',
                    summary TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    content_path TEXT NOT NULL DEFAULT '',
                    char_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}'
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_l0_session ON l0_evidence(session_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_l0_kind ON l0_evidence(kind)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_l0_created ON l0_evidence(created_at)
            """)

    # ── 写入 ─────────────────────────────────────────────────

    def store_evidence(
        self,
        content: str,
        *,
        session_id: str = "",
        kind: str = "conversation",
        source: str = "",
        metadata: dict[str, Any] | None = None,
        ref_id: str = "",
    ) -> str:
        """存储一条 L0 原始证据，返回 ref_id（可回查原文）。

        Parameters
        ----------
        content : str
            原始内容（对话、工具输出等）。
        session_id : str
            所属会话。
        kind : str
            证据种类：conversation / tool_result / task_map / document。
        source : str
            来源标识（如工具名、agent 名）。
        metadata : dict | None
            附加元数据（JSON 可序列化）。
        ref_id : str
            显式指定 ref_id（默认自动生成）。仅接受 [A-Za-z0-9_-]。

        Returns
        -------
        str
            生成的 ref_id；校验失败时返回空字符串。
        """
        if not isinstance(content, str):
            logger.warning("[evidence] content 必须为 str，已忽略")
            return ""
        if kind not in VALID_KINDS:
            logger.warning("[evidence] 非法 kind=%r，已忽略", kind)
            return ""

        rid = ref_id or _new_ref_id()
        if not _REF_ID_PATTERN.match(rid):
            logger.warning("[evidence] 非法 ref_id=%r，已拒绝", rid)
            return ""

        # 全文外置 vs 直接入库
        spilled = len(content.encode("utf-8", errors="ignore")) > self._spill_threshold
        content_path = ""
        if spilled:
            content_path = self._spill_to_file(rid, content)
            if not content_path:
                logger.warning("[evidence] 外置文件失败，回退入库 %s", rid)
                spilled = False

        summary = _make_summary(content)
        now = datetime.now(timezone.utc).isoformat()
        meta_json = _json_dumps(metadata)
        char_count = len(content)

        try:
            with sqlite_connect(self._db_path) as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO l0_evidence
                       (ref_id, session_id, kind, summary, source,
                        content_path, char_count, created_at, metadata)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (rid, session_id, kind, summary, source,
                     content_path, char_count, now, meta_json),
                )
        except Exception as exc:
            logger.warning("[evidence] 存储失败 %s: %s", rid, exc)
            # 入库失败时清理已外置的文件，避免孤儿文件
            if content_path:
                with _suppress_oserror():
                    Path(content_path).unlink(missing_ok=True)
            return ""

        logger.info("[evidence] 存储 %s (kind=%s, chars=%d, spilled=%s)",
                    rid, kind, char_count, spilled)
        return rid

    def _spill_to_file(self, ref_id: str, content: str) -> str:
        """把全文写入 <root>/data/refs/<ref_id>.md，返回文件路径。"""
        path = self._refs_dir / f"{ref_id}.md"
        try:
            path.write_text(content, encoding="utf-8")
            return str(path)
        except OSError as exc:
            logger.warning("[evidence] 写 refs 文件失败 %s: %s", path, exc)
            return ""

    # ── 读取 / 回查 ──────────────────────────────────────────

    def get_evidence(self, ref_id: str) -> str:
        """按 ref_id 回查 L0 原文。

        外置条目从 refs/*.md 读取；未外置条目从 DB 的 summary 列返回。
        查不到返回空字符串。
        """
        if not _REF_ID_PATTERN.match(ref_id):
            return ""
        try:
            with sqlite_connect(self._db_path) as conn:
                row = conn.execute(
                    "SELECT summary, content_path FROM l0_evidence WHERE ref_id = ?",
                    (ref_id,),
                ).fetchone()
        except Exception as exc:
            logger.warning("[evidence] 查询失败 %s: %s", ref_id, exc)
            return ""

        if row is None:
            return ""
        summary = str(row["summary"] or "")
        content_path = str(row["content_path"] or "")
        if content_path:
            try:
                return Path(content_path).read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("[evidence] 回查文件失败 %s: %s", content_path, exc)
                return summary
        return summary

    def get_evidence_meta(self, ref_id: str) -> dict[str, Any] | None:
        """按 ref_id 读取证据元数据（不含原文）。"""
        if not _REF_ID_PATTERN.match(ref_id):
            return None
        try:
            with sqlite_connect(self._db_path) as conn:
                row = conn.execute(
                    "SELECT * FROM l0_evidence WHERE ref_id = ?", (ref_id,),
                ).fetchone()
        except Exception as exc:
            logger.warning("[evidence] 查询失败 %s: %s", ref_id, exc)
            return None
        if row is None:
            return None
        result = dict(row)
        try:
            import json
            result["metadata"] = json.loads(result.get("metadata") or "{}")
        except (ValueError, TypeError):
            result["metadata"] = {}
        return result

    def search_evidence(
        self,
        query: str = "",
        *,
        session_id: str = "",
        kind: str = "",
        top: int = 10,
    ) -> list[dict[str, Any]]:
        """按关键词/会话/种类检索证据摘要，返回含 ref_id 的条目列表。

        检索命中后上层可调用 ``get_evidence(ref_id)`` 回查完整原文——
        即"先高层定位，再底层核验"的漏斗回查链路。
        """
        sql = "SELECT ref_id, session_id, kind, summary, source, char_count, created_at FROM l0_evidence"
        where: list[str] = []
        params: list[Any] = []
        if query:
            where.append("(summary LIKE ? OR source LIKE ?)")
            params.extend([f"%{query}%", f"%{query}%"])
        if session_id:
            where.append("session_id = ?")
            params.append(session_id)
        if kind:
            where.append("kind = ?")
            params.append(kind)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, int(top)))
        try:
            with sqlite_connect(self._db_path) as conn:
                cursor = conn.execute(sql, params)
                cols = [d[0] for d in cursor.description] if cursor.description else []
                return [dict(zip(cols, row)) for row in cursor.fetchall()]
        except Exception as exc:
            logger.warning("[evidence] 检索失败: %s", exc)
            return []

    def delete_evidence(self, ref_id: str) -> bool:
        """删除证据（DB 行 + 外置文件，幂等）。"""
        if not _REF_ID_PATTERN.match(ref_id):
            return False
        try:
            with sqlite_connect(self._db_path) as conn:
                row = conn.execute(
                    "SELECT content_path FROM l0_evidence WHERE ref_id = ?",
                    (ref_id,),
                ).fetchone()
                conn.execute("DELETE FROM l0_evidence WHERE ref_id = ?", (ref_id,))
        except Exception as exc:
            logger.warning("[evidence] 删除失败 %s: %s", ref_id, exc)
            return False
        if row and row["content_path"]:
            with _suppress_oserror():
                Path(row["content_path"]).unlink(missing_ok=True)
        return True

    def prune(
        self,
        *,
        older_than_days: float = 90.0,
        session_id: str = "",
        kind: str = "",
        limit: int = 500,
    ) -> int:
        """清理过期证据（默认 90 天前）。返回删除条数。"""
        cutoff = time.time() - older_than_days * 86400
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
        sql = "SELECT ref_id FROM l0_evidence WHERE created_at < ?"
        params: list[Any] = [cutoff_iso]
        if session_id:
            sql += " AND session_id = ?"
            params.append(session_id)
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        sql += " LIMIT ?"
        params.append(max(1, int(limit)))
        try:
            with sqlite_connect(self._db_path) as conn:
                rows = conn.execute(sql, params).fetchall()
                for row in rows:
                    conn.execute("DELETE FROM l0_evidence WHERE ref_id = ?", (row["ref_id"],))
        except Exception as exc:
            logger.warning("[evidence] prune 失败: %s", exc)
            return 0
        deleted = len(rows)
        # 清理孤儿 refs 文件（外置但 DB 已删）
        if deleted:
            self._cleanup_orphan_files()
        logger.info("[evidence] prune 删除 %d 条", deleted)
        return deleted

    def stats(self) -> dict[str, Any]:
        """L0 证据层统计。"""
        try:
            with sqlite_connect(self._db_path) as conn:
                total = conn.execute("SELECT COUNT(*) AS c FROM l0_evidence").fetchone()["c"]
                by_kind_rows = conn.execute(
                    "SELECT kind, COUNT(*) AS c FROM l0_evidence GROUP BY kind"
                ).fetchall()
                spilled = conn.execute(
                    "SELECT COUNT(*) AS c FROM l0_evidence WHERE content_path != ''"
                ).fetchone()["c"]
                total_chars = conn.execute(
                    "SELECT COALESCE(SUM(char_count), 0) AS c FROM l0_evidence"
                ).fetchone()["c"]
        except Exception as exc:
            logger.warning("[evidence] stats 失败: %s", exc)
            return {"total": 0}
        return {
            "total": total,
            "by_kind": {r["kind"]: r["c"] for r in by_kind_rows},
            "spilled": spilled,
            "total_chars": total_chars,
        }

    def _cleanup_orphan_files(self) -> int:
        """删除 refs/ 目录中不在 DB 里的孤儿 .md 文件。返回清理数。"""
        cleaned = 0
        try:
            with sqlite_connect(self._db_path) as conn:
                known = {r["ref_id"] for r in conn.execute("SELECT ref_id FROM l0_evidence")}
            for path in self._refs_dir.glob("ev-*.md"):
                if path.stem not in known:
                    with _suppress_oserror():
                        path.unlink(missing_ok=True)
                        cleaned += 1
        except Exception as exc:
            logger.warning("[evidence] 孤儿文件清理失败: %s", exc)
        if cleaned:
            logger.info("[evidence] 清理 %d 个孤儿 refs 文件", cleaned)
        return cleaned


# ── 小工具 ───────────────────────────────────────────────────

def _json_dumps(data: dict[str, Any] | None) -> str:
    """把 metadata 序列化为 JSON 字符串（失败返回 '{}'）。"""
    if not data:
        return "{}"
    try:
        import json
        return json.dumps(data, ensure_ascii=False)
    except (TypeError, ValueError):
        return "{}"


class _suppress_oserror:
    """上下文管理器：吞掉 OSError（文件清理场景不允许抛异常）。"""

    def __enter__(self) -> None:
        return None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: types.TracebackType | None,
    ) -> bool:
        if exc_type is OSError:
            logger.debug("[evidence] 忽略 OSError: %s", exc)
            return True
        return False
