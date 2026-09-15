"""
Provider catalogue.

Surfaces what the configured provider is, what else is reachable, and -- for
anything unreachable -- the reason. The unavailable entries are the point: a
missing `ANTHROPIC_API_KEY` should read as a disabled control with an explanation,
not as an absent feature.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter

from app.config import settings
from app.schemas.provider import ProviderRead, ProvidersResponse
from app.services.provider_service import describe_providers

log = structlog.get_logger("app.providers")
router = APIRouter(tags=["providers"])


@router.get(
    "/providers", response_model=ProvidersResponse, summary="Available models and providers"
)
async def list_providers() -> ProvidersResponse:
    statuses = await describe_providers()
    log.info(
        "providers_listed",
        active=settings.llm_provider,
        available=[s.name for s in statuses if s.available],
        unavailable=[s.name for s in statuses if not s.available],
    )
    return ProvidersResponse(
        active=settings.llm_provider,
        fallback_chain=settings.fallback_chain,
        providers=[
            ProviderRead(
                name=status.name,
                available=status.available,
                reason=status.reason,
                models=status.models,
                active=status.active,
            )
            for status in statuses
        ],
    )
