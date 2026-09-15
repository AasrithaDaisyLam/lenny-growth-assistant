"""Session CRUD."""

from __future__ import annotations

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.pi_client import PiError
from app.agent.process_pool import pool
from app.db.session import get_db
from app.schemas.session import SessionCreate, SessionRead, SessionUpdate
from app.services import session_service

log = structlog.get_logger("app.sessions")
router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post(
    "", response_model=SessionRead, status_code=status.HTTP_201_CREATED, summary="New chat"
)
async def create_session(
    payload: SessionCreate, db: Annotated[AsyncSession, Depends(get_db)]
) -> SessionRead:
    session = await session_service.create_session(
        db,
        title=payload.title,
        provider=payload.provider,
        user_metadata=payload.user_metadata,
    )
    return SessionRead.model_validate(session)


@router.get("", response_model=list[SessionRead], summary="List sessions")
async def list_sessions(db: Annotated[AsyncSession, Depends(get_db)]) -> list[SessionRead]:
    sessions = await session_service.list_sessions(db)
    return [SessionRead.model_validate(session) for session in sessions]


@router.get("/{session_id}", response_model=SessionRead, summary="Session detail")
async def get_session(
    session_id: uuid.UUID, db: Annotated[AsyncSession, Depends(get_db)]
) -> SessionRead:
    session = await session_service.get_session(db, session_id)
    return SessionRead.model_validate(session)


@router.patch("/{session_id}", response_model=SessionRead, summary="Rename or switch provider")
async def update_session(
    session_id: uuid.UUID,
    payload: SessionUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SessionRead:
    session = await session_service.update_session(
        db,
        session_id,
        title=payload.title,
        provider=payload.provider,
        user_metadata=payload.user_metadata,
    )
    if payload.provider is not None:
        # The requirement is a switch with no restart. If a process is live for
        # this session, change its model over RPC so the warm conversation
        # survives the switch; if not, there is nothing to switch and the next
        # turn spawns with the new model and rehydrates from Postgres.
        client = pool.peek(str(session_id))
        if client is None:
            log.info(
                "provider_switch_on_next_spawn", session_id=str(session_id), model=session.model
            )
        else:
            try:
                await client.set_model(session.provider, session.model)
                log.info("provider_switched_live", session_id=str(session_id), model=session.model)
            except PiError as exc:
                # A failed live switch must not leave a process bound to the old
                # model; discarding makes the next turn spawn cleanly.
                log.warning(
                    "provider_switch_live_failed", session_id=str(session_id), error=str(exc)
                )
                await pool.discard(str(session_id))
    return SessionRead.model_validate(session)


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete session and its messages",
)
async def delete_session(
    session_id: uuid.UUID, db: Annotated[AsyncSession, Depends(get_db)]
) -> None:
    await session_service.delete_session(db, session_id)
    await pool.discard(str(session_id))
