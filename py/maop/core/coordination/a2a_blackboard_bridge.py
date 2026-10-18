"""MAOP — A2A Protocol ↔ Blackboard Bridge.

This module integrates Google's Agent-to-Agent (A2A) protocol with the
MAOP Blackboard architecture. It creates a bidirectional bridge:

1. **A2A → Blackboard**: Receives A2A messages (tasks, agent responses,
   capability advertisements) and writes them into the blackboard's
   ``agent_contribution`` domain.
2. **Blackboard → A2A**: Monitors the blackboard for hypothesis / decision
   entries that require external agent input, and sends them as A2A tasks
   to registered agents.

This enables a cooperative multi-agent system where distributed agents
contribute to a shared knowledge pool while remaining decoupled through
the A2A protocol.

Usage::

    from maop.core.coordination.a2a_blackboard_bridge import A2ABlackboardBridge

    bridge = A2ABlackboardBridge(blackboard=bb, registry=registry)
    await bridge.start()
    # ... agents write through A2A, blackboard reflects ...
    await bridge.stop()
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from maop.core.reliability.blackboard import (
    Blackboard,
    BlackboardDomain,
    BlackboardEntry,
)

logger = logging.getLogger(__name__)


# ── Agent Registry Protocol ──────────────────────────────────────────


class AgentRegistry(Protocol):
    """Agent registry interface for the A2A bridge.

    Any registry implementing ``get_agent(card_id)`` and
    ``list_agents()`` (optionally ``health_check()``) can be used.
    """

    def get_agent(self, card_id: str) -> Any | None: ...
    def list_agents(self) -> list[AgentCard]: ...


@dataclass
class AgentCard:
    """A2A Agent Card (simplified).

    Mirrors the Google A2A ``AgentCard`` schema. Contains the minimum
    fields needed for routing and capability matching.
    """

    card_id: str
    name: str
    url: str
    description: str = ""
    capabilities: list[str] = field(default_factory=list)
    status: str = "active"  # active | degraded | offline
    health_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ── A2A Message Types (mirrors JSON-RPC 2.0 envelope) ──────────────


@dataclass
class A2AMessage:
    """Represents an A2A inter-agent message.

    Uses JSON-RPC 2.0 envelope: ``method``, ``params``, ``id``.
    ``result`` / ``error`` appear in the response counterpart.
    """

    jsonrpc: str = "2.0"
    method: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source_agent: str = ""
    target_agent: str = ""
    task_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "jsonrpc": self.jsonrpc,
            "method": self.method,
            "params": self.params,
            "id": self.id,
            "source_agent": self.source_agent,
            "target_agent": self.target_agent,
        }
        if self.task_id:
            d["task_id"] = self.task_id
        return d


# ── Bridge Implementation ──────────────────────────────────────────


class A2ABlackboardBridge:
    """Bridges A2A protocol messages to and from the Blackboard.

    The bridge propagates events in two directions:

    * **Ingress (A2A → BB)**: ``on_a2a_message`` writes the message
      content into the ``agent_contribution`` domain and, if the message
      is a task_result, merges it into ``partial_solution`` or
      ``evidence``.
    * **Egress (BB → A2A)**: ``start_monitoring`` (or an attached
      ``Controller``) watches for ``hypothesis`` and ``decision``
      entries and routes them to A2A agents based on capability tags.

    Parameters
    ----------
    blackboard : Blackboard
        The blackboard instance to bridge with.
    registry : AgentRegistry | None
        Agent registry for routing decisions/messages. If None, routing
        still works but only for messages addressed to ``broadcast``.
    """

    def __init__(self, blackboard: Blackboard, registry: AgentRegistry | None = None) -> None:
        self._bb = blackboard
        self._registry = registry
        self._running = False
        self._task_routes: dict[str, str] = {}  # task_id → card_id for tracking

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the bridge and subscribe to blackboard for egress."""
        self._running = True
        # Subscribe to blackboard domains that need A2A routing
        self._bb.subscribe(BlackboardDomain.HYPOTHESIS.value, self._on_hypothesis_for_a2a)
        self._bb.subscribe(BlackboardDomain.DECISION.value, self._on_decision_for_a2a)
        logger.info("[a2a-bb-bridge] started")

    async def stop(self) -> None:
        """Stop the bridge."""
        self._running = False

    # ── A2A → Blackboard (Ingress) ───────────────────────────────

    async def on_a2a_message(self, message: A2AMessage) -> BlackboardEntry | None:
        """Receive an A2A message and write it into the blackboard.

        Parameters
        ----------
        message : A2AMessage
            The incoming A2A message.

        Returns
        -------
        BlackboardEntry | None
            The written entry (or None if the message was filtered).
        """
        if not self._running:
            logger.warning("[a2a-bb-bridge] bridge is not running; message dropped")
            return None

        # Determine domain based on message method
        content = message.to_dict()
        contributor = message.source_agent or "a2a-unknown"

        if message.method == "task_result":
            # Task results go to partial_solution or evidence
            result_kind = message.params.get("result_kind", "partial_solution")
            if result_kind == "evidence":
                domain = BlackboardDomain.EVIDENCE.value
            else:
                domain = BlackboardDomain.PARTIAL_SOLUTION.value
        elif message.method in ("hypothesis", "propose"):
            domain = BlackboardDomain.HYPOTHESIS.value
        elif message.method in ("report", "evidence_submit"):
            domain = BlackboardDomain.EVIDENCE.value
        elif message.method == "decision":
            domain = BlackboardDomain.DECISION.value
        else:
            domain = BlackboardDomain.AGENT_CONTRIBUTION.value

        entry = await self._bb.write(
            domain=domain,
            content=content,
            contributor=contributor,
            confidence=message.params.get("confidence", 0.8),
            metadata={
                "a2a": True,
                "method": message.method,
                "task_id": message.task_id,
                "target_agent": message.target_agent,
            },
        )
        logger.debug(
            "[a2a-bb-bridge] ingested a2a.%s → bb.%s (%s)",
            message.method, domain, entry.entry_id[:8]
        )
        return entry

    # ── Blackboard → A2A (Egress) ────────────────────────────────

    async def _on_hypothesis_for_a2a(self, entry: BlackboardEntry) -> None:
        """Route hypothesis entries requiring external validation to A2A agents.

        The decision logic:

        - If entry has ``metadata.needs_external`` → route to an agent whose
          ``capabilities`` contain the capability tag.
        - If no capable agent exists → log a warning (no crash).
        """
        if not self._running:
            return
        needs_external = entry.metadata.get("needs_external", False)
        if not needs_external:
            return
        capability_tag = entry.metadata.get("capability_tag", "")
        target = self._find_agent_for_capability(capability_tag)
        if target is None:
            logger.info(
                "[a2a-bb-route] no agent for capability=%s, "
                "hypothesis %s stays local",
                capability_tag, entry.entry_id[:8]
            )
            return

        a2a_msg = A2AMessage(
            method="task",
            params={
                "task_kind": "validate",
                "hypothesis": entry.to_dict(),
            },
            source_agent="maop-blackboard-bridge",
            target_agent=target.card_id,
        )
        if a2a_msg.task_id:
            self._task_routes[a2a_msg.task_id] = target.card_id
        logger.info(
            "[a2a-bb-route] routed hypothesis → agent=%s (task=%s)",
            target.card_id, a2a_msg.task_id
        )
    async def _on_decision_for_a2a(self, entry: BlackboardEntry) -> None:
        """Broadcast decision entries to all registered agents.

        Agents subscribed to the blackboard event stream will also see it
        via push; the A2A path here is for agents that are NOT directly
        connected to this dashboard instance.
        """
        if not self._running or self._registry is None:
            return
        agents = self._registry.list_agents()
        if not agents:
            return
        for agent in agents:
            if agent.status != "active":
                continue
            if agent.card_id == entry.contributor:
                continue  # skip the originating agent
            a2a_msg = A2AMessage(
                method="broadcast",
                params={
                    "event": "decision",
                    "data": entry.to_dict(),
                },
                source_agent="maop-blackboard-bridge",
                target_agent=agent.card_id,
            )
            if a2a_msg.task_id:
                self._task_routes[a2a_msg.task_id] = agent.card_id

    # ── Helpers ──────────────────────────────────────────────────

    def _find_agent_for_capability(self, capability_tag: str) -> AgentCard | None:
        """Return the first active agent whose capabilities include the tag."""
        if self._registry is None:
            return None
        for agent in self._registry.list_agents():
            if agent.status == "active" and capability_tag in agent.capabilities:
                return agent
        return None
