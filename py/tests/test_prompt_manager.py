"""Tests for MAOP.prompt_manager — Template CRUD, versioning, rendering, search, import/export."""

from __future__ import annotations

from pathlib import Path

import pytest

from maop.prompt_manager import (
    PromptManager,
    PromptTemplate,
    PromptVersion,
    RenderResult,
)

# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture
def mgr(tmp_path: Path) -> PromptManager:
    """Create a PromptManager with a temp root."""
    return PromptManager(root_dir=tmp_path)


# ── Model tests ───────────────────────────────────────────────

class TestModels:
    def test_prompt_version_defaults(self):
        v = PromptVersion()
        assert v.version == "1.0"
        assert v.content == ""
        assert v.variables == {}

    def test_prompt_template_defaults(self):
        t = PromptTemplate()
        assert t.category == "general"
        assert t.tags == []
        assert t.versions == []
        assert t.current_version == "1.0"

    def test_render_result_defaults(self):
        r = RenderResult()
        assert r.ok is True
        assert r.variables_missing == []


# ── Create & Get ──────────────────────────────────────────────

class TestCreateGet:
    def test_create_returns_id(self, mgr: PromptManager):
        tid = mgr.create("tpl1", content="Hello {{name}}")
        assert tid == "tpl1"

    def test_get_current_version(self, mgr: PromptManager):
        mgr.create("tpl1", content="Hello {{name}}", variables={"name": "str"})
        ver = mgr.get("tpl1")
        assert ver is not None
        assert ver.content == "Hello {{name}}"
        assert ver.version == "1.0"

    def test_get_specific_version(self, mgr: PromptManager):
        mgr.create("tpl1", content="v1", version="1.0")
        mgr.create("tpl1", content="v2", version="2.0")
        ver = mgr.get("tpl1", version="1.0")
        assert ver.content == "v1"
        ver2 = mgr.get("tpl1", version="2.0")
        assert ver2.content == "v2"

    def test_get_nonexistent(self, mgr: PromptManager):
        assert mgr.get("nope") is None

    def test_get_nonexistent_version(self, mgr: PromptManager):
        mgr.create("tpl1", content="hello")
        assert mgr.get("tpl1", version="9.9") is None

    def test_create_upsert(self, mgr: PromptManager):
        mgr.create("tpl1", content="first", name="First")
        mgr.create("tpl1", content="second", name="Second", category="updated")
        ver = mgr.get("tpl1")
        assert ver.content == "second"
        templates = mgr.list_templates()
        assert len(templates) == 1
        assert templates[0].category == "updated"

    def test_create_with_tags_and_category(self, mgr: PromptManager):
        mgr.create("tpl1", content="c", tags=["a", "b"], category="special")
        templates = mgr.list_templates()
        assert templates[0].tags == ["a", "b"]
        assert templates[0].category == "special"


# ── List & Delete ─────────────────────────────────────────────

class TestListDelete:
    def test_list_empty(self, mgr: PromptManager):
        assert mgr.list_templates() == []

    def test_list_multiple(self, mgr: PromptManager):
        for i in range(3):
            mgr.create(f"tpl{i}", content=f"content{i}")
        templates = mgr.list_templates()
        assert len(templates) == 3

    def test_list_by_category(self, mgr: PromptManager):
        mgr.create("a", content="x", category="cat1")
        mgr.create("b", content="y", category="cat2")
        result = mgr.list_templates(category="cat1")
        assert len(result) == 1
        assert result[0].id == "a"

    def test_list_with_versions(self, mgr: PromptManager):
        mgr.create("tpl1", content="v1", version="1.0")
        mgr.create("tpl1", content="v2", version="2.0")
        templates = mgr.list_templates()
        assert len(templates[0].versions) == 2

    def test_delete_existing(self, mgr: PromptManager):
        mgr.create("tpl1", content="hello")
        assert mgr.delete("tpl1") is True
        assert mgr.get("tpl1") is None

    def test_delete_nonexistent(self, mgr: PromptManager):
        assert mgr.delete("nope") is False

    def test_delete_removes_versions(self, mgr: PromptManager):
        mgr.create("tpl1", content="v1", version="1.0")
        mgr.create("tpl1", content="v2", version="2.0")
        mgr.delete("tpl1")
        templates = mgr.list_templates()
        assert len(templates) == 0


# ── Render ────────────────────────────────────────────────────

class TestRender:
    def test_render_basic(self, mgr: PromptManager):
        mgr.create("tpl1", content="Hello {{name}}!")
        result = mgr.render("tpl1", render_vars={"name": "World"})
        assert result.ok is True
        assert result.content == "Hello World!"
        assert result.variables_used == {"name": "World"}
        assert result.variables_missing == []

    def test_render_missing_var(self, mgr: PromptManager):
        mgr.create("tpl1", content="Hello {{name}} and {{place}}!")
        result = mgr.render("tpl1", render_vars={"name": "World"})
        assert result.ok is True
        assert "World" in result.content
        assert "place" in result.variables_missing

    def test_render_no_vars(self, mgr: PromptManager):
        mgr.create("tpl1", content="static content")
        result = mgr.render("tpl1")
        assert result.ok is True
        assert result.content == "static content"

    def test_render_nonexistent_template(self, mgr: PromptManager):
        result = mgr.render("nope")
        assert result.ok is False
        assert "not found" in result.error

    def test_render_specific_version(self, mgr: PromptManager):
        mgr.create("tpl1", content="v1 {{x}}", version="1.0")
        mgr.create("tpl1", content="v2 {{x}}", version="2.0")
        result = mgr.render("tpl1", render_vars={"x": "Y"}, version="2.0")
        assert result.content == "v2 Y"

    def test_render_multiple_same_var(self, mgr: PromptManager):
        mgr.create("tpl1", content="{{x}} and {{x}}")
        result = mgr.render("tpl1", render_vars={"x": "A"})
        assert result.content == "A and A"


# ── Test (validation) ─────────────────────────────────────────

class TestTestMethod:
    def test_test_basic(self, mgr: PromptManager):
        mgr.create("tpl1", content="{{a}} {{b}}", variables={"a": "str", "b": "str"})
        result = mgr.test("tpl1")
        assert result["ok"] is True
        assert set(result["used_variables"]) == {"a", "b"}
        assert result["undeclared"] == []

    def test_test_undeclared_vars(self, mgr: PromptManager):
        mgr.create("tpl1", content="{{a}} {{b}}", variables={"a": "str"})
        result = mgr.test("tpl1")
        assert "b" in result["undeclared"]

    def test_test_unused_declared(self, mgr: PromptManager):
        mgr.create("tpl1", content="{{a}}", variables={"a": "str", "b": "str"})
        result = mgr.test("tpl1")
        assert "b" in result["unused"]

    def test_test_nonexistent(self, mgr: PromptManager):
        result = mgr.test("nope")
        assert result["ok"] is False


# ── Search ────────────────────────────────────────────────────

class TestSearch:
    def test_search_by_name(self, mgr: PromptManager):
        mgr.create("tpl1", content="c", name="code-review")
        result = mgr.search("code")
        assert len(result) == 1

    def test_search_by_content(self, mgr: PromptManager):
        mgr.create("tpl1", content="review this python code", name="t1")
        result = mgr.search("python")
        assert len(result) >= 1

    def test_search_no_match(self, mgr: PromptManager):
        mgr.create("tpl1", content="hello")
        result = mgr.search("zzznonexistent")
        assert result == []

    def test_search_by_category(self, mgr: PromptManager):
        mgr.create("tpl1", content="c", category="special-cat")
        result = mgr.search("special")
        assert len(result) >= 1


# ── Export & Import ───────────────────────────────────────────

class TestExportImport:
    def test_export_all(self, mgr: PromptManager):
        mgr.create("a", content="ca")
        mgr.create("b", content="cb")
        data = mgr.export_templates()
        assert len(data["prompts"]) == 2
        assert "exported_at" in data

    def test_export_specific(self, mgr: PromptManager):
        mgr.create("a", content="ca")
        mgr.create("b", content="cb")
        data = mgr.export_templates(template_ids=["a"])
        assert len(data["prompts"]) == 1
        assert data["prompts"][0]["id"] == "a"

    def test_import_templates(self, tmp_path: Path):
        mgr1 = PromptManager(root_dir=tmp_path / "m1")
        mgr1.create("tpl1", content="hello {{x}}", variables={"x": "str"},
                     tags=["t1"], category="cat1")
        exported = mgr1.export_templates()

        mgr2 = PromptManager(root_dir=tmp_path / "m2")
        count = mgr2.import_templates(exported)
        assert count == 1
        ver = mgr2.get("tpl1")
        assert ver is not None
        assert ver.content == "hello {{x}}"

    def test_import_empty(self, mgr: PromptManager):
        count = mgr.import_templates({"prompts": []})
        assert count == 0

    def test_import_skips_no_id(self, mgr: PromptManager):
        count = mgr.import_templates({"prompts": [{"content": "x"}]})
        assert count == 0


# ── Stats ─────────────────────────────────────────────────────

class TestStats:
    def test_stats_empty(self, mgr: PromptManager):
        s = mgr.stats()
        assert s["total_templates"] == 0
        assert s["total_versions"] == 0

    def test_stats_with_data(self, mgr: PromptManager):
        mgr.create("a", content="v1", category="cat1")
        mgr.create("b", content="v1", category="cat2")
        mgr.create("b", content="v2", version="2.0", category="cat2")
        s = mgr.stats()
        assert s["total_templates"] == 2
        assert s["total_versions"] == 3
        assert s["by_category"]["cat1"] == 1
        assert s["by_category"]["cat2"] == 1
