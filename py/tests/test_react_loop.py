"""Tests for MAOP.core.react_loop, MAOP.core.change_tracker, and MAOP.core.artifact_store."""

from __future__ import annotations


from maop.core.react_loop import ReactLoop, ReactConfig, ReactResult, ReactStep, ReactPhase
from maop.core.change_tracker import ChangeTracker
from maop.core.artifact_store import ArtifactStore


# ── ReactLoop (unit tests, no real LLM) ────────────────────────


class TestReactConfig:
    def test_defaults(self):
        cfg = ReactConfig()
        assert cfg.max_iterations == 10
        assert cfg.max_tool_calls == 30
        assert cfg.provider == "openai"

    def test_custom(self):
        cfg = ReactConfig(max_iterations=5, provider="anthropic")
        assert cfg.max_iterations == 5
        assert cfg.provider == "anthropic"


class TestReactStep:
    def test_thought_step(self):
        step = ReactStep(iteration=0, phase=ReactPhase.THOUGHT, content="I need to fix the bug")
        assert step.phase == ReactPhase.THOUGHT
        assert step.tool_name == ""

    def test_action_step(self):
        step = ReactStep(
            iteration=1, phase=ReactPhase.ACTION,
            tool_name="read_file", tool_args={"path": "/tmp/test.py"},
        )
        assert step.phase == ReactPhase.ACTION
        assert step.tool_name == "read_file"

    def test_observation_step(self):
        step = ReactStep(
            iteration=1, phase=ReactPhase.OBSERVATION,
            tool_result="file contents here",
        )
        assert step.phase == ReactPhase.OBSERVATION
        assert step.tool_result == "file contents here"


class TestReactResult:
    def test_default_result(self):
        result = ReactResult(task="test", agent="mavis")
        assert result.success is False
        assert result.total_iterations == 0
        assert result.steps == []

    def test_with_steps(self):
        result = ReactResult(
            task="fix bug", agent="mavis",
            steps=[
                ReactStep(iteration=0, phase=ReactPhase.THOUGHT, content="thinking"),
                ReactStep(iteration=0, phase=ReactPhase.FINAL, content="done"),
            ],
            final_answer="done",
            total_iterations=1,
            success=True,
        )
        assert result.success is True
        assert len(result.steps) == 2


class TestReactLoopInit:
    def test_init(self):
        loop = ReactLoop()
        assert loop.config.max_iterations == 10

    def test_custom_config(self):
        cfg = ReactConfig(max_iterations=3)
        loop = ReactLoop(config=cfg)
        assert loop.config.max_iterations == 3


# ── ChangeTracker ───────────────────────────────────────────────


class TestChangeTrackerSnapshot:
    def test_create_snapshot(self, tmp_path):
        tracker = ChangeTracker(root_dir=str(tmp_path))
        workdir = tmp_path / "project"
        workdir.mkdir()
        (workdir / "main.py").write_text("print('hello')", encoding="utf-8")
        snap_id = tracker.snapshot(str(workdir), label="v1")
        assert snap_id.startswith("snap-")
        snap = tracker.get_snapshot(snap_id)
        assert snap is not None
        assert snap.label == "v1"
        assert snap.file_count >= 1

    def test_list_snapshots(self, tmp_path):
        tracker = ChangeTracker(root_dir=str(tmp_path))
        workdir = tmp_path / "project"
        workdir.mkdir()
        (workdir / "a.py").write_text("a", encoding="utf-8")
        tracker.snapshot(str(workdir), label="s1")
        tracker.snapshot(str(workdir), label="s2")
        snaps = tracker.list_snapshots(str(workdir))
        assert len(snaps) >= 2

    def test_empty_directory_snapshot(self, tmp_path):
        tracker = ChangeTracker(root_dir=str(tmp_path))
        workdir = tmp_path / "empty_project"
        workdir.mkdir()
        snap_id = tracker.snapshot(str(workdir), label="empty")
        snap = tracker.get_snapshot(snap_id)
        assert snap is not None
        assert snap.file_count == 0


class TestChangeTrackerDiff:
    def test_detect_added_file(self, tmp_path):
        tracker = ChangeTracker(root_dir=str(tmp_path))
        workdir = tmp_path / "project"
        workdir.mkdir()
        tracker.snapshot(str(workdir), label="before")
        (workdir / "new_file.py").write_text("new content", encoding="utf-8")
        diff = tracker.diff(str(workdir), since_label="before")
        assert diff.added >= 1
        assert any(c.change_type == "added" for c in diff.changes)

    def test_detect_modified_file(self, tmp_path):
        tracker = ChangeTracker(root_dir=str(tmp_path))
        workdir = tmp_path / "project"
        workdir.mkdir()
        (workdir / "main.py").write_text("original", encoding="utf-8")
        tracker.snapshot(str(workdir), label="before")
        (workdir / "main.py").write_text("modified", encoding="utf-8")
        diff = tracker.diff(str(workdir), since_label="before")
        assert diff.modified >= 1

    def test_detect_deleted_file(self, tmp_path):
        tracker = ChangeTracker(root_dir=str(tmp_path))
        workdir = tmp_path / "project"
        workdir.mkdir()
        (workdir / "temp.py").write_text("temp", encoding="utf-8")
        tracker.snapshot(str(workdir), label="before")
        (workdir / "temp.py").unlink()
        diff = tracker.diff(str(workdir), since_label="before")
        assert diff.deleted >= 1

    def test_unauthorized_changes(self, tmp_path):
        tracker = ChangeTracker(root_dir=str(tmp_path))
        workdir = tmp_path / "project"
        workdir.mkdir()
        tracker.snapshot(str(workdir), label="before")
        (workdir / "unauthorized.py").write_text("bad", encoding="utf-8")
        diff = tracker.diff(str(workdir), since_label="before", authorized_paths=["allowed.py"])
        assert diff.has_unauthorized is True
        assert "unauthorized.py" in diff.unauthorized_paths

    def test_no_changes(self, tmp_path):
        tracker = ChangeTracker(root_dir=str(tmp_path))
        workdir = tmp_path / "project"
        workdir.mkdir()
        (workdir / "stable.py").write_text("stable", encoding="utf-8")
        tracker.snapshot(str(workdir), label="before")
        diff = tracker.diff(str(workdir), since_label="before")
        assert diff.added == 0
        assert diff.modified == 0
        assert diff.deleted == 0


class TestChangeTrackerLog:
    def test_change_log(self, tmp_path):
        tracker = ChangeTracker(root_dir=str(tmp_path))
        workdir = tmp_path / "project"
        workdir.mkdir()
        tracker.snapshot(str(workdir), label="before")
        (workdir / "new.py").write_text("new", encoding="utf-8")
        tracker.diff(str(workdir), since_label="before")
        log = tracker.get_change_log(str(workdir))
        assert len(log) >= 1

    def test_delete_snapshot(self, tmp_path):
        tracker = ChangeTracker(root_dir=str(tmp_path))
        workdir = tmp_path / "project"
        workdir.mkdir()
        snap_id = tracker.snapshot(str(workdir), label="delete-me")
        assert tracker.delete_snapshot(snap_id) is True
        assert tracker.get_snapshot(snap_id) is None


# ── ArtifactStore ───────────────────────────────────────────────


class TestArtifactSave:
    def test_save_and_load(self, tmp_path):
        store = ArtifactStore(root_dir=str(tmp_path))
        v = store.save("main.py", content="print('hello')")
        assert v == 1
        content = store.load("main.py")
        assert content == "print('hello')"

    def test_save_multiple_versions(self, tmp_path):
        store = ArtifactStore(root_dir=str(tmp_path))
        store.save("config.yaml", content="v1")
        v2 = store.save("config.yaml", content="v2")
        assert v2 == 2
        assert store.load("config.yaml") == "v2"
        assert store.load("config.yaml", version=1) == "v1"

    def test_save_with_tag(self, tmp_path):
        store = ArtifactStore(root_dir=str(tmp_path))
        store.save("main.py", content="hello", tag="stable")
        content = store.get_by_tag("main.py", "stable")
        assert content == "hello"


class TestArtifactHistory:
    def test_history(self, tmp_path):
        store = ArtifactStore(root_dir=str(tmp_path))
        store.save("main.py", content="v1")
        store.save("main.py", content="v2")
        store.save("main.py", content="v3")
        history = store.history("main.py")
        assert len(history) == 3
        assert history[0].version == 3  # Latest first

    def test_list_artifacts(self, tmp_path):
        store = ArtifactStore(root_dir=str(tmp_path))
        store.save("a.py", content="a")
        store.save("b.py", content="b")
        artifacts = store.list_artifacts()
        assert len(artifacts) == 2


class TestArtifactRestore:
    def test_restore(self, tmp_path):
        store = ArtifactStore(root_dir=str(tmp_path))
        store.save("main.py", content="original")
        store.save("main.py", content="modified")
        ok = store.restore("main.py", version=1)
        assert ok is True
        history = store.history("main.py")
        assert len(history) == 3  # v1, v2, v3 (restored)
        assert store.load("main.py") == "original"


class TestArtifactDiff:
    def test_diff_versions(self, tmp_path):
        store = ArtifactStore(root_dir=str(tmp_path))
        store.save("main.py", content="v1")
        store.save("main.py", content="v2")
        diff = store.diff_versions("main.py", 1, 2)
        assert diff["changed"] is True
        assert diff["v1_hash"] != diff["v2_hash"]

    def test_diff_same_version(self, tmp_path):
        store = ArtifactStore(root_dir=str(tmp_path))
        store.save("main.py", content="same")
        diff = store.diff_versions("main.py", 1, 1)
        assert diff["changed"] is False


class TestArtifactDelete:
    def test_delete_artifact(self, tmp_path):
        store = ArtifactStore(root_dir=str(tmp_path))
        store.save("temp.py", content="temp")
        assert store.delete_artifact("temp.py") is True
        assert store.load("temp.py") is None
        assert store.delete_artifact("temp.py") is False

    def test_load_nonexistent(self, tmp_path):
        store = ArtifactStore(root_dir=str(tmp_path))
        assert store.load("nonexistent.py") is None


class TestArtifactTag:
    def test_tag_version(self, tmp_path):
        store = ArtifactStore(root_dir=str(tmp_path))
        store.save("main.py", content="hello")
        assert store.tag_version("main.py", 1, "release") is True
        content = store.get_by_tag("main.py", "release")
        assert content == "hello"

    def test_get_by_nonexistent_tag(self, tmp_path):
        store = ArtifactStore(root_dir=str(tmp_path))
        store.save("main.py", content="hello")
        assert store.get_by_tag("main.py", "nonexistent") is None