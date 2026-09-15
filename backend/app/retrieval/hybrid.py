"""
Hybrid retrieval.

Two arms with different failure modes are fused rather than one being trusted:

- **Dense** (pgvector cosine) matches meaning, so "how do I keep users?" finds a
  passage about retention. It blurs exact strings -- product names, people, odd
  spellings.
- **Lexical** (Postgres full-text) matches terms exactly, so "Lenny Rachitsky" or
  a specific product name lands. It misses paraphrase entirely.

Fusing them with Reciprocal Rank Fusion is what gets both, and RRF needs no score
normalisation between two incomparable scales (cosine similarity vs `ts_rank_cd`),
which is exactly why it is used here rather than a weighted sum.

The relevance floor is a separate concern from the fusion. RRF decides *ordering*;
the floor decides whether the corpus covers the question at all, and it is
compared against the dense similarity because that is the scale it was calibrated
on. `docs/decisions.md` (ADR-003) records why one floor cannot do all the work.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.corpus import Chunk, Episode, Topic, episode_topics
from app.services.ollama import ollama

log = structlog.get_logger("app.retrieval")


@dataclass(slots=True)
class RetrievedChunk:
    chunk_id: int
    episode_id: int
    episode_slug: str
    episode_title: str
    guest: str | None
    youtube_url: str | None
    chunk_index: int
    speaker: str | None
    text: str
    score: float
    similarity: float | None = None


def _to_chunk(
    chunk: Chunk,
    episode: Episode,
    *,
    score: float,
    similarity: float | None = None,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=int(chunk.id),
        episode_id=int(episode.id),
        episode_slug=episode.slug,
        episode_title=episode.title,
        guest=episode.guest,
        youtube_url=episode.youtube_url,
        chunk_index=int(chunk.chunk_index),
        speaker=chunk.speaker,
        text=chunk.text,
        score=score,
        similarity=similarity,
    )


def chunk_from_orm(chunk: Chunk, episode: Episode, *, score: float = 0.0) -> RetrievedChunk:
    """
    Public form of `_to_chunk`, for callers outside retrieval.

    The internal tools hand back passages that did not come from a ranked search
    (a whole episode, say), but they still have to be citable, which means the
    registry and the citation validator both need them in this shape.
    """
    return _to_chunk(chunk, episode, score=score)


async def vector_search(
    db: AsyncSession,
    query: str,
    k: int | None = None,
) -> list[RetrievedChunk]:
    """Top-k chunks by cosine similarity. `similarity` is in [-1, 1] -- the floor's scale."""
    top_k = k or settings.rag_top_k

    embedding = await ollama.embed_one(query)
    similarity = (1 - Chunk.embedding.cosine_distance(embedding)).label("similarity")

    statement = (
        select(Chunk, Episode, similarity)
        .join(Episode, Chunk.episode_id == Episode.id)
        .where(Chunk.embedding.is_not(None))
        .order_by(similarity.desc())
        .limit(top_k)
    )

    rows = (await db.execute(statement)).all()
    return [
        _to_chunk(chunk, episode, score=float(value), similarity=float(value))
        for chunk, episode, value in rows
    ]


async def lexical_search(
    db: AsyncSession,
    query: str,
    k: int | None = None,
) -> list[RetrievedChunk]:
    """
    Top-k chunks by full-text rank.

    `websearch_to_tsquery` is used because it accepts user-shaped input (quoted
    phrases, `or`, `-term`) instead of requiring the caller to build `tsquery`
    syntax, and it cannot raise on malformed input.
    """
    top_k = k or settings.rag_lexical_candidates
    tsquery = func.websearch_to_tsquery("english", query)
    rank = func.ts_rank_cd(Chunk.tsv, tsquery).label("rank")

    statement = (
        select(Chunk, Episode, rank)
        .join(Episode, Chunk.episode_id == Episode.id)
        .where(Chunk.tsv.op("@@")(tsquery))
        .order_by(rank.desc())
        .limit(top_k)
    )

    rows = (await db.execute(statement)).all()
    # No cosine value here, so `similarity` stays None and the floor cannot judge
    # this arm -- see `should_abstain`.
    return [_to_chunk(chunk, episode, score=float(value)) for chunk, episode, value in rows]


def reciprocal_rank_fusion(
    rankings: list[list[RetrievedChunk]], k: int | None = None
) -> list[RetrievedChunk]:
    """
    Fuse ranked lists: `score = Σ 1/(k + rank)`.

    Deduplicates by chunk id, so a chunk found by both arms accumulates both
    contributions and rises -- that agreement is the signal RRF is built on.
    """
    rrf_k = k if k is not None else settings.rag_rrf_k
    totals: dict[int, float] = {}
    best: dict[int, RetrievedChunk] = {}

    for ranking in rankings:
        for rank, chunk in enumerate(ranking, start=1):
            totals[chunk.chunk_id] = totals.get(chunk.chunk_id, 0.0) + 1.0 / (rrf_k + rank)
            kept = best.get(chunk.chunk_id)
            # Prefer whichever copy carries a dense similarity, so the floor still
            # has something to compare after fusion.
            if kept is None or (kept.similarity is None and chunk.similarity is not None):
                best[chunk.chunk_id] = chunk

    fused = sorted(best.values(), key=lambda c: totals[c.chunk_id], reverse=True)
    for chunk in fused:
        chunk.score = totals[chunk.chunk_id]
    return fused


async def search(db: AsyncSession, query: str, k: int | None = None) -> list[RetrievedChunk]:
    """Hybrid search: both arms, fused, top-k."""
    top_k = k or settings.rag_top_k

    vector = await vector_search(db, query, k=settings.rag_vector_candidates)
    lexical = await lexical_search(db, query, k=settings.rag_lexical_candidates)
    fused = reciprocal_rank_fusion([vector, lexical], k=settings.rag_rrf_k)

    log.info(
        "hybrid_search",
        vector_hits=len(vector),
        lexical_hits=len(lexical),
        fused=len(fused),
        returned=min(top_k, len(fused)),
    )
    return fused[:top_k]


def best_similarity(results: list[RetrievedChunk]) -> float | None:
    values = [c.similarity for c in results if c.similarity is not None]
    return max(values) if values else None


def should_abstain(results: list[RetrievedChunk]) -> bool:
    """
    Whether the corpus fails to support an answer.

    Gated on the dense similarity, because that is the scale `RAG_MIN_SCORE` was
    calibrated on. Note the deliberate asymmetry: a result set with *no* dense
    score (lexical-only) is not treated as ungrounded. A lexical hit requires
    literal term overlap, which is harder to produce spuriously than a dense
    near-match, so it is evidence of coverage rather than of absence.
    """
    if not results:
        return True
    best = best_similarity(results)
    if best is None:
        return False
    return best < settings.rag_min_score


async def topic_suggestions(db: AsyncSession, limit: int = 8) -> list[str]:
    """
    What the corpus *does* cover, most-populated topics first.

    This is what turns an abstention from a dead end into a useful answer:
    "no, but here is what I can help with".
    """
    statement = (
        select(Topic.name)
        .join(episode_topics, episode_topics.c.topic_id == Topic.id)
        .group_by(Topic.name)
        .order_by(func.count().desc(), Topic.name)
        .limit(limit)
    )
    return [row[0] for row in (await db.execute(statement)).all()]
