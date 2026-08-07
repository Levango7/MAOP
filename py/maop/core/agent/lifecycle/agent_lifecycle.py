"""MAOP Agent Lifecycle — State machine for agent runtime management.

Provides a formal state machine for tracking agent lifecycle transitions:
    init -> ready -> running -> paused -> stopped
                  |-> error

Each transition triggers optional hooks, enabling monitoring, logging,
and automated responses (e.g., auto-restart on error).

Usage::

    from maop.core.agent.lifecycle.agent_lifecycle import AgentLifecycle, AgentState

    lc = AgentLifecycle(agent_name="claude")

    lc.transition(AgentState.READY)     # init -> ready
    lc.transition(AgentState.RUNNING)   # ready -> running
    lc.transition(AgentState.PAUSED)    # running -> paused
    lc.transition(AgentState.RUNNING)   # paused -> running (resume)
    lc.transition(AgentState.STOPPED)   # running -> stopped

    lc.state                            # AgentState.STOPPED
    lc.history                          # list of transitions
"""

from __future__ import annotations

import logging
import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class AgentState(str, Enum):
    """Agent lifecycle states."""
    INIT = "init"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


class StateTransition(BaseModel):
    """Record of a single state transition."""
    from_state: str
    to_state: str
    agent_name: str
    timestamp: float = Field(default_factory=time.time)
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


_VALID_TRANSITIONS: dict[AgentState, set[AgentState]] = {
    AgentState.INIT: {AgentState.READY, AgentState.ERROR},
    AgentState.READY: {AgentState.RUNNING, AgentState.STOPPED, AgentState.ERROR},
    AgentState.RUNNING: {AgentState.PAUSED, AgentState.STOPPED, AgentState.ERROR},
    AgentState.PAUSED: {AgentState.RUNNING, AgentState.STOPPED, AgentState.ERROR},
    AgentState.ERROR: {AgentState.READY, AgentState.STOPPED},
    AgentState.STOPPED: set(),
}


class AgentLifecycle:
    """State machine for a single agent's lifecycle.

    Parameters
    ----------
    agent_name : str
        Name of the agent this lifecycle tracks.
    """

    def __init__(self, agent_name: str) -> None:
        self.agent_name = agent_name
        self._state = AgentState.INIT
        self._history: list[StateTransition] = []
        self._entered_at: float = time.time()
        self._hooks: list[Any] = []

    @property
    def state(self) -> AgentState:
        """Current agent state."""
        return self._state

    @property
    def history(self) -> list[StateTransition]:
        """Full transition history."""
        return list(self._history)

    @property
    def state_duration_s(self) -> float:
        """Seconds spent in current state."""
        return time.time() - self._entered_at

    def can_transition(self, to_state: AgentState) -> bool:
        """Check if a transition to the given state is valid."""
        return to_state in _VALID_TRANSITIONS.get(self._state, set())

    def transition(
        self,
        to_state: AgentState,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> StateTransition:
        """Execute a state transition.

        Raises ValueError if the transition is invalid.
        """
        if not self.can_transition(to_state):
            raise ValueError(
                f"Invalid transition for '{self.agent_name}': "
                f"{self._state.value} -> {to_state.value}"
            )

        record = StateTransition(
            from_state=self._state.value,
            to_state=to_state.value,
            agent_name=self.agent_name,
            reason=reason,
            metadata=metadata or {},
        )
        self._history.append(record)

        old_state = self._state
        self._state = to_state
        self._entered_at = time.time()

        logger.info(
            "[lifecycle] %s: %s -> %s%s",
            self.agent_name, old_state.value, to_state.value,
            f" ({reason})" if reason else "",
        )

        self._fire_hooks(old_state, to_state, record)

        return record

    def on_transition(self, hook: Any) -> None:
        """Register a hook called on every state transition.

        Hook signature: hook(old_state, new_state, transition_record)
        """
        self._hooks.append(hook)

    def _fire_hooks(
        self, old_state: AgentState, new_state: AgentState, record: StateTransition
    ) -> None:
        for hook in self._hooks:
            try:
                hook(old_state, new_state, record)
            except Exception as exc:
                logger.warning("[lifecycle] Hook error for %s: %s", self.agent_name, exc)

    def force_state(self, state: AgentState, reason: str = "forced") -> None:
        """Force-set state without transition validation (admin/recovery only)."""
        record = StateTransition(
            from_state=self._state.value,
            to_state=state.value,
            agent_name=self.agent_name,
            reason=reason,
            metadata={"forced": True},
        )
        self._history.append(record)
        self._state = state
        self._entered_at = time.time()
        logger.warning("[lifecycle] %s: FORCED %s -> %s", self.agent_name, record.from_state, state.value)

    def reset(self) -> None:
        """Reset to INIT state (for re-initialization)."""
        self._state = AgentState.INIT
        self._entered_at = time.time()

    def summary(self) -> dict[str, Any]:
        """Get lifecycle summary."""
        state_counts: dict[str, int] = {}
        for t in self._history:
            state_counts[t.to_state] = state_counts.get(t.to_state, 0) + 1
        return {
            "agent_name": self.agent_name,
            "current_state": self._state.value,
            "state_duration_s": round(self.state_duration_s, 1),
            "total_transitions": len(self._history),
            "state_counts": state_counts,
        }


class AgentLifecycleManager:
    """Manages lifecycles for all agents.

    Parameters
    ----------
    root_dir : str | Path
        MAOP project root directory.
    """

    def __init__(self, root_dir: str | Any = ".") -> None:
        self._lifecycles: dict[str, AgentLifecycle] = {}

    def get(self, agent_name: str) -> AgentLifecycle:
        """Get or create a lifecycle for an agent."""
        if agent_name not in self._lifecycles:
            self._lifecycles[agent_name] = AgentLifecycle(agent_name)
        return self._lifecycles[agent_name]

    def list_agents(self) -> list[str]:
        """List all tracked agent names."""
        return list(self._lifecycles.keys())

    def agents_by_state(self, state: AgentState) -> list[str]:
        """Get agent names in a specific state."""
        return [name for name, lc in self._lifecycles.items() if lc.state == state]

    def running_agents(self) -> list[str]:
        """Get names of currently running agents."""
        return self.agents_by_state(AgentState.RUNNING)

    def transition_all(self, to_state: AgentState, reason: str = "") -> dict[str, bool]:
        """Attempt to transition all agents to a given state."""
        results: dict[str, bool] = {}
        for name, lc in self._lifecycles.items():
            try:
                lc.transition(to_state, reason=reason)
                results[name] = True
            except ValueError:
                results[name] = False
        return results

    def summary(self) -> dict[str, Any]:
        """Get summary of all agent lifecycles."""
        return {
            "total_agents": len(self._lifecycles),
            "by_state": {
                s.value: len(self.agents_by_state(s)) for s in AgentState
            },
            "agents": {name: lc.summary() for name, lc in self._lifecycles.items()},
        }
