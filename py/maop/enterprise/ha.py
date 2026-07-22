"""MAOP Enterprise High Availability.

Provides HA configuration for production deployments:
  - Leader election (via Redis or DB lease)
  - Failover coordination
  - Health monitoring and auto-recovery
  - Cluster state management
"""

from __future__ import annotations

import logging
import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from maop.config.edition import require_feature, FeatureFlag

logger = logging.getLogger(__name__)


class NodeRole(str, Enum):
    LEADER = "leader"
    FOLLOWER = "follower"
    CANDIDATE = "candidate"


class NodeStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNREACHABLE = "unreachable"


class ClusterNode(BaseModel):
    node_id: str
    address: str
    role: NodeRole = NodeRole.FOLLOWER
    status: NodeStatus = NodeStatus.HEALTHY
    last_heartbeat: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class HAConfig(BaseModel):
    lease_ttl_s: float = 15.0
    heartbeat_interval_s: float = 5.0
    failover_timeout_s: float = 30.0
    min_healthy_nodes: int = 1
    auto_failover: bool = True


class HAManager:
    """Enterprise high availability coordinator."""

    def __init__(self, config: HAConfig | None = None) -> None:
        require_feature(FeatureFlag.MULTI_USER)
        self._config = config or HAConfig()
        self._nodes: dict[str, ClusterNode] = {}
        self._leader_id: str = ""

    @property
    def config(self) -> HAConfig:
        return self._config

    @property
    def leader_id(self) -> str:
        return self._leader_id

    def register_node(self, node_id: str, address: str) -> ClusterNode:
        node = ClusterNode(node_id=node_id, address=address, last_heartbeat=time.time())
        self._nodes[node_id] = node
        logger.info("[ha] Registered node=%s address=%s", node_id, address)
        return node

    def deregister_node(self, node_id: str) -> bool:
        if node_id not in self._nodes:
            return False
        del self._nodes[node_id]
        if self._leader_id == node_id:
            self._leader_id = ""
            logger.warning("[ha] Leader node=%s deregistered — election needed", node_id)
        return True

    def heartbeat(self, node_id: str) -> bool:
        node = self._nodes.get(node_id)
        if not node:
            return False
        node.last_heartbeat = time.time()
        node.status = NodeStatus.HEALTHY
        return True

    def elect_leader(self) -> str | None:
        healthy = [
            n for n in self._nodes.values()
            if n.status == NodeStatus.HEALTHY and (time.time() - n.last_heartbeat) < self._config.failover_timeout_s
        ]
        if not healthy:
            logger.error("[ha] No healthy nodes available for leader election")
            return None
        leader = min(healthy, key=lambda n: n.node_id)
        for n in self._nodes.values():
            n.role = NodeRole.FOLLOWER
        leader.role = NodeRole.LEADER
        self._leader_id = leader.node_id
        logger.info("[ha] Elected leader=%s", leader.node_id)
        return leader.node_id

    def check_health(self) -> dict[str, Any]:
        now = time.time()
        healthy = 0
        degraded = 0
        unreachable = 0
        for node in self._nodes.values():
            elapsed = now - node.last_heartbeat
            if elapsed > self._config.failover_timeout_s:
                node.status = NodeStatus.UNREACHABLE
                unreachable += 1
            elif elapsed > self._config.heartbeat_interval_s * 2:
                node.status = NodeStatus.DEGRADED
                degraded += 1
            else:
                node.status = NodeStatus.HEALTHY
                healthy += 1
        needs_failover = (
            self._config.auto_failover
            and self._leader_id
            and self._nodes.get(self._leader_id, ClusterNode(node_id="", address="")).status != NodeStatus.HEALTHY
        )
        return {
            "total_nodes": len(self._nodes),
            "healthy": healthy,
            "degraded": degraded,
            "unreachable": unreachable,
            "leader_id": self._leader_id,
            "needs_failover": needs_failover,
        }

    def list_nodes(self) -> list[ClusterNode]:
        return list(self._nodes.values())