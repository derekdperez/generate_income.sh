"""Pydantic schema for artifact metadata rows."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


class ArtifactSchema(BaseModel):
    """Validate artifact metadata before object storage writes are indexed."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(min_length=1, max_length=300)
    artifact_type: str = Field(min_length=1, max_length=200)
    root_domain: str = Field(min_length=1, max_length=253)
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0)
    storage_backend: str = Field(default="filesystem", max_length=80)
    storage_uri: str = Field(min_length=1)
    media_type: str = Field(default="application/octet-stream", max_length=200)
    schema_version: int = Field(default=1, ge=1)
    created_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
