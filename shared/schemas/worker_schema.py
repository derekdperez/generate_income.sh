"""Pydantic schema for worker capability and liveness records."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


class WorkerSchema(BaseModel):
    """Validate worker presence, capabilities, and lease heartbeat metadata."""

    model_config = ConfigDict(extra="forbid")

    worker_id: str = Field(min_length=1, max_length=200)
    status: str = Field(default="online", max_length=80)
    capabilities: list[str] = Field(default_factory=list)
    max_parallel_tasks: int = Field(default=1, ge=1)
    last_seen_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
