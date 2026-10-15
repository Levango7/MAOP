"""`KVStore.add()` — atomic insert-if-absent, the missing step of any claim protocol.

Why this file exists: `cas()` only ever compares against a row that **already
exists** (missing key → `success=False`, pinned by
`test_cas_cannot_create_a_missing_key`), and `set()` is an unconditional upsert.
So before `add()` there was no way to express "the first writer wins the empty
slot" — two agents reading "unclaimed" could both `set()` and each believe it
held the claim. These tests pin the two properties that make `add()` usable for
coordination rather than just another write path:

1. **exactly one winner**, verified with real threads instead of a sequence;
2. **an expired claim is contestable even while its row still physically exists**
   (the 30s prune throttle must not be able to wedge a lock past its TTL), and
   replacing it **bumps** `version` rather than resetting it, so `version` is
   usable as a fencing token.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from maop.core.backends.kv_store import KVStore


@pytest.fixture
def kv(tmp_path: Path) -> KVStore:
    return KVStore(db_path=tmp_path / "kv.db")


class TestAddBasics:
    def test_creates_when_absent(self, kv: KVStore) -> None:
        res = kv.add("task:42", {"owner": "a"})
        assert res.success is True
        assert res.current_version == 1
        assert kv.get("task:42") == {"owner": "a"}

    def test_live_key_is_not_overwritten_and_reports_current(self, kv: KVStore) -> None:
        """The whole point: losing a race must leave the winner's value intact."""
        kv.set("slot", "winner-value")
        res = kv.add("slot", "loser-value")

        assert res.success is False
        assert res.current_value == "winner-value"
        assert kv.get("slot") == "winner-value", "losing add must not write"

    def test_returns_current_version_so_caller_can_cas(self, kv: KVStore) -> None:
        kv.set("slot", "v1")
        first = kv.add("slot", "x")
        assert first.success is False and first.current_version == 1
        # …and that version is immediately usable for a real update.
        second = kv.cas("slot", first.current_version, "v2")
        assert second.success is True and second.current_version == 2

    def test_namespace_isolation(self, kv: KVStore) -> None:
        a = kv.add("k", "in-default")
        b = kv.add("k", "in-other", namespace="other")
        assert a.success is True and b.success is True

        blocked = kv.add("k", "second-default")
        assert blocked.success is False


class TestAddIsAtomic:
    def test_exactly_one_winner_under_real_threads(self, kv: KVStore) -> None:
        """Contending writers, not a scripted sequence — this is the claim step.

        Asserted on the three things that would break if `add()` were a
        read-then-write: one winner, every loser told the truth about the
        current state, and exactly one row in the table.
        """
        n = 8
        start = threading.Barrier(n)
        results: dict[str, tuple[bool, int]] = {}
        lock = threading.Lock()

        def worker(agent: str) -> None:
            start.wait()
            res = kv.add("claim:job-1", {"owner": agent}, ttl=60)
            with lock:
                results[agent] = (res.success, res.current_version)

        threads = [threading.Thread(target=worker, args=(f"a{i}",)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        winners = [a for a, (ok, _) in results.items() if ok]
        assert len(winners) == 1, f"expected one winner, got {sorted(winners)}"
        assert len(results) == n, "a worker died — the claim attempt is unmeasured"

        losers = [(ok, ver) for a, (ok, ver) in results.items() if a not in winners]
        assert all(ok is False for ok, _ in losers)
        assert all(ver == 1 for _, ver in losers), "losers must see the winner's version"

        stored = kv.get("claim:job-1")
        assert stored == {"owner": winners[0]}, "winner's payload must be what landed"

    def test_no_extra_rows_from_losers(self, kv: KVStore) -> None:
        for i in range(5):
            kv.add("dedupe-me", i)
        assert kv.list_keys() == ["dedupe-me"]


class TestAddVsExpiry:
    def test_expired_claim_is_contestable_immediately(self, kv: KVStore) -> None:
        """ttl=0.0 is already expired at insert time (get() uses `> now`, strict).

        This is the case the expiry guard exists for: the row is still physically
        present, so a plain `ON CONFLICT DO NOTHING` would refuse the new claim
        and the dead holder would keep the lock far past its TTL.

        The row must be shown to still be in the table **before** the second
        attempt — asserting it via `get()` would be self-defeating, because
        `get()` calls `_maybe_prune()` and the first read of a fresh store prunes
        immediately, deleting the very row the guard is supposed to cope with.
        """
        first = kv.add("lock:res", {"holder": "dead"}, ttl=0.0)
        assert first.success is True

        probe = sqlite3.connect(str(kv.db_path))
        try:
            still_there = probe.execute(
                "SELECT COUNT(*) FROM kv_store WHERE key = 'lock:res'"
            ).fetchone()[0]
        finally:
            probe.close()
        assert still_there == 1, "precondition failed: expired row must not be pruned yet"

        second = kv.add("lock:res", {"holder": "alive"}, ttl=60)
        assert second.success is True, "expired row must not block a new claim"
        assert kv.get("lock:res") == {"holder": "alive"}

    def test_replacing_expired_bumps_version_never_resets(self, kv: KVStore) -> None:
        """Fencing: a claimant must be able to tell it is *not* the old holder."""
        versions = [kv.add("lock:cycle", {"round": i}, ttl=0.0).current_version for i in range(3)]
        assert versions == [1, 2, 3], f"version went backwards or reset: {versions}"

    def test_live_claim_still_blocks_after_another_live_claim(self, kv: KVStore) -> None:
        assert kv.add("lock:x", "holder-a", ttl=300).success is True
        blocked = kv.add("lock:x", "holder-b", ttl=300)
        assert blocked.success is False and blocked.current_value == "holder-a"


class TestAddGapIsReal:
    def test_cas_cannot_create_a_missing_key(self, kv: KVStore) -> None:
        """Pins the substrate gap `add()` exists to close.

        If someone later "helpfully" makes `cas()` create on a missing key,
        `SQLiteKVBackend.cas` breaks: it probes with `expected_version=0`
        precisely because versions start at 1 and 0 must never write.
        """
        res = kv.cas("ghost", 0, "value")
        assert res.success is False
        assert res.current_version == 0
        assert kv.get("ghost") is None, "the version-0 probe must not create anything"

    def test_set_is_unconditional_and_therefore_not_a_claim(self, kv: KVStore) -> None:
        """Documents why callers must not reach for `set()` to claim a slot."""
        kv.set("slot", "first")
        kv.set("slot", "second")
        assert kv.get("slot") == "second", "set() is last-writer-wins by design"
