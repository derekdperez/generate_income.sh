from __future__ import annotations

from pathlib import Path
from typing import Any


def evaluate_preconditions(preconditions: dict[str, Any] | None) -> dict[str, Any]:
    data = preconditions or {}
    checks = data.get("all") if isinstance(data.get("all"), list) else []
    for check in checks:
        if not isinstance(check, dict):
            continue
        if check.get("type") == "file_exists":
            path = str(check.get("path") or "").strip()
            if path and not Path(path).exists():
                return {"ready": False, "blocked_reason": f"waiting for file {path}"}
    return {"ready": True, "blocked_reason": ""}
