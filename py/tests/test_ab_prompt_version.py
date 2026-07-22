"""Tests for A/B Testing and Prompt Version Management."""

import shutil
import tempfile

import pytest

from maop.core.ab_test import ABTestManager
from maop.core.prompt_version import PromptVersionManager


@pytest.fixture
def ab_env():
    tmpdir = tempfile.mkdtemp()
    mgr = ABTestManager(root_dir=tmpdir)
    yield mgr
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def pv_env():
    tmpdir = tempfile.mkdtemp()
    mgr = PromptVersionManager(root_dir=tmpdir)
    yield mgr
    shutil.rmtree(tmpdir, ignore_errors=True)


# ── A/B Test ──────────────────────────────────────────────────

class TestABTest:
    def test_create_experiment(self, ab_env):
        config = ab_env.create_experiment("test1", {"control": 50, "treatment": 50})
        assert config.name == "test1"
        assert config.variants["control"] == 50

    def test_create_invalid_percentages(self, ab_env):
        with pytest.raises(ValueError, match="sum to 100"):
            ab_env.create_experiment("bad", {"a": 50, "b": 30})

    def test_assign_deterministic(self, ab_env):
        ab_env.create_experiment("exp1", {"A": 50, "B": 50})
        v1 = ab_env.assign("exp1", "user-1")
        v2 = ab_env.assign("exp1", "user-1")
        assert v1 == v2

    def test_assign_not_found(self, ab_env):
        with pytest.raises(ValueError, match="not found"):
            ab_env.assign("nonexistent", "user-1")

    def test_record_and_evaluate(self, ab_env):
        ab_env.create_experiment("exp2", {"A": 50, "B": 50})
        for i in range(50):
            v = ab_env.assign("exp2", f"u{i}")
            ab_env.record("exp2", v, f"u{i}", success=(i % 3 != 0))
        result = ab_env.evaluate("exp2")
        assert len(result.variants) == 2
        assert all(v.samples > 0 for v in result.variants)

    def test_evaluate_insufficient_data(self, ab_env):
        ab_env.create_experiment("exp3", {"A": 50, "B": 50})
        result = ab_env.evaluate("exp3")
        assert not result.is_significant


# ── Prompt Version ────────────────────────────────────────────

class TestPromptVersion:
    def test_create_and_get(self, pv_env):
        v1 = pv_env.create("greeting", "Hello!")
        current = pv_env.get_current("greeting")
        assert current is not None
        assert current.content == "Hello!"
        assert current.id == v1.id

    def test_version_chain(self, pv_env):
        v1 = pv_env.create("sys", "v1 content")
        v2 = pv_env.create("sys", "v2 content", parent_version=v1.id)
        assert v2.parent_version == v1.id
        current = pv_env.get_current("sys")
        assert current.id == v2.id

    def test_rollback(self, pv_env):
        v1 = pv_env.create("prompt", "original")
        v2 = pv_env.create("prompt", "updated", parent_version=v1.id)
        result = pv_env.rollback("prompt", v2.id)
        assert result is not None
        assert result.id == v1.id
        current = pv_env.get_current("prompt")
        assert current.id == v1.id

    def test_rollback_no_parent(self, pv_env):
        v1 = pv_env.create("solo", "only version")
        result = pv_env.rollback("solo", v1.id)
        assert result is None

    def test_list_versions(self, pv_env):
        pv_env.create("multi", "v1")
        pv_env.create("multi", "v2")
        versions = pv_env.list_versions("multi")
        assert len(versions) == 2

    def test_find_by_tag(self, pv_env):
        pv_env.create("tagged", "content", tags=["production", "v2"])
        pv_env.create("other", "content", tags=["staging"])
        results = pv_env.find_by_tag("production")
        assert len(results) == 1
        assert results[0].prompt_name == "tagged"