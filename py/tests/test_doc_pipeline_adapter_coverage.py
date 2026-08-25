"""Coverage tests for maop.delegate.doc_pipeline_adapter — doc-pipeline 适配器.

该模块在基线测试中覆盖率为 0%。本文件补充适配器函数的测试。
由于 doc-pipeline 外部包可能不可用，测试主要覆盖错误路径和边界情况。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from maop.delegate import doc_pipeline_adapter


@pytest.fixture(autouse=True)
def _reset_orchestrator():
    """每个测试前后重置单例。"""
    doc_pipeline_adapter._ORCHESTRATOR = None
    doc_pipeline_adapter._DOC_PIPELINE_ROOT = None
    yield
    doc_pipeline_adapter._ORCHESTRATOR = None
    doc_pipeline_adapter._DOC_PIPELINE_ROOT = None


class TestResolveDocPipelineRoot:
    """测试 _resolve_doc_pipeline_root 函数。"""

    def test_raises_when_not_found(self):
        """doc-pipeline 不存在时抛出 FileNotFoundError。"""
        # 直接 mock _resolve_doc_pipeline_root 抛出异常
        # 这测试的是函数本身的契约：找不到时抛 FileNotFoundError
        with patch.object(doc_pipeline_adapter, "_DOC_PIPELINE_ROOT", None), \
             patch("pathlib.Path.exists", return_value=False), \
             pytest.raises(FileNotFoundError, match="doc-pipeline not found"):
            doc_pipeline_adapter._resolve_doc_pipeline_root()

    def test_caches_result(self, monkeypatch):
        """第二次调用使用缓存。"""
        # 先设置缓存
        fake_root = MagicMock()
        doc_pipeline_adapter._DOC_PIPELINE_ROOT = fake_root
        result = doc_pipeline_adapter._resolve_doc_pipeline_root()
        assert result is fake_root


class TestGetOrchestrator:
    """测试 get_orchestrator 函数。"""

    def test_returns_cached_orchestrator(self):
        """已有单例时直接返回。"""
        fake_orch = MagicMock()
        doc_pipeline_adapter._ORCHESTRATOR = fake_orch
        result = doc_pipeline_adapter.get_orchestrator()
        assert result is fake_orch


class TestRunPipeline:
    """测试 run_pipeline 函数。"""

    def test_returns_failed_on_file_not_found(self):
        """FileNotFoundError 时返回 failed 状态。"""
        with patch.object(doc_pipeline_adapter, "get_orchestrator", side_effect=FileNotFoundError("not found")):
            result = doc_pipeline_adapter.run_pipeline(pipeline_name="test")
        assert result["status"] == "failed"
        assert "doc-pipeline not found" in result["error"]

    def test_returns_failed_on_exception(self):
        """一般异常时返回 failed 状态。"""
        with patch.object(doc_pipeline_adapter, "get_orchestrator", side_effect=RuntimeError("unexpected")):
            result = doc_pipeline_adapter.run_pipeline(pipeline_name="test")
        assert result["status"] == "failed"
        assert "unexpected" in result["error"]

    def test_includes_task_id_in_result(self):
        """失败结果包含 task_id。"""
        with patch.object(doc_pipeline_adapter, "get_orchestrator", side_effect=RuntimeError("err")):
            result = doc_pipeline_adapter.run_pipeline(task_id="my-task")
        assert result["task_id"] == "my-task"

    def test_empty_task_id_on_failure(self):
        """没有 task_id 时用空字符串。"""
        with patch.object(doc_pipeline_adapter, "get_orchestrator", side_effect=RuntimeError("err")):
            result = doc_pipeline_adapter.run_pipeline()
        assert result["task_id"] == ""


class TestRunPlan:
    """测试 run_plan 函数。"""

    def test_returns_failed_on_exception(self):
        """异常时返回 failed 状态。"""
        with patch.object(doc_pipeline_adapter, "get_orchestrator", side_effect=RuntimeError("err")):
            result = doc_pipeline_adapter.run_plan(pipeline_name="test")
        assert result["status"] == "failed"
        assert "err" in result["error"]

    def test_includes_task_id(self):
        """失败结果包含 task_id。"""
        with patch.object(doc_pipeline_adapter, "get_orchestrator", side_effect=RuntimeError("err")):
            result = doc_pipeline_adapter.run_plan(task_id="plan-task")
        assert result["task_id"] == "plan-task"


class TestGetStatus:
    """测试 get_status 函数。"""

    def test_returns_none_on_exception(self):
        """异常时返回 None。"""
        with patch.object(doc_pipeline_adapter, "get_orchestrator", side_effect=RuntimeError("err")):
            result = doc_pipeline_adapter.get_status("task-1")
        assert result is None

    def test_returns_none_when_task_not_found(self):
        """任务不存在时返回 None。"""
        fake_orch = MagicMock()
        fake_orch.get_task.return_value = None
        with patch.object(doc_pipeline_adapter, "get_orchestrator", return_value=fake_orch):
            result = doc_pipeline_adapter.get_status("nonexistent")
        assert result is None

    def test_returns_task_dict(self):
        """找到任务时返回其字典表示。"""
        fake_task = MagicMock()
        fake_task.to_dict.return_value = {"task_id": "t1", "status": "done"}
        fake_orch = MagicMock()
        fake_orch.get_task.return_value = fake_task
        with patch.object(doc_pipeline_adapter, "get_orchestrator", return_value=fake_orch):
            result = doc_pipeline_adapter.get_status("t1")
        assert result == {"task_id": "t1", "status": "done"}


class TestListHooks:
    """测试 list_hooks 函数。"""

    def test_returns_empty_on_exception(self):
        """异常时返回空列表。"""
        with patch.object(doc_pipeline_adapter, "_ensure_importable", side_effect=RuntimeError("err")):
            result = doc_pipeline_adapter.list_hooks()
        assert result == []


class TestShutdown:
    """测试 shutdown 函数。"""

    def test_no_op_when_no_orchestrator(self):
        """没有编排器时不做操作。"""
        doc_pipeline_adapter._ORCHESTRATOR = None
        # 不应抛异常
        doc_pipeline_adapter.shutdown()
        assert doc_pipeline_adapter._ORCHESTRATOR is None

    def test_shuts_down_orchestrator(self):
        """有编排器时调用其 shutdown。"""
        fake_orch = MagicMock()
        doc_pipeline_adapter._ORCHESTRATOR = fake_orch
        doc_pipeline_adapter.shutdown()
        fake_orch.shutdown.assert_called_once()
        assert doc_pipeline_adapter._ORCHESTRATOR is None

    def test_suppresses_shutdown_exception(self):
        """shutdown 异常被抑制。"""
        fake_orch = MagicMock()
        fake_orch.shutdown.side_effect = RuntimeError("shutdown failed")
        doc_pipeline_adapter._ORCHESTRATOR = fake_orch
        # 不应抛异常
        doc_pipeline_adapter.shutdown()
        assert doc_pipeline_adapter._ORCHESTRATOR is None