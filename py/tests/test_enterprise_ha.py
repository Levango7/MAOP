"""Tests for maop.enterprise.ha.HAManager — node registration, leader election, and health."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

# H4 修复：将 importorskip 改为显式 pytest.skip，让测试报告显式统计跳过数。
pytest.skip(reason="maop.enterprise 未发布", allow_module_level=True)

from maop.enterprise.ha import (
    HAConfig,
    HAManager,
    NodeRole,
    NodeStatus,
)


@pytest.fixture(autouse=True)
def enterprise_mode():
    """Enable enterprise edition so require_feature(FeatureFlag.MULTI_USER) passes."""
    from maop.config.edition import Edition, reset_edition, set_edition
    set_edition(Edition.ENTERPRISE)
    yield
    reset_edition()


def test_register_node():
    """register_node() returns a ClusterNode with the given id and address."""
    mgr = HAManager()
    node = mgr.register_node("n1", "10.0.0.1:8080")
    assert node.node_id == "n1"
    assert node.address == "10.0.0.1:8080"


def test_register_multiple():
    """Multiple nodes can be registered."""
    mgr = HAManager()
    mgr.register_node("n1", "10.0.0.1:8080")
    mgr.register_node("n2", "10.0.0.2:8080")
    mgr.register_node("n3", "10.0.0.3:8080")
    assert len(mgr.list_nodes()) == 3


def test_deregister_node():
    """deregister_node() returns True for existing, False for missing nodes."""
    mgr = HAManager()
    mgr.register_node("n1", "10.0.0.1:8080")
    assert mgr.deregister_node("n1") is True
    assert mgr.deregister_node("n1") is False
    assert mgr.deregister_node("nope") is False


def test_deregister_leader():
    """Deregistering the leader clears leader_id."""
    mgr = HAManager()
    mgr.register_node("n1", "10.0.0.1:8080")
    mgr.register_node("n2", "10.0.0.2:8080")
    leader = mgr.elect_leader()
    assert leader == mgr.leader_id
    mgr.deregister_node(leader)
    assert mgr.leader_id == ""


def test_heartbeat():
    """heartbeat() returns True for registered nodes, False for unregistered."""
    mgr = HAManager()
    mgr.register_node("n1", "10.0.0.1:8080")
    assert mgr.heartbeat("n1") is True
    assert mgr.heartbeat("nope") is False


def test_heartbeat_updates_status():
    """heartbeat() sets the node status to HEALTHY."""
    mgr = HAManager()
    node = mgr.register_node("n1", "10.0.0.1:8080")
    node.status = NodeStatus.DEGRADED
    mgr.heartbeat("n1")
    assert node.status == NodeStatus.HEALTHY


def test_elect_leader():
    """elect_leader() returns a node_id when healthy nodes exist, None otherwise."""
    mgr = HAManager()
    mgr.register_node("n1", "10.0.0.1:8080")
    assert mgr.elect_leader() == "n1"

    # no healthy nodes available
    mgr2 = HAManager()
    node = mgr2.register_node("n1", "10.0.0.1:8080")
    node.status = NodeStatus.UNREACHABLE
    node.last_heartbeat = time.time() - 1000
    assert mgr2.elect_leader() is None


def test_elect_leader_sets_role():
    """elect_leader() sets the leader's role to LEADER and others to FOLLOWER."""
    mgr = HAManager()
    n1 = mgr.register_node("n1", "10.0.0.1:8080")
    n2 = mgr.register_node("n2", "10.0.0.2:8080")
    n3 = mgr.register_node("n3", "10.0.0.3:8080")
    leader_id = mgr.elect_leader()
    nodes = [n1, n2, n3]
    leader = next(n for n in nodes if n.node_id == leader_id)
    followers = [n for n in nodes if n.node_id != leader_id]
    assert leader.role == NodeRole.LEADER
    assert all(f.role == NodeRole.FOLLOWER for f in followers)


def test_check_health_all_healthy():
    """Freshly registered nodes are all HEALTHY."""
    mgr = HAManager()
    mgr.register_node("n1", "10.0.0.1:8080")
    mgr.register_node("n2", "10.0.0.2:8080")
    health = mgr.check_health()
    assert health["healthy"] == 2
    assert health["degraded"] == 0
    assert health["unreachable"] == 0


def test_check_health_degraded():
    """A node with a stale heartbeat becomes DEGRADED."""
    mgr = HAManager()
    node = mgr.register_node("n1", "10.0.0.1:8080")
    # heartbeat_interval_s=5 -> degraded when elapsed > 10s
    node.last_heartbeat = time.time() - 11
    health = mgr.check_health()
    assert health["degraded"] == 1
    assert node.status == NodeStatus.DEGRADED


def test_check_health_unreachable():
    """A node with a very stale heartbeat becomes UNREACHABLE."""
    mgr = HAManager()
    node = mgr.register_node("n1", "10.0.0.1:8080")
    # failover_timeout_s=30 -> unreachable when elapsed > 30s
    node.last_heartbeat = time.time() - 31
    health = mgr.check_health()
    assert health["unreachable"] == 1
    assert node.status == NodeStatus.UNREACHABLE


def test_check_health_needs_failover():
    """needs_failover is True when the leader is unhealthy."""
    mgr = HAManager()
    leader = mgr.register_node("n1", "10.0.0.1:8080")
    mgr.register_node("n2", "10.0.0.2:8080")
    mgr.elect_leader()
    assert mgr.leader_id == "n1"
    # make the leader unreachable
    leader.last_heartbeat = time.time() - 31
    health = mgr.check_health()
    assert health["needs_failover"] is True


def test_list_nodes():
    """list_nodes() returns all registered nodes."""
    mgr = HAManager()
    mgr.register_node("n1", "10.0.0.1:8080")
    mgr.register_node("n2", "10.0.0.2:8080")
    nodes = mgr.list_nodes()
    assert len(nodes) == 2
    assert {n.node_id for n in nodes} == {"n1", "n2"}


def test_config_defaults():
    """HAConfig has the expected default values."""
    cfg = HAConfig()
    assert cfg.lease_ttl_s == 15.0
    assert cfg.heartbeat_interval_s == 5.0
    assert cfg.failover_timeout_s == 30.0
    assert cfg.min_healthy_nodes == 1
    assert cfg.auto_failover is True


# ═══════════════════════════════════════════════════════════════════════
# Phase 3.4: Redis distributed mode tests (mock RedisDistributedLock)
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def redis_mode(monkeypatch):
    """Enable Redis HA backend via MAOP_HA_BACKEND=redis."""
    monkeypatch.setenv("MAOP_HA_BACKEND", "redis")
    yield
    monkeypatch.delenv("MAOP_HA_BACKEND", raising=False)


@pytest.fixture
def mock_lock(monkeypatch):
    """Mock RedisDistributedLock to avoid needing a real Redis server."""
    from maop.enterprise import ha
    lock = MagicMock()
    lock.acquire.return_value = True
    lock.fencing_token = 42
    lock.refresh.return_value = True
    lock.release.return_value = True
    lock_class = MagicMock(return_value=lock)
    monkeypatch.setattr(ha, "RedisDistributedLock", lock_class)
    return lock


def test_ha_redis_mode_init(redis_mode, enterprise_mode):
    """HAManager initializes in redis mode when MAOP_HA_BACKEND=redis."""
    mgr = HAManager()
    # Use public interface (check_health) instead of private _redis_mode
    assert mgr.check_health()["redis_mode"] is True


def test_ha_redis_elect_leader_acquires_lock(redis_mode, enterprise_mode, mock_lock):
    """elect_leader() acquires the distributed lock and becomes leader."""
    mgr = HAManager(node_id="node-1")
    leader = mgr.elect_leader()
    assert leader == "node-1"
    assert mgr.fencing_token == 42
    mock_lock.acquire.assert_called_once_with(blocking=False)


def test_ha_redis_elect_leader_fails_if_locked(redis_mode, enterprise_mode, mock_lock):
    """elect_leader() returns None when the lock is held by another node."""
    mock_lock.acquire.return_value = False
    mgr = HAManager(node_id="node-2")
    leader = mgr.elect_leader()
    assert leader is None
    assert mgr.leader_id == ""


def test_ha_redis_renew_leadership(redis_mode, enterprise_mode, mock_lock):
    """renew_leadership() refreshes the lock TTL when we are leader."""
    mgr = HAManager(node_id="node-3")
    mgr.elect_leader()
    assert mgr.renew_leadership() is True
    mock_lock.refresh.assert_called_once()


def test_ha_redis_release_leadership(redis_mode, enterprise_mode, mock_lock):
    """release_leadership() releases the lock and clears leader state."""
    mgr = HAManager(node_id="node-4")
    mgr.elect_leader()
    assert mgr.release_leadership() is True
    assert mgr.leader_id == ""
    assert mgr.fencing_token == 0
    mock_lock.release.assert_called_once()


def test_ha_redis_fencing_token(redis_mode, enterprise_mode, mock_lock):
    """fencing_token is set from the lock after election."""
    mock_lock.fencing_token = 99
    mgr = HAManager(node_id="node-5")
    mgr.elect_leader()
    assert mgr.fencing_token == 99


def test_ha_redis_fallback_to_memory(redis_mode, enterprise_mode, monkeypatch):
    """When Redis is unavailable (ImportError), HAManager falls back to memory mode."""
    from maop.enterprise import ha

    def raising_init(*args, **kwargs):
        raise ImportError("redis not installed")

    monkeypatch.setattr(ha, "RedisDistributedLock", raising_init)
    mgr = HAManager()
    # Configured mode is redis, but lock creation will fail on first elect
    mgr.register_node("n1", "10.0.0.1:8080")
    mgr.register_node("n2", "10.0.0.2:8080")
    leader = mgr.elect_leader()  # falls back to memory
    assert leader == "n1"  # min(node_id)
    # Use public interface: degraded to memory mode
    assert mgr.check_health()["redis_mode"] is False


def test_ha_redis_health_monitor_start_stop(redis_mode, enterprise_mode, mock_lock):
    """start/stop_health_monitor controls the background thread."""
    mgr = HAManager(config=HAConfig(heartbeat_interval_s=0.1), node_id="mon-node")
    mgr.start_health_monitor()
    assert mgr._monitor_thread is not None
    assert mgr._monitor_thread.is_alive()
    mgr.stop_health_monitor()
    assert mgr._monitor_thread is None


def test_ha_redis_automatic_failover(redis_mode, enterprise_mode, mock_lock):
    """When leader lease expires, another node can take over."""
    mgr = HAManager(node_id="node-A")
    # First election: lock is held by someone else
    mock_lock.acquire.return_value = False
    leader = mgr.elect_leader()
    assert leader is None
    # Lease expires: now we can acquire
    mock_lock.acquire.return_value = True
    leader = mgr.elect_leader()
    assert leader == "node-A"
