"""
Retrieval-only search.

Deliberately model-free: this endpoint exercises ingestion and the retriever
without an LLM in the loop. That makes retrieval latency measurable on its own,
makes a bad answer attributable to retrieval rather than generation, and makes
this the surface the relevance floor is calibrated against.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.retrieval.hybrid import (
    best_similarity,
    search,
    should_abstain,
    topic_suggestions,
)
from app.schemas.search import SearchResponse, SearchResult

log = structlog.get_logger("app.search")
router = APIRouter(tags=["search"])


@router.get("/search", response_model=SearchResponse, summary="Retrieval only, no model")
async def search_endpoint(
    db: Annotated[AsyncSession, Depends(get_db)],
    q: Annotated[str, Query(min_length=1, description="Natural-language query")],
    k: Annotated[int | None, Query(ge=1, le=50, description="Chunks to return")] = None,
) -> SearchResponse:
    top_k = k or settings.rag_top_k
    chunks = await search(db, q, k=top_k)

    abstained = should_abstain(chunks)
    top = best_similarity(chunks)
    suggestions = await topic_suggestions(db) if abstained else []

    log.info(
        "search_completed",
        k=top_k,
        returned=len(chunks),
        abstained=abstained,
        best_similarity=top,
    )

    return SearchResponse(
        query=q,
        k=top_k,
        count=len(chunks),
        abstained=abstained,
        best_similarity=top,
        topics=suggestions,
        results=[
            SearchResult(
                chunk_id=chunk.chunk_id,
                episode_slug=chunk.episode_slug,
                episode_title=chunk.episode_title,
                guest=chunk.guest,
                youtube_url=chunk.youtube_url,
                chunk_index=chunk.chunk_index,
                speaker=chunk.speaker,
                text=chunk.text,
                score=chunk.score,
                similarity=chunk.similarity,
            )
            for chunk in chunks
        ],
    )
