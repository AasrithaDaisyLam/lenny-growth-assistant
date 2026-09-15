"""
The Lenny Growth Assistant -- FastAPI application.

Startup is deliberately tolerant: if the database is unreachable the app still
boots and reports the failure through /api/health/ready, rather than crash-looping.
That is what makes the resilience requirement demonstrable instead of theoretical.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.agent.process_pool import pool
from app.api.routes import artifacts, health, messages, providers, search, sessions, tools
from app.config import settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.db.session import dispose_engine, init_db

configure_logging()
log = structlog.get_logger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(
        "startup",
        environment=settings.environment,
        provider=settings.llm_provider,
        model=settings.model_for(settings.llm_provider),
        embedding_dim=settings.embedding_dim,
    )
    try:
        await init_db()
    except Exception as exc:  # noqa: BLE001
        # Boot anyway. Readiness will report the failure and the reason.
        log.error("schema_init_failed", error=f"{type(exc).__name__}: {exc}")
    yield
    # Pi processes are children of this app; leaving them running would leak a
    # node process per open session across every reload.
    await pool.shutdown()
    await dispose_engine()
    log.info("shutdown")


app = FastAPI(
    title="The Lenny Growth Assistant",
    description=(
        "Grounded answers over Lenny's Podcast transcripts, with citations and rendered artifacts."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)

register_exception_handlers(app)

app.include_router(health.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(providers.router, prefix="/api")
app.include_router(sessions.router, prefix="/api")
app.include_router(messages.router, prefix="/api")
app.include_router(artifacts.router, prefix="/api")
# Curl-back surface for the Pi extension's tools. Shared-secret gated and kept
# outside /api so it is not part of the public contract.
app.include_router(tools.router, prefix="/internal")


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")
