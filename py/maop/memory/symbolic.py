"""MAOP Symbolic Short-term Memory — 工具结果外置 + 任务状态图。

漏斗式记忆的"符号化短期记忆"层：解决单次任务内**上下文爆炸**问题
（对齐 TencentDB Agent Memory 的设计哲学——"短期上下文治理和长期记忆
同样重要，把工具结果外置、把任务状态符号化，往往比盲目扩大 context
window 更有性价比"）。

两条链路：

1. **工具结果外置 (offload)**：工具/命令的完整输出不塞进上下文，而是
   写入 ``<root>/data/refs/<ref_id>.md``（复用 ``EvidenceStore``），
   上下文里只放一行摘要 + ref 引用号。模型需要细节时按 ref 回查。

2. **任务状态图 (task map)**：把任务进展符号化为 Mermaid 流程图
   （``graph TD``），每轮只注入这张图 + 证据引用，替代堆叠全部日志。
   状态图支持分叉/子任务/完成标记，并可按 ``node_id`` 关联回查原文。

Usage::

    from maop.memory.symbolic import SymbolicMemory

    sym = SymbolicMemory(root_dir="/path/to/MAOP")
    ref = sym.offload_tool_result(tool="grep", tool_input="-r auth",
                                  tool_output="<10000 chars>", session_id="s1")
    sym.update_task_map(session_id="s1", step_id="n1", description="搜 auth 引用")
    sym.mark_done(session_id="s1", step_id="n1")
    mermaid = sym.get_task_map(session_id="s1")   # 注入用的 Mermaid 文本
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from maop.core.backends.db_utils import sqlite_connect
from maop.memory.evidence import EvidenceStore
from maop.memory.shared_db import get_memory_db_path

logger = logging.getLogger(__name__)

# 任务状态
STATUS_TODO = "todo"
STATUS_ACTIVE = "active"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
VALID_STATUSES = frozenset({STATUS_TODO, STATUS_ACTIVE, STATUS_DONE, STATUS_FAILED})

# 单会话任务图节点上限（防止图本身膨胀）。
MAX_NODES_PER_MAP = 50

# 注入摘要中每条证据引用的最大字符数。
REF_SUMMARY_MAX_CHARS = 120


class SymbolicMemory:
    """符号化短期记忆：工具结果外置 + Mermaid 任务状态图。

    Parameters
    ----------
    root_dir : str | Path
        MAOP 项目根目录。
    evidence_store : EvidenceStore | None
        复用的 L0 证据存储（默认懒加载共享实例）。
    """

    def __init__(
        self,
        root_dir: str | Path,
        *,
        evidence_store: EvidenceStore | None = None,
    ) -> None:
        self._root = Path(root_dir)
        self._evidence = evidence_store
        self._db_path = get_memory_db_path()
        self._ensure_db()

    @property
    def evidence(self) -> EvidenceStore:
        """共享的 L0 EvidenceStore 实例（懒加载）。"""
        if self._evidence is None:
            self._evidence = EvidenceStore(root_dir=self._root)
        return self._evidence

    # ── Schema ───────────────────────────────────────────────

    def _ensure_db(self) -> None:
        """初始化 task_maps 表（幂等）。"""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_maps (
                    session_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'todo',
                    parent_id TEXT NOT NULL DEFAULT '',
                    evidence_ref TEXT NOT NULL DEFAULT '',
                    metadata TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, node_id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_taskmap_session
                ON task_maps(session_id, status)
            """)

    # ── 工具结果外置 ─────────────────────────────────────────

    def offload_tool_result(
        self,
        tool: str,
        tool_output: str,
        *,
        tool_input: str = "",
        session_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """把工具/命令输出外置到 refs 文件，返回摘要 + ref 引用。

        Returns
        -------
        dict[str, str]
            ``{"ref_id": ..., "summary": "grep <-r auth> → <首行摘要>"}``
            外置失败时 ``ref_id`` 为空字符串。
        """
        meta = dict(metadata or {})
        meta["tool"] = tool
        meta["tool_input"] = tool_input[:500]
        ref_id = self.evidence.store_evidence(
            tool_output,
            session_id=session_id,
            kind="tool_result",
            source=tool,
            metadata=meta,
        )
        if not ref_id:
            return {"ref_id": "", "summary": ""}
        summary = self._format_tool_summary(tool, tool_input, tool_output)
        return {"ref_id": ref_id, "summary": summary}

    @staticmethod
    def _format_tool_summary(tool: str, tool_input: str, tool_output: str) -> str:
        """生成一行式工具摘要：tool 输入 → 输出首行（截断）。"""
        first_line = tool_output.strip().splitlines()[0] if tool_output.strip() else "(empty)"
        flat = re.sub(r"\s+", " ", first_line)
        if len(flat) > REF_SUMMARY_MAX_CHARS:
            flat = flat[: REF_SUMMARY_MAX_CHARS - 1] + "…"
        arg = f" {tool_input[:60]}" if tool_input else ""
        return f"{tool}{arg} → {flat}"

    # ── 任务状态图 ───────────────────────────────────────────

    def update_task_map(
        self,
        session_id: str,
        step_id: str,
        description: str = "",
        *,
        status: str = STATUS_ACTIVE,
        parent_id: str = "",
        evidence_ref: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """新增/更新一个任务图节点。

        同一 ``(session_id, step_id)`` 幂等：已存在则更新状态与描述。
        """
        if status not in VALID_STATUSES:
            logger.warning("[symbolic] 非法 status=%r，已忽略", status)
            return False
        try:
            with sqlite_connect(self._db_path) as conn:
                count = conn.execute(
                    "SELECT COUNT(*) AS c FROM task_maps WHERE session_id = ?",
                    (session_id,),
                ).fetchone()["c"]
                exists = conn.execute(
                    "SELECT 1 FROM task_maps WHERE session_id = ? AND node_id = ?",
                    (session_id, step_id),
                ).fetchone()
                if count >= MAX_NODES_PER_MAP and not exists:
                    logger.warning("[symbolic] 任务图节点超限 %d，拒绝新增 %s",
                                   MAX_NODES_PER_MAP, step_id)
                    return False
                if exists:
                    conn.execute(
                        """UPDATE task_maps SET status = ?, description = ?,
                           parent_id = ?, evidence_ref = ?, metadata = ?,
                           updated_at = ?
                           WHERE session_id = ? AND node_id = ?""",
                        (status, description, parent_id, evidence_ref,
                         _json_dumps(metadata), _now_iso(),
                         session_id, step_id),
                    )
                else:
                    conn.execute(
                        """INSERT INTO task_maps
                           (session_id, node_id, description, status, parent_id,
                            evidence_ref, metadata, created_at, updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (session_id, step_id, description, status, parent_id,
                         evidence_ref, _json_dumps(metadata), _now_iso(), _now_iso()),
                    )
        except Exception as exc:
            logger.warning("[symbolic] update_task_map 失败: %s", exc)
            return False
        return True

    def mark_done(self, session_id: str, step_id: str) -> bool:
        """标记节点完成。"""
        return self.update_task_map(session_id, step_id, status=STATUS_DONE)

    def mark_failed(self, session_id: str, step_id: str) -> bool:
        """标记节点失败。"""
        return self.update_task_map(session_id, step_id, status=STATUS_FAILED)

    def get_task_map(self, session_id: str) -> str:
        """生成会话的 Mermaid 任务状态图（用于上下文注入）。

        结构::

            ```mermaid
            graph TD
              n1["<description>"]:::active
              n2["<description>"]:::done
            ```

        无节点时返回空字符串。
        """
        try:
            with sqlite_connect(self._db_path) as conn:
                rows = conn.execute(
                    "SELECT node_id, description, status, parent_id, evidence_ref "
                    "FROM task_maps WHERE session_id = ? "
                    "ORDER BY created_at ASC LIMIT ?",
                    (session_id, MAX_NODES_PER_MAP),
                ).fetchall()
        except Exception as exc:
            logger.warning("[symbolic] get_task_map 失败: %s", exc)
            return ""
        if not rows:
            return ""

        lines = ["```mermaid", "graph TD"]
        for row in rows:
            node_id = _safe_node_id(row["node_id"])
            label = _safe_label(row["description"] or node_id)
            status_cls = _status_class(row["status"])
            if row["parent_id"]:
                parent = _safe_node_id(row["parent_id"])
                lines.append(f'  {parent} --> {node_id}["{label}"]'
                             f'{status_cls}{_ref_attr(row["evidence_ref"])}')
            else:
                lines.append(f'  {node_id}["{label}"]{status_cls}'
                             f'{_ref_attr(row["evidence_ref"])}')
        lines.append("```")
        return "\n".join(lines)

    def get_task_map_nodes(self, session_id: str) -> list[dict[str, Any]]:
        """读取会话任务图的节点明细（JSON，供面板/诊断）。"""
        try:
            with sqlite_connect(self._db_path) as conn:
                rows = conn.execute(
                    "SELECT * FROM task_maps WHERE session_id = ? "
                    "ORDER BY created_at ASC LIMIT ?",
                    (session_id, MAX_NODES_PER_MAP),
                ).fetchall()
        except Exception as exc:
            logger.warning("[symbolic] get_task_map_nodes 失败: %s", exc)
            return []
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(item.get("metadata") or "{}")
            except (ValueError, TypeError):
                item["metadata"] = {}
            result.append(item)
        return result

    def build_injection(
        self,
        session_id: str,
        *,
        include_task_map: bool = True,
        include_recent_refs: bool = True,
        recent_ref_top: int = 5,
    ) -> str:
        """构建符号化短期记忆注入块（只放图 + 证据引用，不放全文）。

        Returns
        -------
        str
            形如 ``[Task Map] <mermaid> [Evidence] ref1: 摘要...`` 的文本；
            无可注入内容时返回空字符串。
        """
        parts: list[str] = []
        if include_task_map:
            mermaid = self.get_task_map(session_id)
            if mermaid:
                parts.append(mermaid)
        if include_recent_refs:
            refs = self.evidence.search_evidence(
                session_id=session_id, top=recent_ref_top,
            )
            if refs:
                ref_lines = []
                for r in refs[:recent_ref_top]:
                    rid = r.get("ref_id", "")
                    snippet = (r.get("summary") or "")[:REF_SUMMARY_MAX_CHARS]
                    ref_lines.append(f"- `{rid}`: {snippet}")
                parts.append("[Evidence Refs]\n" + "\n".join(ref_lines))
        return "\n\n".join(parts) if parts else ""

    def clear_session(self, session_id: str) -> int:
        """清空某会话的任务图（会话结束/重置时调用）。返回删除节点数。"""
        try:
            with sqlite_connect(self._db_path) as conn:
                cursor = conn.execute(
                    "DELETE FROM task_maps WHERE session_id = ?", (session_id,),
                )
                return cursor.rowcount
        except Exception as exc:
            logger.warning("[symbolic] clear_session 失败: %s", exc)
            return 0

    def stats(self) -> dict[str, Any]:
        """符号化短期记忆统计。"""
        try:
            with sqlite_connect(self._db_path) as conn:
                sessions = conn.execute(
                    "SELECT COUNT(DISTINCT session_id) AS c FROM task_maps"
                ).fetchone()["c"]
                nodes = conn.execute(
                    "SELECT COUNT(*) AS c FROM task_maps"
                ).fetchone()["c"]
                by_status_rows = conn.execute(
                    "SELECT status, COUNT(*) AS c FROM task_maps GROUP BY status"
                ).fetchall()
        except Exception as exc:
            logger.warning("[symbolic] stats 失败: %s", exc)
            return {"sessions": 0, "nodes": 0}
        return {
            "sessions": sessions,
            "nodes": nodes,
            "by_status": {r["status"]: r["c"] for r in by_status_rows},
        }


# ── 小工具 ───────────────────────────────────────────────────

def _safe_node_id(node_id: str) -> str:
    """Mermaid 节点 ID 只允许字母数字，防注入。"""
    safe = re.sub(r"[^A-Za-z0-9_]", "_", node_id or "n")
    return safe or "n"


def _safe_label(desc: str) -> str:
    """清洗 Mermaid 标签：去引号 + 截断 + 过滤语法字符。

    防止 description 含 ``-->`` / ``class`` / ``subgraph`` 等关键字
    造成 Mermaid 语法注入（破坏图结构或注入新节点/类定义）。
    """
    s = (desc or "").replace('"', "'").replace("\n", " ")
    # 过滤 Mermaid 语法字符（用 Unicode 连字符替代 ASCII 连字符）
    for kw in ("-->", "---", "-.", "class ", "end", "subgraph", "graph "):
        s = s.replace(kw, kw.replace("-", "\u2010"))
    return s[:80]  # 截断防长标签


def _status_class(status: str) -> str:
    """状态 → Mermaid class 后缀。"""
    return {STATUS_ACTIVE: ":::active", STATUS_DONE: ":::done",
            STATUS_FAILED: ":::failed"}.get(status, "")


def _ref_attr(evidence_ref: str) -> str:
    """evidence_ref → Mermaid 节点附加引用（若无则空串）。"""
    if not evidence_ref or not re.match(r"^[A-Za-z0-9_-]+$", evidence_ref):
        return ""
    return f"<br/><font size='1'>ref:{evidence_ref}</font>"


def _json_dumps(data: dict[str, Any] | None) -> str:
    if not data:
        return "{}"
    try:
        return json.dumps(data, ensure_ascii=False)
    except (TypeError, ValueError):
        return "{}"


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
