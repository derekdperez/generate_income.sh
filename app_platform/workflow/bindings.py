from __future__ import annotations

from typing import Any


def resolve_bindings(bindings: dict[str, Any] | None, *, workflow_input: dict[str, Any]) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for key, spec in (bindings or {}).items():
        if not isinstance(spec, dict):
            resolved[key] = spec
            continue
        source = spec.get("source")
        if source == "literal":
            resolved[key] = spec.get("value")
        elif source == "workflow_input":
            path = str(spec.get("path") or "").lstrip("$.")
            resolved[key] = workflow_input.get(path)
    return resolved
