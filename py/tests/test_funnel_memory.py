"""Tests for Funnel Memory Enhancement: L0 Evidence / L1 Atom Facts / Symbolic Memory.

覆盖漏斗式记忆增强的三个新模块：
  - ``maop.memory.evidence``  — L0 原始证据 + refs 回查
  - ``maop.memory.atoms``    — L1 原子事实 + 语义指纹去重
  - ``maop.memory.symbolic`` — 符号化短期记忆（工具外置 + 任务图）

以及它们在 ``MemoryManager`` / ``MemoryFacade`` 中的接线。
"""

from __future__ import annotations

import tempfile

import pytest


# 隔离 DB：per-module 模式避免污染真实 maop.db。
# 注意：必须用 autouse fixture 而非模块级 os.environ 赋值——
# 模块级赋值在 pytest 收集阶段（import 时）即生效且永不恢复，
# 会导致同一进程内后续所有测试（test_agent_platform/test_human_proxy/
# test_plugin_cost/test_store/test_tool_manager/test_data_proxy_coverage 等）
# 的 get_db_path() 返回 per-module 路径（如 agent_scanner.db）而非统一
# maop.db，引发 13 个 AssertionError（期望 maop.db 但实际得到 <module>.db）。
# 改为 function-scope autouse fixture 后，monkeypatch 在测试结束自动恢复，
# 不再污染其他测试。
@pytest.fixture(autouse=True)
def _per_module_db(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MAOP_DB_PER_MODULE", "1")
    yield


@pytest.fixture()
def root_dir() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


# ═══════════════════════════════════════════════════════════════════
# L0: EvidenceStore
# ═══════════════════════════════════════════════════════════════════

class TestEvidenceStore:
    def test_small_content_inline(self, root_dir: str):
        from maop.memory.evidence import EvidenceStore

        ev = EvidenceStore(root_dir=root_dir)
        ref = ev.store_evidence("user likes dark mode", session_id="s1", kind="conversation")
        assert ref.startswith("ev-")
        assert ev.get_evidence(ref) == "user likes dark mode"
        assert ev.get_evidence_meta(ref)["content_path"] == ""

    def test_large_content_spilled_to_refs(self, root_dir: str):
        from maop.memory.evidence import EvidenceStore

        ev = EvidenceStore(root_dir=root_dir)
        big = "x" * 10_000
        ref = ev.store_evidence(big, session_id="s1", kind="tool_result", source="grep")
        meta = ev.get_evidence_meta(ref)
        # 外置到 refs/*.md，DB 只存摘要
        assert meta["content_path"] != ""
        assert len(meta["summary"]) <= 500
        # 回查原文完整
        assert ev.get_evidence(ref) == big

    def test_search_and_delete(self, root_dir: str):
        from maop.memory.evidence import EvidenceStore

        ev = EvidenceStore(root_dir=root_dir)
        ev.store_evidence("auth module uses JWT", session_id="s1", kind="conversation")
        ev.store_evidence("deploy to production", session_id="s2", kind="conversation")
        hits = ev.search_evidence("JWT")
        assert len(hits) == 1
        assert hits[0]["session_id"] == "s1"
        assert ev.delete_evidence(hits[0]["ref_id"]) is True
        assert ev.search_evidence("JWT") == []

    def test_invalid_ref_rejected(self, root_dir: str):
        from maop.memory.evidence import EvidenceStore

        ev = EvidenceStore(root_dir=root_dir)
        assert ev.store_evidence("x", ref_id="../evil") == ""
        assert ev.get_evidence("../evil") == ""
        assert ev.delete_evidence("../evil") is False


# ═══════════════════════════════════════════════════════════════════
# L1: AtomFactStore
# ═══════════════════════════════════════════════════════════════════

class TestAtomFactStore:
    def test_fingerprint_normalization(self):
        from maop.memory.atoms import semantic_fingerprint

        a = semantic_fingerprint("User likes coffee", "likes", "coffee.")
        b = semantic_fingerprint("user likes coffee", "likes", "coffee")
        assert a == b

    def test_dedup_merge(self, root_dir: str):
        from maop.memory.atoms import AtomFactStore

        atoms = AtomFactStore(root_dir=root_dir)
        first = atoms.ingest(
            "database is PostgreSQL 16", source_ref="ev-1", topic="architecture",
        )
        assert first["new"] >= 1
        second = atoms.ingest(
            "The database is PostgreSQL 16", source_ref="ev-2", topic="architecture",
        )
        assert second["merged"] >= 1
        assert atoms.stats()["total"] == 1

    def test_relations_folded_into_facts(self, root_dir: str):
        from maop.memory.atoms import AtomFactStore

        atoms = AtomFactStore(root_dir=root_dir)
        report = atoms.ingest("auth module uses JWT", source_ref="ev-1")
        assert report["extracted"] >= 1
        hits = atoms.search_facts("JWT")
        assert len(hits) >= 1

    def test_promote_facts(self, root_dir: str):
        from maop.memory.atoms import AtomFactStore

        atoms = AtomFactStore(root_dir=root_dir)
        promoted_ids: list[str] = []

        def fake_vector_index(doc_id: str, text: str, metadata=None) -> str:
            promoted_ids.append(doc_id)
            return doc_id

        # 使用能命中抽取模式（is_a）的句子，重复出现以累计 access_count
        for _ in range(3):
            atoms.ingest("database is PostgreSQL 16", source_ref="ev-x")
        report = atoms.promote_facts(min_access=2, vector_index_fn=fake_vector_index)
        assert report["promoted"] >= 1
        assert len(promoted_ids) >= 1


# ═══════════════════════════════════════════════════════════════════
# SymbolicMemory
# ═══════════════════════════════════════════════════════════════════

class TestSymbolicMemory:
    def test_offload_tool_result(self, root_dir: str):
        from maop.memory.symbolic import SymbolicMemory

        sym = SymbolicMemory(root_dir=root_dir)
        big = "line1: found auth ref\n" + "x" * 5000
        out = sym.offload_tool_result(
            tool="grep", tool_input="-r auth", tool_output=big, session_id="s1",
        )
        assert out["ref_id"].startswith("ev-")
        assert "grep" in out["summary"]
        assert len(sym.evidence.get_evidence(out["ref_id"])) == len(big)

    def test_task_map_mermaid(self, root_dir: str):
        from maop.memory.symbolic import SymbolicMemory

        sym = SymbolicMemory(root_dir=root_dir)
        sym.update_task_map("s1", "n1", "扫描 auth 引用", status="active")
        sym.update_task_map("s1", "n2", "修复超时 bug", parent_id="n1")
        sym.mark_done("s1", "n2")
        mermaid = sym.get_task_map("s1")
        assert "graph TD" in mermaid
        assert ":::active" in mermaid
        assert ":::done" in mermaid

    def test_mermaid_injection_safe(self, root_dir: str):
        from maop.memory.symbolic import SymbolicMemory

        sym = SymbolicMemory(root_dir=root_dir)
        payload = 'n1"); alert(1); ("'
        sym.update_task_map("s2", payload, "x")
        mermaid = sym.get_task_map("s2")
        # 载荷的语法字符（分号/括号/引号）不应原样出现；唯一允许的 " 是标签定界符
        stripped = mermaid.replace('["x"]', "")
        assert '"' not in stripped
        assert ";" not in stripped

    def test_clear_session(self, root_dir: str):
        from maop.memory.symbolic import SymbolicMemory

        sym = SymbolicMemory(root_dir=root_dir)
        sym.update_task_map("s1", "n1", "a")
        sym.update_task_map("s1", "n2", "b")
        assert sym.clear_session("s1") == 2
        assert sym.get_task_map("s1") == ""


# ═══════════════════════════════════════════════════════════════════
# LLM 语义去重（方案 A）
# ═══════════════════════════════════════════════════════════════════

class TestLLMDedup:
    """LLM 语义去重开关与降级链路测试。

    覆盖契约：
      - 默认关闭：行为与旧版一致（纯 SHA-256 指纹），judge 不被调用
      - judge=True   → 语义合并（total 不增长）
      - judge=False  → 插入新事实
      - judge=None   → 降级插入新（视为无法判断）
      - judge 抛异常 → 降级插入新，不中断 ingest
    """

    def test_default_off_no_judge_calls(self, root_dir: str):
        from maop.memory.atoms import AtomFactStore

        calls: list[int] = []

        def spy_judge(a, b):
            calls.append(1)
            return True

        atoms = AtomFactStore(root_dir=root_dir, llm_dedup=False, llm_judge=spy_judge)
        atoms.ingest("database is PostgreSQL 16", source_ref="e1")
        # 指纹不同 → 旧行为是插入新（merged=0），judge 不应被调用
        r = atoms.ingest("database is a relational database", source_ref="e2")
        assert r["merged"] == 0
        assert atoms.stats()["total"] == 2
        assert calls == []

    def test_judge_true_merges(self, root_dir: str):
        from maop.memory.atoms import AtomFactStore

        atoms = AtomFactStore(
            root_dir=root_dir, llm_dedup=True, llm_judge=lambda a, b: True,
        )
        atoms.ingest("database is PostgreSQL 16", source_ref="e1")
        r = atoms.ingest("database is a relational database", source_ref="e2")
        assert r["merged"] >= 1
        assert atoms.stats()["total"] == 1

    def test_judge_false_inserts_new(self, root_dir: str):
        from maop.memory.atoms import AtomFactStore

        atoms = AtomFactStore(
            root_dir=root_dir, llm_dedup=True, llm_judge=lambda a, b: False,
        )
        atoms.ingest("database is PostgreSQL 16", source_ref="e1")
        r = atoms.ingest("database is a relational database", source_ref="e2")
        assert r["merged"] == 0
        assert atoms.stats()["total"] == 2

    def test_judge_none_degrades_to_insert(self, root_dir: str):
        from maop.memory.atoms import AtomFactStore

        atoms = AtomFactStore(
            root_dir=root_dir, llm_dedup=True, llm_judge=lambda a, b: None,
        )
        atoms.ingest("database is PostgreSQL 16", source_ref="e1")
        r = atoms.ingest("database is a relational database", source_ref="e2")
        assert r["merged"] == 0
        assert atoms.stats()["total"] == 2

    def test_judge_exception_degrades_to_insert(self, root_dir: str):
        from maop.memory.atoms import AtomFactStore

        def boom(a, b):
            raise RuntimeError("judge boom")

        atoms = AtomFactStore(root_dir=root_dir, llm_dedup=True, llm_judge=boom)
        atoms.ingest("database is PostgreSQL 16", source_ref="e1")
        r = atoms.ingest("database is a relational database", source_ref="e2")
        # judge 异常不中断 ingest，降级为插入新
        assert r["merged"] == 0
        assert atoms.stats()["total"] == 2

    def test_llm_dedup_off_equals_sha256_only(self, root_dir: str):
        """回归验证：开关关闭时与纯 SHA-256 行为完全一致（merge 只发生在指纹相同）。"""
        from maop.memory.atoms import AtomFactStore

        atoms = AtomFactStore(root_dir=root_dir)  # 默认 llm_dedup=False
        # 指纹相同 → merged（去重仍然生效）
        atoms.ingest("database is PostgreSQL 16", source_ref="e1")
        r2 = atoms.ingest("The database is PostgreSQL 16", source_ref="e2")
        assert r2["merged"] >= 1
        assert atoms.stats()["total"] == 1


# ═══════════════════════════════════════════════════════════════════
# MemoryManager LLM 去重开关接线（MAOP_LLM_DEDUP / config 覆盖）
# ═══════════════════════════════════════════════════════════════════

class TestManagerLLMDedup:
    """MemoryManager 层 LLM 去重开关接线测试。

    覆盖：
      - ``MAOP_LLM_DEDUP`` 环境变量开启 → atom_facts.llm_dedup=True
      - 未设置 → False
      - 编程 ``MemoryManagerConfig(llm_dedup=...)`` 优先于环境变量
    """

    def test_env_on_enables_llm_dedup(self, root_dir: str, monkeypatch):
        from maop.memory.manager import MemoryManager

        monkeypatch.setenv("MAOP_LLM_DEDUP", "1")
        mgr = MemoryManager(root_dir=root_dir)
        assert mgr.atom_facts.llm_dedup is True

    def test_env_off_default(self, root_dir: str, monkeypatch):
        from maop.memory.manager import MemoryManager

        monkeypatch.delenv("MAOP_LLM_DEDUP", raising=False)
        mgr = MemoryManager(root_dir=root_dir)
        assert mgr.atom_facts.llm_dedup is False

    def test_config_overrides_env(self, root_dir: str, monkeypatch):
        from maop.memory.manager import MemoryManager, MemoryManagerConfig

        monkeypatch.setenv("MAOP_LLM_DEDUP", "1")
        mgr = MemoryManager(
            root_dir=root_dir, config=MemoryManagerConfig(llm_dedup=False),
        )
        assert mgr.atom_facts.llm_dedup is False

    def test_config_enables_without_env(self, root_dir: str, monkeypatch):
        from maop.memory.manager import MemoryManager, MemoryManagerConfig

        monkeypatch.delenv("MAOP_LLM_DEDUP", raising=False)
        mgr = MemoryManager(
            root_dir=root_dir, config=MemoryManagerConfig(llm_dedup=True),
        )
        assert mgr.atom_facts.llm_dedup is True

    def test_env_false_string(self, root_dir: str, monkeypatch):
        from maop.memory.manager import MemoryManager

        monkeypatch.setenv("MAOP_LLM_DEDUP", "0")
        mgr = MemoryManager(root_dir=root_dir)
        assert mgr.atom_facts.llm_dedup is False


# ═══════════════════════════════════════════════════════════════════
# MemoryManager 接线（漏斗钩子）
# ═══════════════════════════════════════════════════════════════════

class TestFunnelIntegration:
    def test_add_exchange_writes_l0_and_l1(self, root_dir: str):
        from maop.memory.manager import MemoryManager

        mgr = MemoryManager(root_dir=root_dir)
        result = mgr.add_exchange(
            session_id="s1",
            user_msg="the auth module uses JWT",
            assistant_msg="database is PostgreSQL 16",
            agent="mavis",
        )
        assert result["evidence_user_ref"].startswith("ev-")
        assert result["evidence_asst_ref"].startswith("ev-")
        assert "atom_new" in result and "atom_merged" in result

    def test_build_context_injects_funnel(self, root_dir: str):
        from maop.memory.manager import MemoryManager

        mgr = MemoryManager(root_dir=root_dir)
        mgr.add_exchange(
            session_id="s1",
            user_msg="auth module uses JWT",
            assistant_msg="ok",
            agent="mavis",
        )
        ctx = mgr.build_context("s1", query="JWT")
        assert ctx.atom_facts  # L1 命中
        assert ctx.evidence_refs  # L0 引用
        assert "[Known Facts]" in ctx.injected_summary

    def test_facade_passthrough_chat_mode(self, root_dir: str):
        from maop.memory.facade import MemoryFacade

        facade = MemoryFacade(root_dir=root_dir, mode="chat")
        out = facade.offload_tool_result(
            tool="grep", tool_output="x" * 6000, session_id="s1",
        )
        assert out["ref_id"].startswith("ev-")
        assert facade.get_evidence(out["ref_id"]) == "x" * 6000
        assert facade.update_task_map("s1", "n1", "do it") is True
        assert "graph TD" in facade.get_task_map("s1")

    def test_facade_agent_mode_funnel_works(self, root_dir: str):
        """agent 模式下漏斗增强同样可用（Facade 懒加载独立实例）。"""
        from maop.memory.facade import MemoryFacade

        facade = MemoryFacade(root_dir=root_dir, mode="agent")
        # L0 证据 + 符号化外置
        out = facade.offload_tool_result(
            tool="grep", tool_output="x" * 6000, session_id="s1",
        )
        assert out["ref_id"].startswith("ev-")
        assert facade.get_evidence(out["ref_id"]) == "x" * 6000
        # 任务状态图
        assert facade.update_task_map("s1", "n1", "do it") is True
        assert "graph TD" in facade.get_task_map("s1")
        # L1 原子事实检索（空查询不报错，返回 list）
        assert isinstance(facade.search_facts("x"), list)

    def test_facade_agent_mode_evidence_and_atoms_instances(self, root_dir: str):
        """agent 模式下 evidence_store/atom_facts/symbolic 返回独立实例。"""
        from maop.memory.atoms import AtomFactStore
        from maop.memory.evidence import EvidenceStore
        from maop.memory.facade import MemoryFacade
        from maop.memory.symbolic import SymbolicMemory

        facade = MemoryFacade(root_dir=root_dir, mode="agent")
        assert isinstance(facade.evidence_store(), EvidenceStore)
        assert isinstance(facade.atom_facts(), AtomFactStore)
        assert isinstance(facade.symbolic(), SymbolicMemory)
        # symbolic 复用同一个 evidence_store 实例
        assert facade.symbolic().evidence is facade.evidence_store()
        # 重复访问返回同一实例（懒加载缓存）
        assert facade.evidence_store() is facade.evidence_store()
        assert facade.atom_facts() is facade.atom_facts()
        assert facade.symbolic() is facade.symbolic()
