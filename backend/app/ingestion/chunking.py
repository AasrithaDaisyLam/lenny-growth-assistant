"""
Speaker-aware chunking.

A fixed-size split is actively wrong for transcripts: it cuts mid-sentence and
scatters a single answer across chunks, so a citation points at half a thought.
Instead, consecutive turns are merged up to a target size and a small overlap is
carried across the boundary so an idea that spans a join is still retrievable
from either side. Turns are never split mid-sentence.

Sizing counts whitespace-separated words as a stand-in for tokens. That is a
deliberate approximation: it keeps ingestion dependency-free, and it is the same
unit on both sides of the comparison, so it does not bias retrieval. The budget
is measured against the *rendered* chunk -- the text that is actually embedded,
including the `Speaker: ` prefix -- so `token_count` never drifts from the target
it was supposed to respect. A real tokenizer can be swapped in later without a
schema change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.ingestion.parser import Turn

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(slots=True)
class Chunk:
    index: int
    speaker: str | None
    text: str
    token_count: int


def estimate_tokens(text: str) -> int:
    """Whitespace word count; the project-wide stand-in for tokens (see module docstring)."""
    return len(text.split())


def _line(turn: Turn) -> str:
    return f"{turn.speaker}: {turn.text}" if turn.speaker else turn.text


def _cost(turn: Turn) -> int:
    """What this turn adds to the rendered chunk, prefix included."""
    return estimate_tokens(_line(turn))


def _split_long_turn(turn: Turn, max_tokens: int) -> list[Turn]:
    """
    Break a single oversized turn on sentence boundaries.

    A sentence longer than the budget is kept whole rather than truncated: an
    incomplete sentence is worse for retrieval than an oversized chunk.
    """
    sentences = [s for s in _SENTENCE_SPLIT_RE.split(turn.text) if s]
    pieces: list[Turn] = []
    buffer: list[str] = []

    for sentence in sentences:
        candidate = Turn(speaker=turn.speaker, text=" ".join([*buffer, sentence]))
        if buffer and _cost(candidate) > max_tokens:
            pieces.append(Turn(speaker=turn.speaker, text=" ".join(buffer)))
            buffer = [sentence]
        else:
            buffer.append(sentence)
    if buffer:
        pieces.append(Turn(speaker=turn.speaker, text=" ".join(buffer)))

    return pieces


def _render(units: list[Turn]) -> Chunk:
    """
    Render a group of turns into chunk text with speaker labels inlined.

    Attribution has to survive inside the text: a chunk spanning several speakers
    cannot be described by the single `speaker` column, and the model needs to
    know who said what in order to cite a person rather than a blob.
    """
    speakers = {u.speaker for u in units}
    speaker = units[0].speaker if len(speakers) == 1 else None

    text = "\n".join(_line(u) for u in units)
    return Chunk(index=0, speaker=speaker, text=text, token_count=estimate_tokens(text))


def chunk_turns(
    turns: list[Turn], target_tokens: int = 500, overlap_ratio: float = 0.15
) -> list[Chunk]:
    """
    Merge turns into overlapping chunks of roughly `target_tokens`.

    Overlap is expressed as a fraction of the target and is applied by rewinding
    to the turn boundary that fits inside the overlap budget, which keeps every
    chunk starting on a whole turn.
    """
    if not turns:
        return []

    max_tokens = max(1, target_tokens)
    overlap_tokens = max(1, int(max_tokens * overlap_ratio))

    units: list[Turn] = []
    for turn in turns:
        if _cost(turn) > max_tokens:
            units.extend(_split_long_turn(turn, max_tokens))
        else:
            units.append(turn)

    chunks: list[Chunk] = []
    start = 0
    while start < len(units):
        end = start
        total = 0
        # Always take at least one unit, even if it alone exceeds the target.
        while end < len(units) and (total == 0 or total + _cost(units[end]) <= max_tokens):
            total += _cost(units[end])
            end += 1

        chunks.append(_render(units[start:end]))

        if end >= len(units):
            break

        # Rewind from the end to carry the overlap, but never back past the
        # chunk we just emitted, so the walk always strictly advances.
        rewind = end
        carried = 0
        while rewind > start + 1 and carried + _cost(units[rewind - 1]) <= overlap_tokens:
            rewind -= 1
            carried += _cost(units[rewind])

        start = max(rewind, start + 1)

    for index, chunk in enumerate(chunks):
        chunk.index = index
    return chunks
