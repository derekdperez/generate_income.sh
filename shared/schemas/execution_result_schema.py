"""Pydantic schema for plugin execution results."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ExecutionResultSchema(BaseModel):
    """Validate a worker/plugin result before task completion is persisted."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["completed", "failed"]
    exit_code: int = 0
    error: str = ""
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    progress: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
