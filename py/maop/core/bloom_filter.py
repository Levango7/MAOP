"""MAOP Bloom Filter — probabilistic set membership for deduplication.

Provides a space-efficient, probabilistic data structure for checking whether
an item *might* have been seen before.  False positives are possible but
false negatives are not.

Use cases in MAOP:
  - MemoryStore: skip wiki.json / memory.json full-scan on store() when
    the entry ID is definitely new (bloom miss = certain new).
  - EventBus: deduplicate event IDs in dead-letter tracking.
  - MessageQueue: fast "already enqueued?" check before dequeue.

Implementation uses MurmurHash3 (mmh3) when available, falls back to
a pure-Python hash chain otherwise.

Usage::

    bf = BloomFilter(expected_items=100_000, fp_rate=0.01)
    bf.add("entry-20260713-abc123")
    if "entry-20260713-abc123" in bf:
        print("might exist")   # small FP chance
    if "new-id" not in bf:
        print("definitely new")  # guaranteed
"""

from __future__ import annotations

import logging
import math
import struct
from collections.abc import Iterator

logger = logging.getLogger(__name__)

try:
    import mmh3 as _mmh3
    _HAS_MMH3 = True
except Exception:
    _mmh3 = None  # type: ignore[assignment]
    _HAS_MMH3 = False

# ── Hash functions ──────────────────────────────────────────────

def _mmh3_hash32(key: bytes, seed: int = 0) -> int:
    """MurmurHash3 x86_32 — fast, good distribution."""
    if _HAS_MMH3 and _mmh3 is not None:
        try:
            return _mmh3.hash(key, seed, signed=False)
        except Exception:
            pass
    # Pure-Python fallback (FNV-1a variant with seed mixing)
    h = 2166136261 ^ seed
    for b in key:
        h ^= b
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def _hash_i(key: bytes, i: int, m: int) -> int:
    """Double-hashing scheme: h(i) = (h1 + i * h2) % m."""
    h1 = _mmh3_hash32(key, seed=0)
    h2 = _mmh3_hash32(key, seed=1)
    return (h1 + i * h2) % m


# ── Bit array ───────────────────────────────────────────────────

class _BitArray:
    """Compact bit array backed by bytearray."""

    __slots__ = ("_data", "_size")

    def __init__(self, size: int) -> None:
        self._size = size
        self._data = bytearray((size + 7) // 8)

    def set(self, index: int) -> None:
        """Set bit at index."""
        self._data[index >> 3] |= 1 << (index & 7)

    def test(self, index: int) -> bool:
        """Test bit at index."""
        return bool(self._data[index >> 3] & (1 << (index & 7)))

    def __len__(self) -> int:
        return self._size

    def count_set(self) -> int:
        """Count number of set bits (popcount)."""
        n = 0
        for byte in self._data:
            n += (byte).bit_count()
        return n

    def to_bytes(self) -> bytes:
        """Serialize to bytes."""
        return struct.pack(">I", self._size) + bytes(self._data)

    @classmethod
    def from_bytes(cls, data: bytes) -> _BitArray:
        """Deserialize from bytes."""
        size = struct.unpack(">I", data[:4])[0]
        ba = cls.__new__(cls)
        ba._size = size
        ba._data = bytearray(data[4:])
        return ba


# ── BloomFilter ────────────────────────────────────────────────

class BloomFilter:
    """Space-efficient probabilistic set membership test.

    Parameters
    ----------
    expected_items : int
        Expected number of items to be added.
    fp_rate : float
        Desired false-positive rate (0 < fp_rate < 1).
    """

    __slots__ = ("_bits", "_count", "_fp_rate", "_k", "_m")

    def __init__(
        self,
        expected_items: int = 100_000,
        fp_rate: float = 0.01,
    ) -> None:
        expected_items = max(expected_items, 1)
        if not 0 < fp_rate < 1:
            fp_rate = 0.01

        self._fp_rate = fp_rate
        # Optimal bit array size: m = -n * ln(p) / (ln2)^2
        self._m = max(64, int(
            -expected_items * math.log(fp_rate) / (math.log(2) ** 2)
        ))
        # Optimal number of hash functions: k = (m/n) * ln2
        self._k = max(1, int((self._m / expected_items) * math.log(2)))
        self._bits = _BitArray(self._m)
        self._count = 0

    # ── Core operations ───────────────────────────────────────

    def add(self, item: str) -> None:
        """Add an item to the filter."""
        key = item.encode("utf-8")
        for i in range(self._k):
            self._bits.set(_hash_i(key, i, self._m))
        self._count += 1

    def __contains__(self, item: str) -> bool:
        """Test if item *might* be in the set (may false-positive)."""
        key = item.encode("utf-8")
        return all(self._bits.test(_hash_i(key, i, self._m)) for i in range(self._k))

    def __len__(self) -> int:
        """Number of items added (approximate — no removal)."""
        return self._count

    # ── Bulk operations ───────────────────────────────────────

    def update(self, items: Iterator[str] | list[str]) -> None:
        """Add multiple items."""
        for item in items:
            self.add(item)

    def might_contain_any(self, items: Iterator[str] | list[str]) -> list[str]:
        """Return items that *might* be in the filter (possible FP)."""
        return [item for item in items if item in self]

    # ── Serialization ─────────────────────────────────────────

    def to_bytes(self) -> bytes:
        """Serialize the filter state to bytes for persistence."""
        import struct
        # Header: m(I4) + k(I4) + fp_rate(f4) + count(I4) = 16 bytes
        header = struct.pack(">IIfI", self._m, self._k, self._fp_rate, self._count)
        return header + self._bits.to_bytes()

    @classmethod
    def from_bytes(cls, data: bytes) -> BloomFilter:
        """Deserialize a filter from bytes."""
        import struct
        HEADER_SIZE = 16  # 4+4+4+4
        m, k, fp_rate, count = struct.unpack(">IIfI", data[:HEADER_SIZE])
        bf = cls.__new__(cls)
        bf._m = m
        bf._k = k
        bf._fp_rate = fp_rate
        bf._count = count
        bf._bits = _BitArray.from_bytes(data[HEADER_SIZE:])
        return bf

    # ── Stats ─────────────────────────────────────────────────

    @property
    def false_positive_rate(self) -> float:
        """Current estimated false-positive rate based on fill ratio."""
        set_bits = self._bits.count_set()
        return (set_bits / self._m) ** self._k if self._m > 0 else 0.0

    @property
    def fill_ratio(self) -> float:
        """Fraction of bits set."""
        return self._bits.count_set() / self._m if self._m > 0 else 0.0

    def stats(self) -> dict:
        """Return filter statistics."""
        return {
            "items_added": self._count,
            "bit_array_size": self._m,
            "hash_functions": self._k,
            "fill_ratio": round(self.fill_ratio, 4),
            "current_fp_rate": round(self.false_positive_rate, 6),
            "target_fp_rate": self._fp_rate,
            "memory_bytes": len(self._bits._data) + 16,
        }

    def __repr__(self) -> str:
        return (
            f"BloomFilter(items={self._count}, m={self._m}, "
            f"k={self._k}, fp={self.false_positive_rate:.4f})"
        )
