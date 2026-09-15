"""Provider catalogue contracts."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProviderRead(BaseModel):
    name: str
    available: bool
    reason: str | None = Field(
        default=None, description="Why this provider cannot be used, when it cannot."
    )
    models: list[str] = Field(default_factory=list)
    active: bool = Field(description="Whether this is the configured provider.")


class ProvidersResponse(BaseModel):
    active: str
    fallback_chain: list[str]
    providers: list[ProviderRead]
