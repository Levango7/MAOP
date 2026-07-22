"""Tests for Worktree Manager — git-worktree style task branching."""

import shutil
import tempfile

import pytest

from maop.core.worktree import (
    BranchStatus,
    MergeStrategy,
    WorktreeManager,
)


@pytest.fixture
def wt():
    tmpdir = tempfile.mkdtemp()
    mgr = WorktreeManager(root_dir=tmpdir)
    yield mgr
    shutil.rmtree(tmpdir, ignore_errors=True)


class TestCreateRoot:
    def test_create_root(self, wt):
        root_id = wt.create_root("Fix auth bug")
        assert root_id
        branch = wt.get_branch(root_id)
        assert branch is not None
        assert branch.name == "Fix auth bug"
        assert branch.root_id == root_id
        assert branch.status == BranchStatus.ACTIVE

    def test_create_root_with_description(self, wt):
        root_id = wt.create_root("Task", description="Detailed description")
        branch = wt.get_branch(root_id)
        assert branch.description == "Detailed description"


class TestBranch:
    def test_branch_from_root(self, wt):
        root_id = wt.create_root("Main task")
        branch_id = wt.branch(root_id, name="approach-a", description="Try timeout")
        assert branch_id
        branch = wt.get_branch(branch_id)
        assert branch.name == "approach-a"
        assert branch.parent_id == root_id
        assert branch.root_id == root_id

    def test_branch_from_branch(self, wt):
        root_id = wt.create_root("Main")
        b1 = wt.branch(root_id, name="approach-a")
        b2 = wt.branch(b1, name="sub-approach")
        branch = wt.get_branch(b2)
        assert branch.parent_id == b1
        assert branch.root_id == root_id

    def test_branch_invalid_parent(self, wt):
        with pytest.raises(ValueError, match="not found"):
            wt.branch("nonexistent", name="x")

    def test_branch_with_metadata(self, wt):
        root_id = wt.create_root("Main")
        b = wt.branch(root_id, name="meta-branch", metadata={"priority": "high"})
        branch = wt.get_branch(b)
        assert branch.metadata["priority"] == "high"


class TestUpdateResult:
    def test_update_result(self, wt):
        root_id = wt.create_root("Task")
        wt.update_result(root_id, result="Done successfully")
        branch = wt.get_branch(root_id)
        assert branch.result == "Done successfully"

    def test_update_metadata(self, wt):
        root_id = wt.create_root("Task")
        wt.update_result(root_id, metadata={"files_changed": 3})
        branch = wt.get_branch(root_id)
        assert branch.metadata["files_changed"] == 3

    def test_update_nonexistent(self, wt):
        assert wt.update_result("nonexistent", result="x") is False


class TestMerge:
    def test_merge_branch_to_root(self, wt):
        root_id = wt.create_root("Main task")
        b = wt.branch(root_id, name="approach-a")
        wt.update_result(b, result="Solution A works")
        result = wt.merge(b)
        assert result.success is True
        assert result.source_branch == b
        assert result.target_branch == root_id
        assert result.merged_result == "Solution A works"
        branch = wt.get_branch(b)
        assert branch.status == BranchStatus.MERGED

    def test_merge_with_conflict(self, wt):
        root_id = wt.create_root("Main")
        wt.update_result(root_id, result="Existing result")
        b = wt.branch(root_id, name="approach-b")
        wt.update_result(b, result="Different result")
        result = wt.merge(b, strategy=MergeStrategy.AUTO)
        assert result.success is True
        assert len(result.conflicts) > 0
        assert result.strategy == MergeStrategy.MANUAL

    def test_merge_nonexistent_source(self, wt):
        result = wt.merge("nonexistent")
        assert result.success is False

    def test_merge_to_specific_target(self, wt):
        root_id = wt.create_root("Main")
        b1 = wt.branch(root_id, name="a")
        b2 = wt.branch(root_id, name="b")
        wt.update_result(b1, result="Result A")
        wt.update_result(b2, result="Result B")
        result = wt.merge(b2, target_branch=b1)
        assert result.success is True
        assert result.target_branch == b1


class TestDiff:
    def test_diff_different_branches(self, wt):
        root_id = wt.create_root("Main")
        b1 = wt.branch(root_id, name="a")
        b2 = wt.branch(root_id, name="b")
        wt.update_result(b1, result="Result A")
        wt.update_result(b2, result="Result B")
        diff = wt.diff(b1, b2)
        assert not diff.identical
        result_diff = [d for d in diff.differences if d.field == "result"]
        assert len(result_diff) == 1

    def test_diff_identical_results(self, wt):
        root_id = wt.create_root("Main")
        b1 = wt.branch(root_id, name="a")
        b2 = wt.branch(root_id, name="b")
        wt.update_result(b1, result="Same")
        wt.update_result(b2, result="Same")
        diff = wt.diff(b1, b2)
        result_diffs = [d for d in diff.differences if d.field == "result"]
        assert len(result_diffs) == 0

    def test_diff_nonexistent(self, wt):
        diff = wt.diff("a", "b")
        assert diff.branch_a == "a"


class TestCherryPick:
    def test_cherry_pick_items(self, wt):
        root_id = wt.create_root("Main")
        b1 = wt.branch(root_id, name="source", metadata={"key1": "val1", "key2": "val2"})
        b2 = wt.branch(root_id, name="target", metadata={"key3": "val3"})
        wt.cherry_pick(b1, b2, ["key1"])
        branch = wt.get_branch(b2)
        assert branch.metadata["key1"] == "val1"
        assert branch.metadata["key3"] == "val3"

    def test_cherry_pick_nonexistent_source(self, wt):
        root_id = wt.create_root("Main")
        b = wt.branch(root_id, name="target")
        assert wt.cherry_pick("nonexistent", b, ["key"]) is False


class TestCheckpointRollback:
    def test_checkpoint_and_rollback(self, wt):
        root_id = wt.create_root("Task")
        wt.update_result(root_id, result="Initial result", metadata={"v": 1})
        cp_id = wt.checkpoint(root_id, label="v1")
        wt.update_result(root_id, result="Modified result", metadata={"v": 2})
        branch = wt.get_branch(root_id)
        assert branch.result == "Modified result"
        assert wt.rollback(root_id, cp_id) is True
        branch = wt.get_branch(root_id)
        assert branch.result == "Initial result"
        assert branch.metadata["v"] == 1

    def test_rollback_invalid_checkpoint(self, wt):
        root_id = wt.create_root("Task")
        assert wt.rollback(root_id, "nonexistent") is False

    def test_list_checkpoints(self, wt):
        root_id = wt.create_root("Task")
        wt.checkpoint(root_id, label="cp1")
        wt.checkpoint(root_id, label="cp2")
        cps = wt.list_checkpoints(root_id)
        assert len(cps) == 2


class TestAbandon:
    def test_abandon_branch(self, wt):
        root_id = wt.create_root("Main")
        b = wt.branch(root_id, name="bad-approach")
        wt.abandon(b)
        branch = wt.get_branch(b)
        assert branch.status == BranchStatus.ABANDONED


class TestListBranches:
    def test_list_all(self, wt):
        root_id = wt.create_root("Main")
        wt.branch(root_id, name="a")
        wt.branch(root_id, name="b")
        branches = wt.list_branches(root_id=root_id)
        assert len(branches) == 3

    def test_list_active_only(self, wt):
        root_id = wt.create_root("Main")
        b = wt.branch(root_id, name="to-abandon")
        wt.abandon(b)
        active = wt.list_branches(root_id=root_id, active_only=True)
        assert all(br.status == BranchStatus.ACTIVE for br in active)
