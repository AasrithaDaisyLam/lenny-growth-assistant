"""
Artifact endpoints.

`/render` is the third containment layer: it serves the *sanitized* HTML only, with
a no-egress CSP attached. Artifacts are considered untrusted even when a frontier
cloud model produced them, because a prompt-injected transcript could steer either
provider -- so the CSP is applied unconditionally rather than to a suspicious subset.

The fourth layer is in the browser (`SandboxFrame`): an iframe with
`sandbox="allow-scripts"` and **never** `allow-same-origin`, which is the specific
combination that would defeat sandboxing.
"""

from __future__ import annotations

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.artifact import ArtifactCreate, ArtifactRead, ArtifactRender
from app.services import artifact_service, session_service

log = structlog.get_logger("app.artifacts")
router = APIRouter(tags=["artifacts"])

# No egress: an artifact cannot load remote images, fonts, or scripts, and cannot
# be framed or submit a form. `'unsafe-inline'` for scripts is required by
# self-contained charts and is contained by the iframe sandbox in the viewer -- an
# accepted trade-off, since a data-exfiltrating artifact is far worse than a
# missing web font.
ARTIFACT_CSP = (
    "default-src 'none'; "
    "img-src data:; "
    "style-src 'unsafe-inline'; "
    "font-src data:; "
    "script-src 'unsafe-inline'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'"
)


@router.post(
    "/sessions/{session_id}/artifacts",
    response_model=ArtifactRead,
    status_code=201,
    summary="Generate an artifact for a session",
)
async def create_artifact(
    session_id: uuid.UUID,
    payload: ArtifactCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ArtifactRead:
    await session_service.get_session(db, session_id)
    artifact = await artifact_service.create_artifact(
        db,
        session_id,
        kind=payload.kind,
        title=payload.title,
        content=payload.content,
    )
    return ArtifactRead.model_validate(artifact)


@router.get(
    "/sessions/{session_id}/artifacts",
    response_model=list[ArtifactRead],
    summary="Artifacts for a session",
)
async def list_artifacts(
    session_id: uuid.UUID, db: Annotated[AsyncSession, Depends(get_db)]
) -> list[ArtifactRead]:
    await session_service.get_session(db, session_id)
    artifacts = await artifact_service.list_artifacts(db, session_id)
    return [ArtifactRead.model_validate(artifact) for artifact in artifacts]


@router.get(
    "/artifacts/{artifact_id}",
    response_model=ArtifactRender,
    summary="Artifact metadata, sanitized and raw",
)
async def get_artifact(
    artifact_id: uuid.UUID, db: Annotated[AsyncSession, Depends(get_db)]
) -> ArtifactRender:
    artifact = await artifact_service.get_artifact(db, artifact_id)
    return ArtifactRender(
        id=artifact.id,
        session_id=artifact.session_id,
        kind=artifact.kind,
        title=artifact.title,
        version=artifact.version,
        sanitized_content=artifact.sanitized_content,
        raw_content=artifact.raw_content,
        removed_elements=artifact.removed_elements,
    )


@router.get(
    "/artifacts/{artifact_id}/render",
    response_class=HTMLResponse,
    summary="Sanitized HTML with a no-egress CSP",
)
async def render_artifact(
    artifact_id: uuid.UUID, db: Annotated[AsyncSession, Depends(get_db)]
) -> HTMLResponse:
    artifact = await artifact_service.get_artifact(db, artifact_id)
    log.info("artifact_rendered", artifact_id=str(artifact_id), kind=artifact.kind)
    return HTMLResponse(
        content=artifact.sanitized_content,
        headers={
            "Content-Security-Policy": ARTIFACT_CSP,
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
        },
    )


@router.post(
    "/artifacts/{artifact_id}/regenerate",
    response_model=ArtifactRead,
    status_code=201,
    summary="New version, re-sanitized from the same raw content",
)
async def regenerate_artifact(
    artifact_id: uuid.UUID, db: Annotated[AsyncSession, Depends(get_db)]
) -> ArtifactRead:
    artifact = await artifact_service.regenerate(db, artifact_id)
    return ArtifactRead.model_validate(artifact)
