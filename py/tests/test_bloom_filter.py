"""Tests for MAOP.core.bloom_filter — Bloom filter for deduplication."""


from maop.core.bloom_filter import BloomFilter, _BitArray, _hash_i, _mmh3_hash32

# ── BitArray tests ─────────────────────────────────────────────

class TestBitArray:
    def test_set_and_test(self):
        ba = _BitArray(128)
        assert not ba.test(0)
        ba.set(0)
        assert ba.test(0)
        assert not ba.test(1)

    def test_multiple_bits(self):
        ba = _BitArray(256)
        for i in [0, 7, 8, 15, 16, 127, 255]:
            ba.set(i)
        for i in [0, 7, 8, 15, 16, 127, 255]:
            assert ba.test(i)
        for i in [1, 2, 3, 126, 128, 254]:
            assert not ba.test(i)

    def test_count_set(self):
        ba = _BitArray(64)
        ba.set(0)
        ba.set(7)
        ba.set(8)
        assert ba.count_set() == 3

    def test_serialization_roundtrip(self):
        ba = _BitArray(128)
        for i in [0, 5, 10, 63, 127]:
            ba.set(i)
        data = ba.to_bytes()
        restored = _BitArray.from_bytes(data)
        assert len(restored) == 128
        for i in [0, 5, 10, 63, 127]:
            assert restored.test(i)
        for i in [1, 2, 3, 64, 126]:
            assert not restored.test(i)


# ── Hash function tests ────────────────────────────────────────

class TestHashFunctions:
    def test_hash_deterministic(self):
        key = b"test-key"
        h1 = _mmh3_hash32(key, seed=0)
        h2 = _mmh3_hash32(key, seed=0)
        assert h1 == h2

    def test_hash_different_seeds(self):
        key = b"test-key"
        h0 = _mmh3_hash32(key, seed=0)
        h1 = _mmh3_hash32(key, seed=1)
        assert h0 != h1

    def test_hash_i_in_range(self):
        key = b"test-key"
        m = 1000
        for i in range(10):
            h = _hash_i(key, i, m)
            assert 0 <= h < m


# ── BloomFilter core tests ─────────────────────────────────────

class TestBloomFilterCore:
    def test_add_and_contains(self):
        bf = BloomFilter(expected_items=100, fp_rate=0.01)
        bf.add("item-1")
        assert "item-1" in bf

    def test_not_contains_guaranteed(self):
        bf = BloomFilter(expected_items=100, fp_rate=0.01)
        bf.add("item-1")
        assert "item-2" not in bf  # guaranteed no false negative

    def test_multiple_items(self):
        bf = BloomFilter(expected_items=1000, fp_rate=0.01)
        items = [f"id-{i}" for i in range(100)]
        for item in items:
            bf.add(item)
        for item in items:
            assert item in bf

    def test_len(self):
        bf = BloomFilter(expected_items=100, fp_rate=0.01)
        assert len(bf) == 0
        bf.add("a")
        bf.add("b")
        assert len(bf) == 2

    def test_empty_filter(self):
        bf = BloomFilter(expected_items=100, fp_rate=0.01)
        assert "anything" not in bf
        assert len(bf) == 0


# ── Bulk operations ────────────────────────────────────────────

class TestBloomFilterBulk:
    def test_update(self):
        bf = BloomFilter(expected_items=100, fp_rate=0.01)
        items = [f"item-{i}" for i in range(50)]
        bf.update(items)
        assert len(bf) == 50
        for item in items:
            assert item in bf

    def test_might_contain_any(self):
        bf = BloomFilter(expected_items=100, fp_rate=0.01)
        bf.add("exists-1")
        bf.add("exists-2")
        results = bf.might_contain_any(["exists-1", "not-exists", "exists-2"])
        assert "exists-1" in results
        assert "exists-2" in results
        # "not-exists" might appear as FP but very unlikely at 1% rate


# ── Serialization ──────────────────────────────────────────────

class TestBloomFilterSerialization:
    def test_roundtrip(self):
        bf = BloomFilter(expected_items=100, fp_rate=0.01)
        for i in range(50):
            bf.add(f"item-{i}")

        data = bf.to_bytes()
        restored = BloomFilter.from_bytes(data)

        assert len(restored) == 50
        for i in range(50):
            assert f"item-{i}" in restored
        assert "item-999" not in restored

    def test_empty_roundtrip(self):
        bf = BloomFilter(expected_items=100, fp_rate=0.01)
        data = bf.to_bytes()
        restored = BloomFilter.from_bytes(data)
        assert len(restored) == 0
        assert "anything" not in restored


# ── Statistics ─────────────────────────────────────────────────

class TestBloomFilterStats:
    def test_stats_structure(self):
        bf = BloomFilter(expected_items=1000, fp_rate=0.01)
        stats = bf.stats()
        assert "items_added" in stats
        assert "bit_array_size" in stats
        assert "hash_functions" in stats
        assert "fill_ratio" in stats
        assert "current_fp_rate" in stats
        assert "target_fp_rate" in stats
        assert "memory_bytes" in stats

    def test_fill_ratio_increases(self):
        bf = BloomFilter(expected_items=100, fp_rate=0.01)
        assert bf.fill_ratio == 0.0
        for i in range(50):
            bf.add(f"item-{i}")
        assert bf.fill_ratio > 0.0

    def test_repr(self):
        bf = BloomFilter(expected_items=100, fp_rate=0.01)
        r = repr(bf)
        assert "BloomFilter" in r
        assert "items=0" in r


# ── False positive rate validation ─────────────────────────────

class TestBloomFilterFP:
    def test_fp_rate_within_bound(self):
        """Empirical FP rate should be close to target for expected load."""
        n = 10_000
        fp_target = 0.01
        bf = BloomFilter(expected_items=n, fp_rate=fp_target)

        # Add n items
        for i in range(n):
            bf.add(f"item-{i}")

        # Test n items that were NOT added
        fp_count = 0
        test_count = 10_000
        for i in range(n, n + test_count):
            if f"item-{i}" in bf:
                fp_count += 1

        empirical_fp = fp_count / test_count
        # Allow generous margin (3x target) since variance exists
        assert empirical_fp < fp_target * 3, (
            f"FP rate {empirical_fp:.4f} exceeds 3x target {fp_target * 3}"
        )

    def test_no_false_negatives(self):
        """Bloom filter must never produce false negatives."""
        bf = BloomFilter(expected_items=1000, fp_rate=0.01)
        items = [f"id-{i}" for i in range(500)]
        for item in items:
            bf.add(item)
        # Every added item MUST be found
        for item in items:
            assert item in bf, f"False negative for {item}!"
