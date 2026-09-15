"""Artifact contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ArtifactKind = Literal["markdown", "html"]


class ArtifactCreate(BaseModel):
    kind: ArtifactKind
    title: str | None = None
    content: str = Field(min_length=1)


class ArtifactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    message_id: int | None
    kind: str
    title: str | None
    version: int
    removed_elements: list[dict[str, Any]]
    created_at: datetime


class ArtifactRender(BaseModel):
    """Includes both forms: `raw` for audit, `sanitized` for display."""

    id: uuid.UUID
    session_id: uuid.UUID
    kind: str
    title: str | None
    version: int
    sanitized_content: str
    raw_content: str
    removed_elements: list[dict[str, Any]]
