"""Session and message contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SessionCreate(BaseModel):
    title: str | None = None
    provider: str | None = Field(default=None, description="Defaults to the configured provider.")
    user_metadata: dict[str, Any] = Field(default_factory=dict)


class SessionUpdate(BaseModel):
    title: str | None = None
    provider: str | None = Field(
        default=None,
        description="Applies from the next turn; the running agent process is replaced.",
    )
    user_metadata: dict[str, Any] | None = None


class SessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    provider: str
    model: str
    user_metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None


class MessageCreate(BaseModel):
    content: str = Field(min_length=1)


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    intent: str | None
    citations: list[dict[str, Any]]
    provider: str | None
    model: str | None
    latency_ms: int | None
    created_at: datetime
