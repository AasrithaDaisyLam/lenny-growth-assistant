"""
Async database engine and schema bootstrap.

Schema creation uses `create_all` rather than Alembic. That is a deliberate
take-home trade-off: no client is consuming incremental schema changes, so a
migration chain would be ceremony. `architecture.md` names Alembic as the first
thing to add for a real deployment.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import structlog
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.db.base import Base

log = structlog.get_logger("app.db")

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session that always closes."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except SQLAlchemyError:
            await session.rollback()
            raise


async def init_db() -> None:
    """
    Ensure the pgvector extension exists, then create any missing tables.

    The extension must be created before any table with a vector column, which is
    why it cannot live in the model metadata alone.
    """
    from app import models  # noqa: F401  (import registers all mappers)

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)

    log.info("schema_ready", tables=len(Base.metadata.tables))


async def ping_db() -> tuple[bool, str | None]:
    """Readiness probe. Returns (ok, error_message)."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:  # noqa: BLE001 -- readiness must never raise
        return False, f"{type(exc).__name__}: {exc}"


async def dispose_engine() -> None:
    await engine.dispose()
