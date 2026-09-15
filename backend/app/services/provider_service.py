"""
Provider availability and selection.

Two rules shape this module:

1. **Unavailable providers are reported, not hidden.** A missing API key or an
   unreachable Ollama is a fact the user needs, with its reason. A provider that
   silently disappears from a list looks like a product limitation rather than a
   configuration one.
2. **Selection is deterministic and ordered.** The preferred provider is tried
   first, then `PROVIDER_FALLBACK_CHAIN`, and the choice is a pure function of the
   chain plus an availability map (`candidate_chain`, `pick_available`). That
   makes the fallback logic unit-testable without standing up a provider.

Availability is probed live for Ollama (it is a process that can simply be down)
and by configuration for Anthropic (a missing key needs no network call to detect).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from app.config import settings
from app.services.ollama import ollama

log = structlog.get_logger("app.providers")

KNOWN_PROVIDERS = ("ollama", "anthropic")


@dataclass(slots=True)
class ProviderStatus:
    name: str
    available: bool
    reason: str | None
    models: list[str] = field(default_factory=list)
    active: bool = False


@dataclass(slots=True)
class Resolution:
    """The outcome of choosing a provider for a turn."""

    provider: str | None
    model: str | None
    attempted: list[dict] = field(default_factory=list)
    fell_back: bool = False

    @property
    def ok(self) -> bool:
        return self.provider is not None

    def reason_summary(self) -> str:
        return "; ".join(f"{item['provider']}: {item['reason']}" for item in self.attempted)


def candidate_chain(preferred: str, fallback_chain: list[str]) -> list[str]:
    """
    Ordered, de-duplicated providers to try: preferred first, then the chain.

    Duplicates are dropped so a chain that repeats the preferred provider (the
    shipped default is `ollama` for both) does not probe it twice.
    """
    ordered: list[str] = []
    for name in [preferred, *fallback_chain]:
        if name and name not in ordered:
            ordered.append(name)
    return ordered


def pick_available(candidates: list[str], unavailable: dict[str, str]) -> str | None:
    """First candidate that is not in the unavailable map."""
    for name in candidates:
        if name not in unavailable:
            return name
    return None


async def check_provider(name: str) -> tuple[bool, str | None, list[str]]:
    """Return (available, reason_if_not, models)."""
    if name == "anthropic":
        reason = settings.provider_unavailable_reason("anthropic")
        return (reason is None, reason, [settings.anthropic_model])

    if name == "ollama":
        version = await ollama.version()
        if version is None:
            return (False, f"Ollama unreachable at {settings.ollama_base_url}", [])
        models = await ollama.list_models()
        if not models:
            return (False, "Ollama reported no models", [])
        return (True, None, models)

    return (False, f"unknown provider '{name}'", [])


async def describe_providers() -> list[ProviderStatus]:
    """Every known provider, with its reason when unusable. The active one is flagged."""
    names = list(KNOWN_PROVIDERS)
    for name in settings.fallback_chain:
        if name not in names:
            names.append(name)

    statuses: list[ProviderStatus] = []
    for name in names:
        available, reason, models = await check_provider(name)
        statuses.append(
            ProviderStatus(
                name=name,
                available=available,
                reason=reason,
                models=models,
                active=name == settings.llm_provider,
            )
        )
    return statuses


async def resolve_provider(preferred: str) -> Resolution:
    """
    Choose the provider to answer with, honouring the fallback chain.

    Returns a Resolution whose `provider` is None only when *every* candidate is
    unavailable; the caller then degrades with the reasons recorded in
    `attempted` rather than raising an opaque failure.
    """
    candidates = candidate_chain(preferred, settings.fallback_chain)
    unavailable: dict[str, str] = {}
    attempted: list[dict] = []

    for name in candidates:
        available, reason, _ = await check_provider(name)
        if available:
            if name != preferred:
                log.warning("provider_fallback", preferred=preferred, using=name)
            return Resolution(
                provider=name,
                model=settings.model_for(name),
                attempted=attempted,
                fell_back=name != preferred,
            )
        unavailable[name] = reason or "unavailable"
        attempted.append({"provider": name, "reason": reason})

    log.error("no_provider_available", preferred=preferred, attempted=attempted)
    return Resolution(provider=None, model=None, attempted=attempted, fell_back=False)
