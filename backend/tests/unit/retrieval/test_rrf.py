import pytest

from research_agent.retrieval.rrf import reciprocal_rank_fusion


def test_rrf_rewards_items_found_by_both_retrievers() -> None:
    fused = reciprocal_rank_fusion(
        lexical=["a", "b"],
        vector=["b", "c"],
        k=60,
    )

    assert fused[0][0] == "b"
    assert fused[0][1] == pytest.approx(1 / 62 + 1 / 61)
    assert fused[1:] == [("a", 1 / 61), ("c", 1 / 62)]


def test_rrf_uses_item_id_to_break_score_ties() -> None:
    assert reciprocal_rank_fusion(lexical=["b"], vector=["a"], k=60) == [
        ("a", 1 / 61),
        ("b", 1 / 61),
    ]


def test_rrf_rejects_non_positive_k() -> None:
    with pytest.raises(ValueError, match="positive"):
        reciprocal_rank_fusion(lexical=["a"], vector=[], k=0)
