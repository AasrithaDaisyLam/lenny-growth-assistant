"""Fusion and abstention tests: pure logic, no database or model."""

from __future__ import annotations

from app.config import settings
from app.retrieval.hybrid import (
    RetrievedChunk,
    best_similarity,
    reciprocal_rank_fusion,
    should_abstain,
)

# The floor is calibrated and expected to move (`make calibrate`), so these tests
# are written relative to whatever it is configured to rather than to a literal.
FLOOR = settings.rag_min_score


def _chunk(chunk_id: int, similarity: float | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        episode_id=1,
        episode_slug="ep",
        episode_title="Episode",
        guest=None,
        youtube_url=None,
        chunk_index=chunk_id,
        speaker=None,
        text=f"chunk {chunk_id}",
        score=0.0,
        similarity=similarity,
    )


def test_fusion_ranks_agreement_highest() -> None:
    vector = [_chunk(1), _chunk(2)]
    lexical = [_chunk(1), _chunk(3)]

    fused = reciprocal_rank_fusion([vector, lexical], k=60)

    # Chunk 1 is top of both arms, so it must lead; 2 and 3 each appear once.
    assert fused[0].chunk_id == 1
    assert fused[0].score > fused[1].score


def test_fusion_deduplicates_a_chunk_found_by_both_arms() -> None:
    fused = reciprocal_rank_fusion([[_chunk(1), _chunk(2)], [_chunk(1), _chunk(3)]], k=60)
    assert [c.chunk_id for c in fused].count(1) == 1
    assert {c.chunk_id for c in fused} == {1, 2, 3}


def test_fusion_keeps_a_chunk_found_by_only_one_arm() -> None:
    fused = reciprocal_rank_fusion([[_chunk(1)], [_chunk(9)]], k=60)
    assert {c.chunk_id for c in fused} == {1, 9}


def test_fusion_preserves_similarity_from_whichever_arm_has_it() -> None:
    # A lexical-only first arm carries no cosine value; the dense copy must win.
    lexical = [_chunk(1)]
    vector = [_chunk(1, similarity=0.7)]

    fused = reciprocal_rank_fusion([lexical, vector], k=60)

    assert fused[0].similarity == 0.7


def test_fusion_is_deterministic() -> None:
    left = [_chunk(1), _chunk(2)]
    right = [_chunk(2), _chunk(3)]
    first = [c.chunk_id for c in reciprocal_rank_fusion([left, right], k=60)]
    second = [c.chunk_id for c in reciprocal_rank_fusion([list(left), list(right)], k=60)]
    assert first == second


def test_best_similarity_ignores_missing_values() -> None:
    assert best_similarity([_chunk(1), _chunk(2, 0.3), _chunk(3, 0.9)]) == 0.9
    assert best_similarity([_chunk(1), _chunk(2)]) is None


def test_abstains_on_empty_results() -> None:
    assert should_abstain([]) is True


def test_abstains_below_the_floor() -> None:
    assert should_abstain([_chunk(1, similarity=FLOOR - 0.01)]) is True


def test_does_not_abstain_at_or_above_the_floor() -> None:
    assert should_abstain([_chunk(1, similarity=FLOOR)]) is False
    assert should_abstain([_chunk(1, similarity=FLOOR + 0.2)]) is False


def test_lexical_only_results_are_not_treated_as_ungrounded() -> None:
    """
    No dense score means the floor has nothing to compare, and a literal term
    match is positive evidence of coverage -- so this must not abstain.
    """
    assert should_abstain([_chunk(1), _chunk(2)]) is False
