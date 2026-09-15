"""
Message transcript and the SSE chat turn.
"""

from __future__ import annotations

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal, get_db
from app.schemas.events import SSE_MEDIA_TYPE
from app.schemas.session import MessageCreate, MessageRead
from app.services import chat_service, session_service

log = structlog.get_logger("app.messages")
router = APIRouter(prefix="/sessions/{session_id}/messages", tags=["messages"])


@router.get("", response_model=list[MessageRead], summary="Full transcript")
async def list_messages(
    session_id: uuid.UUID, db: Annotated[AsyncSession, Depends(get_db)]
) -> list[MessageRead]:
    await session_service.get_session(db, session_id)
    messages = await session_service.list_messages(db, session_id)
    return [MessageRead.model_validate(message) for message in messages]


@router.post("", summary="Send a turn and stream the answer (SSE)")
async def post_message(session_id: uuid.UUID, payload: MessageCreate) -> StreamingResponse:
    # Check the session exists before the stream starts, so an unknown id is a
    # clean 404 rather than an error frame buried halfway through a 200 response.
    async with AsyncSessionLocal() as db:
        await session_service.get_session(db, session_id)

    log.info("turn_requested", session_id=str(session_id), chars=len(payload.content))

    return StreamingResponse(
        chat_service.stream_turn(session_id, payload.content),
        media_type=SSE_MEDIA_TYPE,
        headers={
            "Cache-Control": "no-cache",
            # Defeats proxy buffering, which would otherwise hold the whole
            # stream and destroy the point of streaming.
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
