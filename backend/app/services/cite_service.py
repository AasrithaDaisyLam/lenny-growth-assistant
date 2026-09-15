"""
Citation validation.

The model is asked to cite `[S1]`, `[S2]`, ... against the numbered sources it was
given. This module enforces that the markers it produces actually resolve.

It matters because an unresolvable citation is worse than no citation: `[S7]` in a
five-source answer reads as rigour while pointing at nothing. Markers outside the
provided range are therefore *stripped from the text and reported*, so the stored
answer never contains a citation that cannot be resolved, and the fact that one was
produced is still visible in the trace and the logs rather than silently repaired.

Two distinctions are kept deliberately narrow:

- **Normalisation is only safe when there is nothing to guess.** `[s1]` and
  `[S 1]` are the canonical marker with different casing/spacing, so they are
  rewritten to `[S1]`. `[Source 1]`, `[S1, S2]` and `[S1-S3]` are source-shaped
  but not the contract, so they are *reported* rather than interpreted.
- **Anything that is not source-shaped is left alone.** `[appendix]` is prose,
  not a citation, and rewriting it would corrupt the answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.retrieval.hybrid import RetrievedChunk

# The canonical marker, tolerating case and inner spacing because both map to the
# same source with nothing to disambiguate. `[S1]` is what is written back out.
_CANONICAL_RE = re.compile(r"\[([Ss])\s*(\d+)\]")

# Every bracketed span, so each one can be classified rather than only the ones
# that already look canonical. Without this, `[s1]` was invisible: neither
# resolved nor reported.
_BRACKET_RE = re.compile(r"\[[^\[\]]{0,60}\]")

# Source-shaped but not the contract: a list, a range, a spelled-out word, or a
# bare `[S]`. Reported and stripped, never interpreted -- guessing which source
# `[S1, S2]` means is exactly the silent acceptance this module exists to prevent.
_NEAR_MISS_RE = re.compile(
    r"^\[\s*(?:"
    r"[Ss]\s*\d+\s*(?:[,&;/]|\s+and\s+|\s*[-–]\s*)[^\]]*"
    r"|(?:the\s+)?(?:sources?|srcs?|citations?|refs?)[\s.:#]*\d*"
    r"|[Ss]"
    r")\s*\]$",
    re.IGNORECASE,
)

# Shorter than this and the turn produced a refusal or a fragment, not a claim to
# ground -- so an absent citation is not evidence of an ungrounded answer.
MIN_SUBSTANTIVE_CHARS = 80


@dataclass
class ValidationResult:
    text: str
    citations: list[dict] = field(default_factory=list)
    unknown_markers: list[str] = field(default_factory=list)

    @property
    def had_citations(self) -> bool:
        return bool(self.citations)


def is_substantive(text: str) -> bool:
    """Whether the text is long enough to be an answer rather than a refusal."""
    return len(text.strip()) >= MIN_SUBSTANTIVE_CHARS


def needs_citation_retry(result: ValidationResult, chunks: list[RetrievedChunk]) -> bool:
    """
    Whether a grounded answer was produced without a single resolvable citation.

    Only meaningful when sources were actually retrieved: with none, there is
    nothing to cite and an uncited answer is the correct outcome, not a defect.
    """
    if not chunks or result.citations:
        return False
    return is_substantive(result.text)


def _citation_from_chunk(marker: str, chunk: RetrievedChunk) -> dict:
    return {
        "marker": marker,
        "chunk_id": chunk.chunk_id,
        "episode_slug": chunk.episode_slug,
        "episode_title": chunk.episode_title,
        "guest": chunk.guest,
        "youtube_url": chunk.youtube_url,
        "chunk_index": chunk.chunk_index,
        "speaker": chunk.speaker,
    }


def validate_citations(text: str, chunks: list[RetrievedChunk]) -> ValidationResult:
    """
    Resolve `[S#]` markers against the sources actually retrieved for this turn.

    Sources are numbered from 1 in the order they were presented, so the mapping
    is positional and must match the prompt exactly. Tool-returned passages are
    appended to that same order by `source_registry`, which is why their markers
    resolve here too without any extra bookkeeping.
    """
    valid = {str(index): chunk for index, chunk in enumerate(chunks, start=1)}
    used: list[str] = []
    unknown: list[str] = []

    def replace(match: re.Match[str]) -> str:
        span = match.group(0)

        canonical = _CANONICAL_RE.fullmatch(span)
        if canonical is not None:
            number = canonical.group(2)
            if number in valid:
                if number not in used:
                    used.append(number)
                return f"[S{number}]"
            unknown.append(span)
            return ""

        if _NEAR_MISS_RE.match(span):
            unknown.append(span)
            return ""

        # Prose brackets are not citations and are left untouched.
        return span

    cleaned = _BRACKET_RE.sub(replace, text)
    if unknown:
        # Removing a marker can leave a doubled space where it stood.
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r" +([.,;:])", r"\1", cleaned)

    citations = [_citation_from_chunk(f"S{number}", valid[number]) for number in used]
    return ValidationResult(text=cleaned.strip(), citations=citations, unknown_markers=unknown)
