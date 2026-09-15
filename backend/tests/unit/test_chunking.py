"""Chunking tests: the merge, the overlap, and the no-mid-sentence rule."""

from __future__ import annotations

from app.ingestion.chunking import chunk_turns, estimate_tokens
from app.ingestion.parser import Turn


def _words(count: int) -> str:
    return " ".join(f"w{i}" for i in range(count))


def test_empty_input_produces_no_chunks() -> None:
    assert chunk_turns([]) == []


def test_consecutive_turns_are_merged_towards_the_target() -> None:
    turns = [Turn("Ada", _words(4)) for _ in range(10)]
    chunks = chunk_turns(turns, target_tokens=20, overlap_ratio=0.15)

    assert len(chunks) > 1
    # Every chunk should be near the target rather than one turn each.
    assert all(chunk.token_count <= 20 for chunk in chunks)
    assert sum(chunk.token_count for chunk in chunks) >= 40


def test_turns_are_never_split_mid_sentence() -> None:
    text = "One two three. Four five six. Seven eight nine. Ten eleven twelve."
    chunks = chunk_turns([Turn("Ada", text)], target_tokens=6, overlap_ratio=0.15)

    assert len(chunks) >= 2
    for chunk in chunks:
        body = chunk.text.removeprefix("Ada: ").rstrip()
        assert body.endswith((".", "!", "?")), chunk.text


def test_overlap_carries_a_turn_across_the_boundary() -> None:
    turns = [Turn("Ada", word) for word in ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"]]
    chunks = chunk_turns(turns, target_tokens=10, overlap_ratio=0.2)

    assert len(chunks) >= 2
    # The last line of one chunk is repeated as the first line of the next, so an
    # idea spanning the join is retrievable from either side.
    assert chunks[0].text.splitlines()[-1] == chunks[1].text.splitlines()[0]


def test_chunk_indices_are_sequential() -> None:
    turns = [Turn("Ada", _words(5)) for _ in range(12)]
    chunks = chunk_turns(turns, target_tokens=15, overlap_ratio=0.2)
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))


def test_uniform_speaker_is_recorded_and_mixed_speakers_are_not() -> None:
    uniform = chunk_turns([Turn("Ada", "hello there friend")], target_tokens=50)
    assert uniform[0].speaker == "Ada"

    mixed = chunk_turns(
        [Turn("Ada", "hello there"), Turn("Grace", "general response")], target_tokens=50
    )
    assert mixed[0].speaker is None
    # Attribution still lives inside the text.
    assert "Ada:" in mixed[0].text and "Grace:" in mixed[0].text


def test_estimate_tokens_counts_words() -> None:
    assert estimate_tokens("one two three") == 3
    assert estimate_tokens("") == 0
