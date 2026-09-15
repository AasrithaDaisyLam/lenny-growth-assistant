"""Health and readiness endpoints."""

from __future__ import annotations

import shutil

import structlog
from fastapi import APIRouter

from app.config import settings
from app.db.session import ping_db
from app.services.ollama import ollama

log = structlog.get_logger("app.health")
router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness")
async def health() -> dict:
    """Touches no dependency: this must answer even when everything else is down."""
    return {
        "status": "ok",
        "service": "lenny-growth-assistant",
        "environment": settings.environment,
    }


@router.get("/health/ready", summary="Readiness: per-dependency status")
async def ready() -> dict:
    """
    Report each dependency independently, so a failure names the culprit rather
    than returning a single opaque red light.
    """
    db_ok, db_error = await ping_db()

    ollama_version = await ollama.version()
    ollama_ok = ollama_version is not None
    embed_ok = False
    chat_model_ok = False
    if ollama_ok:
        models = await ollama.list_models()
        base = settings.ollama_embed_model.split(":")[0]
        embed_ok = any(m == settings.ollama_embed_model or m.split(":")[0] == base for m in models)
        chat_base = settings.ollama_model.split(":")[0]
        chat_model_ok = any(
            m == settings.ollama_model or m.split(":")[0] == chat_base for m in models
        )

    pi_ok = shutil.which(settings.pi_bin) is not None

    components = {
        "database": {"ok": db_ok, "error": db_error},
        "ollama": {
            "ok": ollama_ok,
            "version": ollama_version,
            "url": settings.ollama_base_url,
            "error": None if ollama_ok else "unreachable",
        },
        "embed_model": {
            "ok": embed_ok,
            "name": settings.ollama_embed_model,
            "error": None if embed_ok else f"{settings.ollama_embed_model} not pulled",
        },
        "chat_model": {
            "ok": chat_model_ok,
            "name": settings.ollama_model,
            "error": None if chat_model_ok else f"{settings.ollama_model} not pulled",
        },
        "agent_binary": {
            "ok": pi_ok,
            "name": settings.pi_bin,
            "error": None if pi_ok else "pi not found on PATH",
        },
        "active_provider": {
            "ok": settings.provider_unavailable_reason(settings.llm_provider) is None,
            "provider": settings.llm_provider,
            "model": settings.model_for(settings.llm_provider),
            "error": settings.provider_unavailable_reason(settings.llm_provider),
        },
    }

    # Chat model and agent binary are required for a turn; the DB is required for
    # sessions. Embeddings are required for retrieval but a degraded run is still
    # useful, so they do not gate readiness.
    required = ("database", "ollama", "chat_model", "agent_binary", "active_provider")
    overall_ok = all(components[c]["ok"] for c in required)

    return {
        "status": "ok" if overall_ok else "degraded",
        "components": components,
        "config": {
            "llm_provider": settings.llm_provider,
            "fallback_chain": settings.fallback_chain,
            "rag_top_k": settings.rag_top_k,
            "rag_min_score": settings.rag_min_score,
            "embedding_dim": settings.embedding_dim,
        },
    }
