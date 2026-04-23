from __future__ import annotations

from typing import Any


def validate_workflow_definition(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not str(payload.get("workflow_key") or payload.get("name") or "").strip():
        errors.append("workflow_key or name is required")
    steps = payload.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append("at least one step is required")
    seen: set[str] = set()
    for idx, step in enumerate(steps or [], start=1):
        key = str(step.get("step_key") or f"step-{idx}")
        if key in seen:
            errors.append(f"duplicate step_key: {key}")
        seen.add(key)
        if not str(step.get("plugin_key") or step.get("plugin_name") or "").strip():
            errors.append(f"step {idx} is missing plugin_key")
    return errors
