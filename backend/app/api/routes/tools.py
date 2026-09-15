"""
Internal tool endpoints, called by the Pi extension.

These are not part of the public API surface (`include_in_schema=False`) and are
gated by a shared secret. They exist because the extension is a thin shim: Pi
supplies the agent loop, Python keeps sole ownership of retrieval, embeddings, and
database access, so there is one implementation of RAG rather than two that can
drift apart.

The token is compared with `secrets.compare_digest` so the check cannot leak the
secret's length or content through timing.
"""

from __future__ import annotations

import secrets
import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.models.corpus import Chunk, Episode
from app.retrieval.hybrid import chunk_from_orm, search
from app.schemas.tools import (
    ArtifactSavedResponse,
    ArtifactSaveRequest,
    EpisodeRequest,
    EpisodeResponse,
    TranscriptHit,
    TranscriptSearchRequest,
    TranscriptSearchResponse,
)
from app.services import artifact_service, session_service, source_registry

log = structlog.get_logger("app.tools")
router = APIRouter(prefix="/tools", tags=["internal"], include_in_schema=False)


async def require_internal_token(
    x_internal_token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
) -> None:
    expected = settings.internal_tool_token
    if not x_internal_token or not secrets.compare_digest(x_internal_token, expected):
        from app.core.errors import AppError

        class Unauthorized(AppError):
            status_code = 401
            code = "unauthorized"
            message = "Missing or invalid internal tool token."

        raise Unauthorized()


@router.post(
    "/search_transcripts",
    response_model=TranscriptSearchResponse,
    dependencies=[Depends(require_internal_token)],
    summary="Hybrid retrieval for mid-turn follow-up searches",
)
async def search_transcripts(
    payload: TranscriptSearchRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_session_id: Annotated[str | None, Header(alias="X-Session-Id")] = None,
) -> TranscriptSearchResponse:
    chunks = await search(db, payload.query, k=payload.k)
    sources = source_registry.for_session(x_session_id) if x_session_id else None
    log.info("internal_search", query=payload.query[:120], returned=len(chunks))
    return TranscriptSearchResponse(
        query=payload.query,
        count=len(chunks),
        results=[
            TranscriptHit(
                marker=sources.register(chunk) if sources else "",
                chunk_id=chunk.chunk_id,
                episode_slug=chunk.episode_slug,
                episode_title=chunk.episode_title,
                guest=chunk.guest,
                speaker=chunk.speaker,
                score=chunk.score,
                similarity=chunk.similarity,
                text=chunk.text,
            )
            for chunk in chunks
        ],
    )


@router.post(
    "/get_episode",
    response_model=EpisodeResponse,
    dependencies=[Depends(require_internal_token)],
    summary="Full transcript for one episode",
)
async def get_episode(
    payload: EpisodeRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_session_id: Annotated[str | None, Header(alias="X-Session-Id")] = None,
) -> EpisodeResponse:
    episode = (
        await db.execute(select(Episode).where(Episode.slug == payload.slug))
    ).scalar_one_or_none()
    if episode is None:
        from app.core.errors import NotFoundError

        raise NotFoundError(f"Episode '{payload.slug}' not found.")

    rows = list(
        (
            await db.execute(
                select(Chunk).where(Chunk.episode_id == episode.id).order_by(Chunk.chunk_index)
            )
        )
        .scalars()
        .all()
    )

    # Each passage keeps its own marker so a whole-episode fetch is citable too,
    # and the numbering continues the turn's existing sources rather than
    # restarting at S1.
    sources = source_registry.for_session(x_session_id) if x_session_id else None
    parts: list[str] = []
    for chunk in rows:
        if sources is None:
            parts.append(chunk.text)
            continue
        marker = sources.register(chunk_from_orm(chunk, episode))
        parts.append(f"[{marker}]\n{chunk.text}")

    log.info("internal_get_episode", slug=payload.slug, chunks=len(parts))

    return EpisodeResponse(
        slug=episode.slug,
        title=episode.title,
        guest=episode.guest,
        youtube_url=episode.youtube_url,
        chunk_count=len(parts),
        transcript="\n\n".join(parts),
    )


@router.post(
    "/save_artifact",
    response_model=ArtifactSavedResponse,
    dependencies=[Depends(require_internal_token)],
    summary="Persist a generated artifact, sanitized",
)
async def save_artifact(
    payload: ArtifactSaveRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_session_id: Annotated[str | None, Header(alias="X-Session-Id")] = None,
) -> ArtifactSavedResponse:
    """
    The session comes from the request header, which the extension fills from the
    env var the pool sets for its process -- one Pi process serves one session, so
    the agent cannot write into a different conversation.
    """
    if not x_session_id:
        from app.core.errors import ValidationError

        raise ValidationError("X-Session-Id header is required to save an artifact.")

    try:
        session_id = uuid.UUID(x_session_id)
    except ValueError as exc:
        from app.core.errors import ValidationError

        raise ValidationError(f"X-Session-Id is not a UUID: {x_session_id}") from exc

    await session_service.get_session(db, session_id)
    artifact = await artifact_service.create_artifact(
        db,
        session_id,
        kind=payload.kind,
        title=payload.title,
        content=payload.content,
    )
    return ArtifactSavedResponse(
        artifact_id=str(artifact.id),
        kind=artifact.kind,
        title=artifact.title,
        version=artifact.version,
        removed_count=len(artifact.removed_elements),
    )
