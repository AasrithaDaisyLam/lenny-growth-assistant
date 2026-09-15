"""Tool-returned passages must be citable by the same [S#] convention as injected ones."""

from __future__ import annotations

from app.retrieval.hybrid import RetrievedChunk
from app.services import source_registry
from app.services.cite_service import validate_citations


def _chunk(chunk_id: int, slug: str = "ep") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        episode_id=1,
        episode_slug=slug,
        episode_title="Episode",
        guest="Guest",
        youtube_url=None,
        chunk_index=chunk_id,
        speaker="Speaker",
        text=f"text {chunk_id}",
        score=0.0,
        similarity=0.7,
    )


def test_injected_chunks_keep_their_prompt_numbering() -> None:
    turn = source_registry.TurnSources([_chunk(1), _chunk(2)])
    assert [marker for marker, _ in turn.items()] == ["S1", "S2"]


def test_tool_passage_continues_the_injected_numbering() -> None:
    """A fetched passage must not collide with an injected one."""
    turn = source_registry.TurnSources([_chunk(1), _chunk(2)])
    assert turn.register(_chunk(99, slug="tool-ep")) == "S3"


def test_tool_passage_marker_resolves_in_validation() -> None:
    turn = source_registry.open_turn("session-a", [_chunk(1)])
    marker = turn.register(_chunk(99, slug="tool-ep"))

    result = validate_citations(f"Fetched mid-turn [{marker}].", turn.chunks())

    assert [c["chunk_id"] for c in result.citations] == [99]
    assert result.citations[0]["episode_slug"] == "tool-ep"
    source_registry.close_turn("session-a")


def test_repeated_passage_keeps_one_marker() -> None:
    """The same chunk cannot be cited two ways within a turn."""
    turn = source_registry.TurnSources([_chunk(1)])
    assert turn.register(_chunk(1)) == "S1"
    assert len(turn.chunks()) == 1


def test_closed_turn_is_no_longer_visible() -> None:
    source_registry.open_turn("session-b", [_chunk(1)])
    source_registry.close_turn("session-b")
    assert source_registry.for_session("session-b") is None
