"""Response models for ``/api/worktree/*`` — the 8 endpoints the dashboard calls.

The producers are ``WorktreeManager`` (``maop/core/agent/memory_ctx/worktree.py``)
reached through ``execution_service``: ``BranchInfo`` for a branch,
``MergeResult`` for a merge. Those domain models are reused verbatim below so
the declared shape cannot drift from what the manager actually builds — the
manager is the only thing that knows how many keys ``BranchInfo`` has.

Note on the ``status`` defaults: every success path writes ``"status": "ok"``,
while the ``@handle_api_errors(error_value=...)`` fallbacks omit ``status``
entirely on some routes (``/api/worktree/list`` returns
``{"branches": [], "count": 0, "error": "..."}``). Those fallback dicts are
returned as plain dicts, so they ARE validated by ``response_model`` — an
``"ok"`` default would label an error response as a success. Defaulting to
``"error"`` keeps the wire truthful on both paths.
"""

from __future__ import annotations

from pydantic import BaseModel

from maop.core.agent.memory_ctx.worktree import BranchInfo, MergeResult


class CreateRootEnvelope(BaseModel):
    """``worktree_create_root()`` returns the new node id."""

    status: str = "error"
    node_id: str = ""
    error: str = ""


class BranchEnvelope(BaseModel):
    """``worktree_branch()`` returns the new node id."""

    status: str = "error"
    node_id: str = ""
    error: str = ""


class AbandonEnvelope(BaseModel):
    """``worktree_abandon()`` echoes the abandoned node id."""

    status: str = "error"
    id: str = ""
    error: str = ""


class GetEnvelope(BaseModel):
    status: str = "error"
    branch: BranchInfo | None = None
    error: str = ""


class ListEnvelope(BaseModel):
    status: str = "error"
    branches: list[BranchInfo] = []
    count: int = 0
    error: str = ""


class MergeEnvelope(BaseModel):
    """``merge`` carries ``MergeResult`` — ``success`` is the field to read."""

    status: str = "error"
    merge: MergeResult | None = None
    error: str = ""


class CheckpointEnvelope(BaseModel):
    status: str = "error"
    checkpoint_id: str = ""
    error: str = ""


class RollbackEnvelope(BaseModel):
    """Rollback reports no payload of its own — only the outcome."""

    status: str = "error"
    error: str = ""
