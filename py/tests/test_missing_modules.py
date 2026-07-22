"""Tests for 4 missing modules: human_proxy, sandbox, tool_manager, prompt_manager."""

import tempfile
from pathlib import Path

import pytest


# ═══════════════════════════════════════════════════════════════
# HumanProxy Tests
# ═══════════════════════════════════════════════════════════════

class TestHumanProxy:
    def setup_method(self):
        self._tmp = tempfile.mkdtemp()
        from maop.core.human_proxy import HumanProxy
        self.proxy = HumanProxy(root_dir=self._tmp)

    def test_request(self):
        req_id = self.proxy.request(task="Deploy to prod", agent="claude", reason="Production deployment")
        assert req_id.startswith("hr-")
        req = self.proxy.get(req_id)
        assert req is not None
        assert req.task == "Deploy to prod"
        assert req.status == "pending"
        assert req.priority == "medium"

    def test_request_with_priority(self):
        req_id = self.proxy.request(task="Urgent fix", priority="critical")
        req = self.proxy.get(req_id)
        assert req.priority == "critical"

    def test_approve(self):
        req_id = self.proxy.request(task="Test task")
        assert self.proxy.approve(req_id) is True
        req = self.proxy.get(req_id)
        assert req.status == "approved"
        assert req.resolved is not None

    def test_reject(self):
        req_id = self.proxy.request(task="Test task")
        assert self.proxy.reject(req_id, reason="Not ready") is True
        req = self.proxy.get(req_id)
        assert req.status == "rejected"

    def test_approve_not_found(self):
        assert self.proxy.approve("nonexistent") is False

    def test_pending(self):
        self.proxy.request(task="Low", priority="low")
        self.proxy.request(task="Critical", priority="critical")
        self.proxy.request(task="Medium", priority="medium")
        pending = self.proxy.pending()
        assert len(pending) == 3
        # Sorted by priority: critical first
        assert pending[0].priority == "critical"

    def test_list_all(self):
        self.proxy.request(task="T1")
        req2 = self.proxy.request(task="T2")
        self.proxy.approve(req2)
        all_reqs = self.proxy.list_all()
        assert len(all_reqs) == 2
        pending_only = self.proxy.list_all(status="pending")
        assert len(pending_only) == 1

    def test_resolve(self):
        req_id = self.proxy.request(task="Test")
        assert self.proxy.resolve(req_id, "approve") is True
        assert self.proxy.get(req_id).status == "approved"

    def test_stats(self):
        self.proxy.request(task="T1")
        req2 = self.proxy.request(task="T2")
        self.proxy.approve(req2)
        stats = self.proxy.stats()
        assert stats.get("pending", 0) == 1
        assert stats.get("approved", 0) == 1


# ═══════════════════════════════════════════════════════════════
# SandboxManager Tests
# ═══════════════════════════════════════════════════════════════

class TestSandboxManager:
    def setup_method(self):
        self._tmp = tempfile.mkdtemp()
        from maop.core.sandbox import SandboxManager
        self.mgr = SandboxManager(root_dir=self._tmp)

    def test_create(self):
        sb = self.mgr.create()
        assert sb.id.startswith("sb-")
        assert sb.status == "active"
        assert Path(sb.path).exists()
        assert (Path(sb.path) / "input").exists()
        assert (Path(sb.path) / "output").exists()

    def test_create_with_id(self):
        sb = self.mgr.create(sandbox_id="my-sandbox")
        assert sb.id == "my-sandbox"

    def test_create_invalid_id(self):
        with pytest.raises(ValueError):
            self.mgr.create(sandbox_id="../evil")

    def test_get(self):
        sb = self.mgr.create()
        info = self.mgr.get(sb.id)
        assert info is not None
        assert info.id == sb.id

    def test_list(self):
        self.mgr.create()
        self.mgr.create()
        sandboxes = self.mgr.list_all()
        assert len(sandboxes) == 2

    def test_cleanup(self):
        sb = self.mgr.create()
        assert self.mgr.cleanup(sb.id) is True
        info = self.mgr.get(sb.id)
        assert info.status == "cleaned"
        assert not Path(sb.path).exists()

    def test_run_command(self):
        sb = self.mgr.create()
        result = self.mgr.run(sb.id, command="echo hello")
        assert result.ok is True
        assert result.exit_code == 0
        assert result.duration_ms > 0

    def test_run_not_found(self):
        result = self.mgr.run("nonexistent", command="echo hi")
        assert result.ok is False

    def test_stats(self):
        self.mgr.create()
        stats = self.mgr.stats()
        assert stats.get("active", 0) >= 1


# ═══════════════════════════════════════════════════════════════
# ToolManager Tests
# ═══════════════════════════════════════════════════════════════

class TestToolManager:
    def setup_method(self):
        self._tmp = tempfile.mkdtemp()
        from maop.core.tool_manager import ToolManager
        self.mgr = ToolManager(root_dir=self._tmp)

    def test_register(self):
        tool_id = self.mgr.register("lint", command="ruff check", category="quality")
        assert tool_id == "lint"
        info = self.mgr.info("lint")
        assert info is not None
        assert info.command == "ruff check"
        assert info.category == "quality"

    def test_register_with_params(self):
        self.mgr.register("deploy", command="deploy.sh", params={"env": "staging"})
        info = self.mgr.info("deploy")
        assert info.params["env"] == "staging"

    def test_list(self):
        self.mgr.register("lint", command="ruff", category="quality")
        self.mgr.register("test", command="pytest", category="quality")
        self.mgr.register("build", command="make", category="build")
        result = self.mgr.list()
        assert len(result) == 2  # 2 categories
        cats = {r["category"] for r in result}
        assert cats == {"quality", "build"}

    def test_find(self):
        self.mgr.register("ruff-lint", command="ruff", description="Python linter")
        results = self.mgr.find("linter")
        assert len(results) == 1
        assert results[0].id == "ruff-lint"

    def test_enable_disable(self):
        self.mgr.register("tool1", command="echo")
        assert self.mgr.disable("tool1") is True
        info = self.mgr.info("tool1")
        assert info.enabled is False
        assert self.mgr.enable("tool1") is True
        info = self.mgr.info("tool1")
        assert info.enabled is True

    def test_delete(self):
        self.mgr.register("tool1", command="echo")
        assert self.mgr.delete("tool1") is True
        assert self.mgr.info("tool1") is None

    async def test_call_disabled(self):
        # F7d (2026-07-22, Phase F): now async — mgr.call is async.
        self.mgr.register("tool1", command="echo")
        self.mgr.disable("tool1")
        result = await self.mgr.call("tool1")
        assert result.ok is False
        assert "disabled" in result.error

    async def test_call_not_found(self):
        # F7d (Phase F): now async.
        result = await self.mgr.call("nonexistent")
        assert result.ok is False

    def test_stats(self):
        self.mgr.register("lint", command="ruff", category="quality")
        self.mgr.register("build", command="make", category="build")
        stats = self.mgr.stats()
        assert stats["total"] == 2
        assert stats["enabled"] == 2


# ═══════════════════════════════════════════════════════════════
# PromptManager Tests
# ═══════════════════════════════════════════════════════════════

class TestPromptManager:
    def setup_method(self):
        self._tmp = tempfile.mkdtemp()
        from maop.prompt_manager import PromptManager
        self.mgr = PromptManager(root_dir=self._tmp)

    def test_create(self):
        tid = self.mgr.create(
            "code-review",
            content="Review {{language}} code: {{snippet}}",
            variables={"language": "str", "snippet": "str"},
        )
        assert tid == "code-review"

    def test_get(self):
        self.mgr.create("greet", content="Hello {{name}}!", variables={"name": "str"})
        ver = self.mgr.get("greet")
        assert ver is not None
        assert "Hello" in ver.content
        assert ver.version == "1.0"

    def test_list(self):
        self.mgr.create("p1", content="Template 1", category="general")
        self.mgr.create("p2", content="Template 2", category="coding")
        templates = self.mgr.list_templates()
        assert len(templates) == 2

    def test_render(self):
        self.mgr.create(
            "greet",
            content="Hello {{name}}, welcome to {{place}}!",
            variables={"name": "str", "place": "str"},
        )
        result = self.mgr.render("greet", render_vars={"name": "Alice", "place": "MAOP"})
        assert result.ok is True
        assert result.content == "Hello Alice, welcome to MAOP!"
        assert "name" in result.variables_used

    def test_render_missing_vars(self):
        self.mgr.create(
            "greet",
            content="Hello {{name}} from {{place}}!",
            variables={"name": "str", "place": "str"},
        )
        result = self.mgr.render("greet", render_vars={"name": "Alice"})
        assert result.ok is True
        assert "place" in result.variables_missing
        assert "{{place}}" in result.content

    def test_render_not_found(self):
        result = self.mgr.render("nonexistent")
        assert result.ok is False

    def test_test_template(self):
        self.mgr.create(
            "review",
            content="Review {{lang}} code",
            variables={"lang": "str", "extra": "str"},
        )
        test_result = self.mgr.test("review")
        assert test_result["ok"] is True
        assert "lang" in test_result["used_variables"]
        assert "extra" in test_result["unused"]  # declared but not used in template

    def test_search(self):
        self.mgr.create("code-review", content="Review code changes", category="coding")
        self.mgr.create("doc-gen", content="Generate documentation", category="docs")
        results = self.mgr.search("review")
        assert len(results) == 1
        assert results[0].id == "code-review"

    def test_delete(self):
        self.mgr.create("temp", content="Temporary")
        assert self.mgr.delete("temp") is True
        assert self.mgr.get("temp") is None

    def test_export_import(self):
        self.mgr.create("p1", content="Template 1", tags=["test"])
        self.mgr.create("p2", content="Template 2", category="coding")
        exported = self.mgr.export_templates()
        assert len(exported["prompts"]) == 2

        # Import into fresh manager
        from maop.prompt_manager import PromptManager
        mgr2 = PromptManager(root_dir=tempfile.mkdtemp())
        count = mgr2.import_templates(exported)
        assert count == 2
        templates = mgr2.list_templates()
        assert len(templates) == 2

    def test_stats(self):
        self.mgr.create("p1", content="T1", category="general")
        self.mgr.create("p2", content="T2", category="coding")
        stats = self.mgr.stats()
        assert stats["total_templates"] == 2
        assert stats["total_versions"] == 2

    def test_versioning(self):
        self.mgr.create("p1", content="Version 1", version="1.0")
        self.mgr.create("p1", content="Version 2", version="2.0")
        ver1 = self.mgr.get("p1", version="1.0")
        ver2 = self.mgr.get("p1", version="2.0")
        assert ver1.content == "Version 1"
        assert ver2.content == "Version 2"
