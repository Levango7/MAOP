"""Execution 域 Service 层 — DAG / Worktree / Hook / Hooks.

框架无关（不导入 FastAPI），函数接收显式参数，返回普通 dict/object，
不抛 HTTPException。可独立单元测试，无需 HTTP 上下文。

本模块从以下 4 个 router 提取业务逻辑：

- ``dag``       — LLM 智能任务拆分 + DAG 执行
- ``worktree``  — WorktreeManager 节点树 CRUD
- ``hook``      — HookManager 注册/触发（单数路径 ``/api/hook/*``）
- ``hooks``     — Hook 可视化配置 CRUD（复数路径 ``/api/hooks``，任务199）

设计要点：
  - ``TaskSplitter`` / ``Engine`` 在 ``auto_split`` / ``execute_dag`` 内部
    按需新建（无全局单例），与原 router 行为一致。
  - ``WorktreeManager`` / ``HookManager`` 是进程级单例，由本 service 持有
    全局变量 + 双重检查锁定；``_set_xxx`` 供测试注入。
  - 业务函数接收 mgr 参数（由 router 通过 ``_get_xxx_mgr()`` 获取并传入），
    便于测试通过 monkeypatch router 的 ``_get_xxx_mgr`` 注入 mock。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# §1  DAG — LLM 任务拆分 + DAG 执行
# ══════════════════════════════════════════════════════════════════


async def auto_split(
    *, description: str, context: str, max_subtasks: int,
) -> dict[str, Any]:
    """LLM 驱动的自然语言任务拆分，返回子任务 DAG.

    Parameters
    ----------
    description : str
        自然语言任务描述。
    context : str
        额外上下文信息。
    max_subtasks : int
        最大子任务数量。

    Returns
    -------
    dict
        ``TaskSplitter.split()`` 的原始返回值（可直接用于 MAOP DAG 调度器）。

    Raises
    ------
    TaskSplitError
        任务拆分失败（由 router 转为 HTTPException 400）。
    Exception
        其他内部错误（由 router 转为 HTTPException 500）。
    """
    from maop.core.scheduling.task_splitter import TaskSplitter

    splitter = TaskSplitter()
    result = await splitter.split(
        description=description,
        context=context,
        max_subtasks=max_subtasks,
    )
    return result


async def execute_dag(
    *, nodes: list[dict[str, Any]], edges: list[dict[str, Any]],
) -> dict[str, Any]:
    """执行前端工作流编辑器导出的 DAG，返回 trace_id + 步骤结果.

    将编辑器节点（agent/tool/condition/parallel）映射为 ``WorkflowStep``，
    按 edges 推导依赖关系后调用 ``Engine`` 执行。

    Parameters
    ----------
    nodes : list[dict]
        DAG 节点列表。
    edges : list[dict]
        DAG 边列表（source→target）。

    Returns
    -------
    dict
        ``{"run_id": str, "success": bool, "steps": list}``。

    Raises
    ------
    ValueError
        当 nodes 为空。
    Exception
        其他内部错误（由 router 转为 HTTPException 500）。
    """
    if not nodes:
        raise ValueError("DAG 无节点，无法执行")

    from maop.engine import Engine, StepType, WorkflowStep

    # edges source→target 推导 depends_on
    depends: dict[str, list[str]] = {}
    for edge in edges:
        src = edge.get("source", "")
        tgt = edge.get("target", "")
        if src and tgt:
            depends.setdefault(tgt, []).append(src)

    _type_map = {
        "agent": StepType.AGENT,
        "tool": StepType.AGENT,
        "condition": StepType.CONDITION,
        "parallel": StepType.DAG,
    }
    steps: list[WorkflowStep] = []
    for node in nodes:
        nid = node.get("id", "")
        cfg = node.get("config") or {}
        steps.append(
            WorkflowStep(
                id=nid,
                type=_type_map.get(node.get("type", "agent"), StepType.AGENT),
                agent=cfg.get("agent") or cfg.get("tool") or "",
                task=node.get("label") or cfg.get("task") or cfg.get("predicate") or "",
                depends_on=depends.get(nid, []),
            )
        )

    from types import SimpleNamespace

    async def _default_step_executor(step, context, workdir, trace_id):
        """Default step executor using Dispatcher to run agent/tool steps."""
        from maop.delegate.dispatch_core import Dispatcher
        dispatcher = Dispatcher()
        try:
            dr = await dispatcher.dispatch(
                agent=step.agent,
                task=step.task,
                workdir=str(workdir) if workdir else "",
                trace_id=trace_id or "",
            )
            return SimpleNamespace(
                output=dr.result.stdout,
                exit_code=dr.result.exit_code,
                error=dr.result.error or ("" if dr.result.ok else dr.result.stderr),
            )
        except Exception as exc:
            logger.warning("[dag/execute] step executor error: %s", exc)
            return SimpleNamespace(
                output="",
                exit_code=1,
                error="Step execution failed",
            )

    result = await Engine(step_executor=_default_step_executor).run(steps)
    return {
        "run_id": result.trace_id,
        "success": result.success,
        "steps": [s.model_dump() for s in result.steps],
    }


# ══════════════════════════════════════════════════════════════════
# §2  Worktree — 节点树 CRUD
# ══════════════════════════════════════════════════════════════════

_worktree_mgr: Any = None
_worktree_mgr_lock = threading.Lock()


def _get_worktree_mgr(maop_root: Any = None) -> Any:
    """惰性初始化全局 WorktreeManager 单例.

    Parameters
    ----------
    maop_root : Any
        MAOP 根路径（str 或 Path）。首次初始化时使用；后续调用忽略。
    """
    global _worktree_mgr
    if _worktree_mgr is None:
        with _worktree_mgr_lock:
            if _worktree_mgr is None:
                from maop.core.agent.memory_ctx.worktree import WorktreeManager
                _worktree_mgr = WorktreeManager(root_dir=str(maop_root))
    return _worktree_mgr


def _set_worktree_mgr(mgr: Any) -> None:
    """供测试注入自定义 WorktreeManager（隔离）。"""
    global _worktree_mgr
    with _worktree_mgr_lock:
        _worktree_mgr = mgr


def worktree_create_root(mgr: Any, *, task: str, description: str) -> str:
    """创建 worktree root，返回 node_id."""
    return mgr.create_root(task=task, description=description)


def worktree_branch(
    mgr: Any, *,
    parent_id: str, name: str, description: str, metadata: dict[str, Any] | None,
) -> str:
    """创建分支，返回 node_id.

    Raises
    ------
    ValueError
        分支创建失败（父节点不存在等）。由 router 转为 HTTPException 400。
    """
    return mgr.branch(parent_id=parent_id, name=name, description=description, metadata=metadata)


def worktree_abandon(mgr: Any, node_id: str) -> bool:
    """放弃节点，返回是否成功."""
    return bool(mgr.abandon(node_id))


def worktree_get(mgr: Any, node_id: str) -> Any:
    """获取分支信息，返回 BranchInfo 或 None."""
    return mgr.get_branch(node_id)


def worktree_list(mgr: Any, *, root_id: str, active_only: bool) -> list[Any]:
    """列出分支."""
    return mgr.list_branches(root_id=root_id, active_only=active_only)


def worktree_merge(mgr: Any, *, source_branch: str, target_branch: str) -> Any:
    """合并分支，返回 merge result."""
    return mgr.merge(source_branch=source_branch, target_branch=target_branch)


def worktree_checkpoint(mgr: Any, *, node_id: str, label: str) -> str:
    """创建检查点，返回 checkpoint_id.

    Raises
    ------
    ValueError
        检查点创建失败。由 router 转为 HTTPException 400。
    """
    return mgr.checkpoint(node_id, label=label)


def worktree_rollback(mgr: Any, *, node_id: str, checkpoint_id: str) -> bool:
    """回滚到检查点，返回是否成功."""
    return bool(mgr.rollback(node_id, to_checkpoint=checkpoint_id))


# ══════════════════════════════════════════════════════════════════
# §3  Hook (单数 /api/hook/*) — 注册/触发
# ══════════════════════════════════════════════════════════════════

_hook_mgr: Any = None
_hook_mgr_lock = threading.Lock()


def _get_hook_mgr(maop_root: Any = None) -> Any:
    """惰性初始化全局 HookManager 单例.

    Parameters
    ----------
    maop_root : Any
        MAOP 根路径（str 或 Path）。首次初始化时使用；后续调用忽略。
    """
    global _hook_mgr
    if _hook_mgr is None:
        with _hook_mgr_lock:
            if _hook_mgr is None:
                from maop.core.agent.plugins_hooks.hook_manager import HookManager
                _hook_mgr = HookManager(root_dir=str(maop_root))
    return _hook_mgr


def _set_hook_mgr(mgr: Any) -> None:
    """供测试注入自定义 HookManager（隔离）。"""
    global _hook_mgr
    with _hook_mgr_lock:
        _hook_mgr = mgr


def hook_register(
    mgr: Any, *,
    event: str, url: str, callback: str, priority: int, description: str,
) -> Any:
    """注册 Hook.

    当 ``url`` 非空时注册 webhook；当 ``callback`` 非空时注册日志回调
    （确保 hook 触发时有可观测的副作用）。

    Returns
    -------
    HookDef
        注册后的 hook 定义。

    Raises
    ------
    ValueError
        当 url 和 callback 都为空。
    """
    if url:
        return mgr.register(event=event, url=url, priority=priority, description=description)
    if callback:
        def _log_callback(evt: str, data: dict[str, Any]) -> None:
            logger.info("[hook] callback triggered for event=%s", evt)

        return mgr.register(event=event, callback=_log_callback, priority=priority, description=description)
    raise ValueError("must provide url or callback")


def hook_unregister(mgr: Any, hook_id: str) -> bool:
    """注销 Hook，返回是否移除成功."""
    return bool(mgr.unregister(hook_id))


def hook_enable(mgr: Any, hook_id: str) -> bool:
    """启用 Hook，返回是否成功."""
    return bool(mgr.enable(hook_id))


def hook_disable(mgr: Any, hook_id: str) -> bool:
    """禁用 Hook，返回是否成功."""
    return bool(mgr.disable(hook_id))


def hook_list(mgr: Any, event: str) -> list[Any]:
    """列出 Hook（可选按事件过滤）."""
    return mgr.list_hooks(event=event or "")


def hook_get(mgr: Any, hook_id: str) -> Any:
    """获取单个 Hook，返回 HookDef 或 None."""
    return mgr.get_hook(hook_id)


async def hook_trigger(mgr: Any, event: str, data: dict[str, Any]) -> list[Any]:
    """触发 Hook 事件，返回触发结果列表."""
    return await mgr.trigger(event, data)


def hook_logs(mgr: Any, event: str, limit: int) -> list[Any]:
    """获取 Hook 日志."""
    return mgr.get_logs(event=event or "", limit=limit)


def hook_events() -> list[dict[str, str]]:
    """列出所有可用的 lifecycle 事件类型.

    Returns
    -------
    list[dict]
        每项为 ``{"name": str, "phase": str, "domain": str}``。
    """
    from maop.core.agent.plugins_hooks.hook_manager import LifecycleEvent
    return [
        {"name": e.value, "phase": e.value.split(".")[-1], "domain": e.value.split(".")[0]}
        for e in LifecycleEvent
    ]


# ══════════════════════════════════════════════════════════════════
# §4  Hooks (复数 /api/hooks) — 可视化配置 CRUD（任务199）
# ══════════════════════════════════════════════════════════════════
# 复用 §3 的 HookManager 单例 (_get_hook_mgr / _set_hook_mgr)。


def _is_valid_event(event: str) -> bool:
    """判断事件是否合法：精确匹配或 ``<domain>.*`` 通配."""
    from maop.core.agent.plugins_hooks.hook_manager import LifecycleEvent
    valid_events: set[str] = {e.value for e in LifecycleEvent}
    if event in valid_events:
        return True
    # 允许通配符，如 "agent.*"
    if event.endswith(".*"):
        prefix = event[:-2]
        return any(e.startswith(prefix + ".") for e in valid_events)
    return False


def hooks_list(mgr: Any, event: str) -> list[Any]:
    """列出全部 hook（可选按事件过滤）— 复数路径 /api/hooks."""
    return mgr.list_hooks(event=event or "")


def hooks_create(
    mgr: Any, *,
    name: str, event: str, url: str,
) -> Any:
    """创建新 hook（webhook 类型）— 复数路径 /api/hooks.

    Parameters
    ----------
    name : str
        Hook 名称。
    event : str
        事件类型（需为 LifecycleEvent 之一或通配）。
    url : str
        Webhook 接收 URL（已通过 SSRF 校验）。

    Returns
    -------
    HookDef
        注册后的 hook 定义。

    Raises
    ------
    ValueError
        当 event 无效。
    """
    if not _is_valid_event(event):
        raise ValueError(f"Invalid event type: {event}")
    hdef = mgr.register(
        event=event,
        url=url,
        priority=0,
        description=name,
        source="api",
    )
    logger.info("[hooks] Created hook '%s' for event '%s'", hdef.id, event)
    return hdef


def hooks_get(mgr: Any, hook_id: str) -> Any:
    """获取单个 hook — 复数路径 /api/hooks/{hook_id}."""
    return mgr.get_hook(hook_id)


def hooks_update(
    mgr: Any, *,
    hook_id: str,
    new_event: str, new_url: str, new_name: str, new_enabled: bool,
) -> Any:
    """更新 hook 配置（先删除旧 hook 再注册新 hook，保留原 id）.

    Parameters
    ----------
    hook_id : str
        原 hook ID。
    new_event : str
        新事件类型（已校验合法）。
    new_url : str
        新 URL（已通过 SSRF 校验）。
    new_name : str
        新名称。
    new_enabled : bool
        是否启用。

    Returns
    -------
    HookDef
        更新后的 hook 定义。

    Raises
    ------
    ValueError
        当 event 无效或 url 为空。
    """
    if not _is_valid_event(new_event):
        raise ValueError(f"Invalid event type: {new_event}")
    if not new_url:
        raise ValueError("url must not be empty")

    existing = mgr.get_hook(hook_id)
    if existing is None:
        raise KeyError(f"Hook {hook_id} not found")

    # 删除旧 hook 并注册新 hook（保留原 id）
    mgr.unregister(hook_id)
    hdef = mgr.register(
        event=new_event,
        url=new_url,
        priority=existing.priority,
        description=new_name,
        source="api",
        hook_id=hook_id,
    )
    if not new_enabled:
        mgr.disable(hook_id)
    logger.info("[hooks] Updated hook '%s'", hook_id)
    return hdef


def hooks_delete(mgr: Any, hook_id: str) -> bool:
    """删除 hook，返回是否移除成功.

    Raises
    ------
    KeyError
        当 hook 不存在。
    """
    existing = mgr.get_hook(hook_id)
    if existing is None:
        raise KeyError(f"Hook {hook_id} not found")
    return bool(mgr.unregister(hook_id))


async def hooks_test(mgr: Any, hook_id: str) -> dict[str, Any]:
    """触发一次测试事件，向 hook URL 发送 POST 请求.

    临时启用 hook 以便触发（不修改持久化状态）。

    Returns
    -------
    dict
        ``{"success": bool, "response": str, "error": str, "duration_ms": int}``
        或 ``{"success": True, "response": "no listeners"}``。

    Raises
    ------
    KeyError
        当 hook 不存在。
    """
    hdef = mgr.get_hook(hook_id)
    if hdef is None:
        raise KeyError(f"Hook {hook_id} not found")

    # 临时启用 hook 以便触发（不修改持久化状态）
    was_enabled = hdef.enabled
    if not was_enabled:
        mgr.enable(hook_id)
    try:
        results = await mgr.trigger(hdef.event, {"test": True, "hook_id": hook_id})
    finally:
        if not was_enabled:
            mgr.disable(hook_id)

    if not results:
        return {"success": True, "response": "no listeners", "error": "", "duration_ms": 0}
    r = results[0]
    return {
        "success": r.success,
        "response": r.response,
        "error": r.error,
        "duration_ms": r.duration_ms,
    }


def hooks_enable(mgr: Any, hook_id: str) -> bool:
    """启用 hook，返回是否成功.

    Raises
    ------
    KeyError
        当 hook 不存在。
    """
    if mgr.get_hook(hook_id) is None:
        raise KeyError(f"Hook {hook_id} not found")
    return bool(mgr.enable(hook_id))


def hooks_disable(mgr: Any, hook_id: str) -> bool:
    """禁用 hook，返回是否成功.

    Raises
    ------
    KeyError
        当 hook 不存在。
    """
    if mgr.get_hook(hook_id) is None:
        raise KeyError(f"Hook {hook_id} not found")
    return bool(mgr.disable(hook_id))


def hooks_events() -> list[dict[str, str]]:
    """列出所有可用的 lifecycle 事件类型 — 复数路径 /api/hooks/events.

    与 ``hook_events()`` 返回相同结构。
    """
    from maop.core.agent.plugins_hooks.hook_manager import LifecycleEvent
    return [
        {"name": e.value, "phase": e.value.split(".")[-1], "domain": e.value.split(".")[0]}
        for e in LifecycleEvent
    ]