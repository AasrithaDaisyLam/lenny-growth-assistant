"""
Artifact persistence.

Every artifact is stored twice on purpose: `raw_content` is exactly what the model
produced, and `sanitized_content` is the only thing the viewer may render. Keeping
the raw form makes stripping auditable after the fact instead of destroying the
evidence, and `removed_elements` records what was taken out so the loss is visible
to the user rather than silent.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.artifacts.sanitize import sanitize
from app.core.errors import NotFoundError
from app.models.conversation import Artifact

log = structlog.get_logger("app.artifacts")


async def _next_version(db: AsyncSession, session_id: uuid.UUID, title: str | None) -> int:
    statement = select(func.coalesce(func.max(Artifact.version), 0)).where(
        Artifact.session_id == session_id, Artifact.title == title
    )
    return int((await db.execute(statement)).scalar_one()) + 1


async def create_artifact(
    db: AsyncSession,
    session_id: uuid.UUID,
    *,
    kind: str,
    content: str,
    title: str | None = None,
    message_id: int | None = None,
) -> Artifact:
    result = sanitize(kind, content)
    artifact = Artifact(
        session_id=session_id,
        message_id=message_id,
        kind=kind,
        title=title,
        raw_content=content,
        sanitized_content=result.cleaned,
        removed_elements=result.removed,
        version=await _next_version(db, session_id, title),
    )
    db.add(artifact)
    await db.commit()
    await db.refresh(artifact)
    log.info(
        "artifact_created",
        artifact_id=str(artifact.id),
        kind=kind,
        version=artifact.version,
        removed=len(result.removed),
    )
    return artifact


async def list_artifacts(db: AsyncSession, session_id: uuid.UUID) -> list[Artifact]:
    statement = (
        select(Artifact)
        .where(Artifact.session_id == session_id)
        .order_by(Artifact.created_at, Artifact.id)
    )
    return list((await db.execute(statement)).scalars().all())


async def get_artifact(db: AsyncSession, artifact_id: uuid.UUID) -> Artifact:
    artifact = await db.get(Artifact, artifact_id)
    if artifact is None:
        raise NotFoundError(f"Artifact {artifact_id} not found.")
    return artifact


async def regenerate(db: AsyncSession, artifact_id: uuid.UUID) -> Artifact:
    """
    Re-sanitize the stored raw content and record the result as a new version.

    The value is not that sanitization is non-deterministic -- it is that the rules
    can change. When the allowlist is tightened, this re-applies it to existing
    artifacts without needing the model to regenerate anything.
    """
    previous = await get_artifact(db, artifact_id)
    result = sanitize(previous.kind, previous.raw_content)

    regenerated = Artifact(
        session_id=previous.session_id,
        message_id=previous.message_id,
        kind=previous.kind,
        title=previous.title,
        raw_content=previous.raw_content,
        sanitized_content=result.cleaned,
        removed_elements=result.removed,
        version=await _next_version(db, previous.session_id, previous.title),
    )
    db.add(regenerated)
    await db.commit()
    await db.refresh(regenerated)
    log.info(
        "artifact_regenerated",
        artifact_id=str(regenerated.id),
        from_version=previous.version,
        to_version=regenerated.version,
    )
    return regenerated
