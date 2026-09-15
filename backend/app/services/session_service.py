"""Session and message persistence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.errors import NotFoundError, ProviderUnavailableError
from app.models.conversation import ChatMessage, ChatSession

log = structlog.get_logger("app.session")


async def create_session(
    db: AsyncSession,
    *,
    title: str | None = None,
    provider: str | None = None,
    user_metadata: dict[str, Any] | None = None,
) -> ChatSession:
    chosen = provider or settings.llm_provider
    session = ChatSession(
        title=title,
        provider=chosen,
        model=settings.model_for(chosen),
        user_metadata=user_metadata or {},
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    log.info("session_created", session_id=str(session.id), provider=chosen, model=session.model)
    return session


async def list_sessions(db: AsyncSession, limit: int = 50) -> list[ChatSession]:
    statement = (
        select(ChatSession)
        .order_by(func.coalesce(ChatSession.last_message_at, ChatSession.created_at).desc())
        .limit(limit)
    )
    return list((await db.execute(statement)).scalars().all())


async def get_session(db: AsyncSession, session_id: uuid.UUID) -> ChatSession:
    session = await db.get(ChatSession, session_id)
    if session is None:
        raise NotFoundError(f"Session {session_id} not found.")
    return session


async def update_session(
    db: AsyncSession,
    session_id: uuid.UUID,
    *,
    title: str | None = None,
    provider: str | None = None,
    user_metadata: dict[str, Any] | None = None,
) -> ChatSession:
    session = await get_session(db, session_id)

    if title is not None:
        session.title = title
    if user_metadata is not None:
        session.user_metadata = user_metadata
    if provider is not None and provider != session.provider:
        reason = settings.provider_unavailable_reason(provider)
        if reason:
            # 503, not 404: the provider exists and is configured, it just cannot
            # be used. Reporting "not found" would send the caller looking for a
            # typo instead of at the missing credential.
            raise ProviderUnavailableError(f"Provider '{provider}' is unavailable: {reason}")
        # Session-scoped on purpose: a thread with two models is undebuggable, so
        # the switch applies to the whole session and the next turn picks it up.
        session.provider = provider
        session.model = settings.model_for(provider)
        log.info("provider_switched", session_id=str(session_id), provider=provider)

    await db.commit()
    await db.refresh(session)
    return session


async def delete_session(db: AsyncSession, session_id: uuid.UUID) -> None:
    session = await get_session(db, session_id)
    await db.delete(session)
    await db.commit()
    log.info("session_deleted", session_id=str(session_id))


async def list_messages(db: AsyncSession, session_id: uuid.UUID) -> list[ChatMessage]:
    statement = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at, ChatMessage.id)
    )
    return list((await db.execute(statement)).scalars().all())


async def recent_turns(
    db: AsyncSession, session_id: uuid.UUID, limit: int = 6
) -> list[tuple[str, str]]:
    """
    The last few (role, content) pairs, oldest first.

    Used only to rehydrate an evicted Pi process. Excludes system rows because
    they are not part of the conversational thread.
    """
    statement = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id, ChatMessage.role != "system")
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .limit(limit)
    )
    rows = list((await db.execute(statement)).scalars().all())
    return [(row.role, row.content) for row in reversed(rows)]


async def add_message(
    db: AsyncSession,
    session_id: uuid.UUID,
    role: str,
    content: str,
    *,
    intent: str | None = None,
    citations: list[dict[str, Any]] | None = None,
    retrieval_trace: dict[str, Any] | None = None,
    provider: str | None = None,
    model: str | None = None,
    token_usage: dict[str, Any] | None = None,
    latency_ms: int | None = None,
) -> ChatMessage:
    message = ChatMessage(
        session_id=session_id,
        role=role,
        content=content,
        intent=intent,
        citations=citations or [],
        retrieval_trace=retrieval_trace or {},
        provider=provider,
        model=model,
        token_usage=token_usage or {},
        latency_ms=latency_ms,
    )
    db.add(message)

    session = await db.get(ChatSession, session_id)
    if session is not None:
        session.last_message_at = datetime.now(UTC)
        # Name a brand-new session from its first question, so the sidebar is not
        # a list of identical "Untitled" rows.
        if session.title is None and role == "user":
            session.title = content.strip()[:80]

    await db.commit()
    await db.refresh(message)
    return message
