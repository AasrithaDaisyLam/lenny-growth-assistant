"""
Per-turn registry of citable sources.

The prompt numbers the sources it injects `S1..SN`. When the agent calls its own
tools mid-turn, those passages must be citable too, and their markers must not
collide with the injected ones -- so a turn keeps exactly one ordered list of
sources. The chat turn opens it with the injected chunks, the internal tool
endpoints append whatever they return and hand the marker back to the extension,
and citation validation resolves against the whole list.

No lock: everything here runs on the API's single event loop and no method
awaits, so a mutation cannot interleave with a read. The same reasoning applies to
`process_pool.peek`. Turns are keyed by session because one Pi process serves one
session, which is what makes a tool result attributable to the right turn.
"""

from __future__ import annotations

from app.retrieval.hybrid import RetrievedChunk


class TurnSources:
    """The ordered sources of one turn, addressed by their `S#` markers."""

    def __init__(self, injected: list[RetrievedChunk]) -> None:
        self._chunks: list[RetrievedChunk] = list(injected)

    def items(self) -> list[tuple[str, RetrievedChunk]]:
        """`(marker, chunk)` pairs in marker order, for prompts that must keep numbering."""
        return [(f"S{index}", chunk) for index, chunk in enumerate(self._chunks, start=1)]

    def chunks(self) -> list[RetrievedChunk]:
        """The chunks in marker order, for positional citation validation."""
        return list(self._chunks)

    def register(self, chunk: RetrievedChunk) -> str:
        """
        Add a tool-returned chunk and return the marker it is citable by.

        Re-returning a chunk already in the list yields its existing marker rather
        than a second one, so the same passage cannot be cited two ways in a turn.
        """
        for index, existing in enumerate(self._chunks, start=1):
            if existing.chunk_id == chunk.chunk_id:
                return f"S{index}"
        self._chunks.append(chunk)
        return f"S{len(self._chunks)}"


_turns: dict[str, TurnSources] = {}


def open_turn(session_id: str, injected: list[RetrievedChunk]) -> TurnSources:
    """Start a turn's source list from the chunks retrieval already injected."""
    sources = TurnSources(injected)
    _turns[session_id] = sources
    return sources


def for_session(session_id: str) -> TurnSources | None:
    """The open turn for a session, if one is running."""
    return _turns.get(session_id)


def close_turn(session_id: str) -> None:
    """Drop a finished turn's sources so they cannot leak into the next one."""
    _turns.pop(session_id, None)
