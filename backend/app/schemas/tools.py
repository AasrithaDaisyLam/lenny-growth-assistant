"""Contracts for the internal, extension-called tool endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TranscriptSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    k: int | None = Field(default=None, ge=1, le=20)


class TranscriptHit(BaseModel):
    # The marker this passage is citable by in the running turn (`S7`, ...). The
    # extension prints it next to the passage so the model can cite what it read
    # rather than only the sources the prompt injected.
    marker: str
    chunk_id: int
    episode_slug: str
    episode_title: str
    guest: str | None = None
    speaker: str | None = None
    score: float
    similarity: float | None = None
    text: str


class TranscriptSearchResponse(BaseModel):
    query: str
    count: int
    results: list[TranscriptHit]


class EpisodeRequest(BaseModel):
    slug: str = Field(min_length=1)


class EpisodeResponse(BaseModel):
    slug: str
    title: str
    guest: str | None = None
    youtube_url: str | None = None
    chunk_count: int
    transcript: str


class ArtifactSaveRequest(BaseModel):
    kind: Literal["markdown", "html"]
    title: str | None = None
    content: str = Field(min_length=1)


class ArtifactSavedResponse(BaseModel):
    artifact_id: str
    kind: str
    title: str | None = None
    version: int
    removed_count: int = Field(
        description="How many constructs sanitization stripped, so the strip is visible."
    )
