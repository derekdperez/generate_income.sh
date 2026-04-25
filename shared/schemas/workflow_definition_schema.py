"""Pydantic schema for immutable workflow definitions."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .workflow_step_schema import WorkflowStepSchema


class WorkflowDefinitionSchema(BaseModel):
    """Validate a complete workflow definition before creating an immutable run."""

    model_config = ConfigDict(extra="forbid")

    workflow_key: str = Field(min_length=1, max_length=200)
    version: int = Field(default=1, ge=1)
    name: str = Field(min_length=1, max_length=300)
    description: str = ""
    status: str = Field(default="draft", pattern="^(draft|published|archived)$")
    trigger_mode: str = Field(default="manual", max_length=80)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    ui_schema: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    steps: list[WorkflowStepSchema] = Field(default_factory=list)

    @field_validator("workflow_key")
    @classmethod
    def normalize_workflow_key(cls, value: str) -> str:
        """Normalize workflow keys so API, DB, and workers use the same identity."""
        return str(value or "").strip().lower().replace("_", "-")
