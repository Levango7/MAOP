# ADR-014: HA Module Current Status — Single-Instance In-Memory

**Date**: 2026-07-25  
**Status**: Superseded by [ADR-015](015-distributed-ha-redis-lease.md) (2026-07-25) — single-instance baseline retained as fallback when MAOP_HA_BACKEND=memory  

> **Note**: This ADR documents the Phase 3.2 single-instance HA baseline.
> Distributed HA with Redis lease + fencing tokens has been implemented in
> [ADR-015](015-distributed-ha-redis-lease.md). The memory-mode fallback
> described here remains as the degradation path when Redis is unavailable.

**Phase**: 3.2 (Documentation Alignment)

## Context

During the Phase 3 re-evaluation of the MAOP enterprise architecture, a thorough code review of `maop/enterprise/ha.py` revealed a significant gap between the module's documentation and its actual implementation:

- **Documentation claimed**: "Leader election (via Redis or DB lease)", "Failover coordination", "auto-recovery"
- **Actual implementation**: Pure in-memory `dict` for node tracking, deterministic `min(node_id)` ordering for leader selection, no distributed coordination, no automatic failover

This ADR documents the current state to prevent misunderstanding and establishes the baseline for the planned Phase 3.4 distributed HA work.

## Current Implementation (as of 2026-07-25)

### What Works
- **Node registration**: `register_node()` / `deregister_node()` manage an in-memory `dict[str, ClusterNode]`
- **Health tracking**: `heartbeat()` updates `last_heartbeat`; `check_health()` classifies nodes as HEALTHY/DEGRADED/UNREACHABLE based on time elapsed
- **Leader election**: `elect_leader()` selects the healthy node with the smallest `node_id` (deterministic, local-only)
- **Failover detection**: `check_health()` returns `needs_failover: bool` when the current leader is unhealthy

### What Does NOT Work
- **No distributed coordination**: All state lives in process memory; multiple `HAManager` instances are unaware of each other
- **No lease mechanism**: `HAConfig.lease_ttl_s` field exists but is never used for lease acquisition
- **No automatic failover**: `needs_failover=True` is a flag only; the caller must manually invoke `elect_leader()`
- **No split-brain protection**: No fencing tokens, no quorum, no distributed lock
- **No cross-process communication**: No Redis, etcd, PG advisory lock, or RPC

### Test Coverage
The test suite (`tests/test_enterprise_ha.py`, 14 tests) correctly validates the single-instance behavior. There are no multi-instance coordination tests because the feature does not exist.

## Decision

We accept the current single-instance implementation as the Phase 3.2 baseline and explicitly document it as such. The `ha.py` module docstring has been updated to accurately describe the current behavior and reference Phase 3.4 for the distributed implementation.

## Consequences

### Positive
- **No false confidence**: Users and developers will not mistakenly deploy `HAManager` expecting distributed HA
- **Clear baseline**: Phase 3.4 work has a documented starting point
- **Test accuracy**: Tests reflect actual behavior, not aspirational behavior

### Negative
- **Feature gap**: Enterprise users requiring true HA must wait for Phase 3.4
- **Deployment constraint**: Current architecture assumes single-writer (single MAOP instance or master-worker with shared SQLite)

## Phase 3.4 Plan (Future)

The distributed HA implementation will require:
1. **Coordination storage**: Redis lease (`SET key value NX EX ttl`) or PG advisory lock
2. **Fencing tokens**: Monotonically increasing tokens to prevent split-brain
3. **Automatic failover**: Background task that triggers re-election when `needs_failover=True`
4. **Cluster state broadcast**: Pub/sub mechanism for leader change notifications
5. **Health-based routing**: Load balancer integration to skip unhealthy nodes

Dependencies:
- Phase 3.1 (PostgreSQL production-grade) must be complete if PG advisory locks are used
- `backends_redis.py` must be implemented if Redis leases are used

## References

- `maop/enterprise/ha.py` — current implementation (docstring updated 2026-07-25)
- `tests/test_enterprise_ha.py` — 14 tests validating single-instance behavior
- `maop/enterprise/__init__.py` — backend module status documentation (updated 2026-07-25)
- ADR-013 — Agent/LLM direct CLI fallback (unrelated, but same Phase 3 evaluation cycle)
