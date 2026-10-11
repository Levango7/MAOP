"""F2-02 B1 — Reciprocal Rank Fusion pure function."""

from __future__ import annotations

import pytest

from maop.core.memory.fusion import reciprocal_rank_fusion


def test_fusion_promotes_cross_list_agreement():
    """A doc ranked well in *both* legs outranks a doc dominating one leg."""
    fused = reciprocal_rank_fusion([
        ("multimodal", [("a", 0.9), ("shared", 0.5)]),
        ("text_memory", [("b", 0.95), ("shared", 0.4)]),
    ])
    order = [h.id for h in fused]
    assert order[0] == "shared"
    shared = fused[0]
    assert shared.sources == ["multimodal", "text_memory"]
    assert shared.score == pytest.approx(2 / 62, abs=1e-9)


def test_fusion_preserves_single_list_order():
    fused = reciprocal_rank_fusion([("only", [("x", 0.3), ("y", 0.2), ("z", 0.1)])])
    assert [h.id for h in fused] == ["x", "y", "z"]


def test_fusion_weights_scale_contribution():
    fused = reciprocal_rank_fusion(
        [("a", [("x", 1.0)]), ("b", [("y", 1.0)])],
        weights={"a": 3.0},
    )
    assert fused[0].id == "x"
    assert fused[0].score == pytest.approx(3 / 61, abs=1e-9)
    assert fused[1].score == pytest.approx(1 / 61, abs=1e-9)


def test_fusion_ties_break_by_id_for_determinism():
    fused = reciprocal_rank_fusion([("a", [("z", 1.0), ("y", 1.0)])])
    # equal RRF ranks can't tie; force a tie via two single-hit lists
    tied = reciprocal_rank_fusion([("a", [("bbb", 1.0)]), ("b", [("aaa", 1.0)])])
    assert [h.id for h in fused] == ["z", "y"]
    assert [h.id for h in tied] == ["aaa", "bbb"]


def test_fusion_empty_inputs():
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([("a", [])]) == []


def test_fusion_duplicate_id_in_one_list_counts_best_rank_once():
    fused = reciprocal_rank_fusion([("a", [("x", 0.9), ("x", 0.1)])])
    assert len(fused) == 1
    assert fused[0].score == pytest.approx(1 / 61, abs=1e-9)
    assert fused[0].sources == ["a"]


def test_fusion_top_truncation():
    fused = reciprocal_rank_fusion(
        [("a", [("1", 5.0), ("2", 4.0), ("3", 3.0)])], top=2
    )
    assert [h.id for h in fused] == ["1", "2"]


def test_fusion_best_score_payload_keeps_raw_scores():
    fused = reciprocal_rank_fusion([
        ("a", [("x", 0.31)]),
        ("b", [("x", 0.88)]),
    ])
    assert fused[0].payload["best_score"] == {"a": 0.31, "b": 0.88}


def test_fusion_rejects_nonpositive_k():
    with pytest.raises(ValueError, match="k must be positive"):
        reciprocal_rank_fusion([("a", [("x", 1.0)])], k=0)
