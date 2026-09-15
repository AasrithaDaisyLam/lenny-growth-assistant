"""Response contracts for retrieval-only search."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SearchResult(BaseModel):
    chunk_id: int
    episode_slug: str
    episode_title: str
    guest: str | None = None
    youtube_url: str | None = None
    chunk_index: int
    speaker: str | None = None
    text: str
    score: float = Field(
        description="Ranking score: RRF fusion score for hybrid results, otherwise the arm's own score."
    )
    similarity: float | None = Field(
        default=None, description="Cosine similarity, present when the dense arm matched."
    )


class SearchResponse(BaseModel):
    query: str
    k: int
    count: int
    abstained: bool = Field(description="True when nothing clears the relevance floor.")
    best_similarity: float | None = Field(
        default=None, description="Highest cosine similarity in the result set."
    )
    topics: list[str] = Field(
        default_factory=list,
        description="What the corpus does cover; populated only when abstaining.",
    )
    results: list[SearchResult]
