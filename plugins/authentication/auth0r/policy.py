from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse


@dataclass
class ReplayPolicy:
    read_only_mode: bool = False
    verify_state_changes: bool = True
    allowed_methods: list[str] = field(default_factory=list)
    denied_methods: list[str] = field(default_factory=list)
    allow_path_patterns: list[str] = field(default_factory=list)
    deny_path_patterns: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "ReplayPolicy":
        data = value if isinstance(value, dict) else {}
        return cls(
            read_only_mode=bool(data.get("read_only_mode", False)),
            verify_state_changes=bool(data.get("verify_state_changes", True)),
            allowed_methods=[str(v).upper() for v in data.get("allowed_methods", []) if str(v).strip()],
            denied_methods=[str(v).upper() for v in data.get("denied_methods", []) if str(v).strip()],
            allow_path_patterns=[str(v) for v in data.get("allow_path_patterns", []) if str(v).strip()],
            deny_path_patterns=[str(v) for v in data.get("deny_path_patterns", []) if str(v).strip()],
        )


def _path(url: str) -> str:
    try:
        return urlparse(str(url or "")).path or "/"
    except Exception:
        return "/"


def _matches_any(path: str, patterns: list[str]) -> bool:
    for pattern in patterns or []:
        try:
            if re.search(pattern, path, flags=re.I):
                return True
        except re.error:
            if pattern in path:
                return True
    return False


def evaluate_action(policy: ReplayPolicy, action: dict[str, Any]) -> tuple[bool, str]:
    method = str(action.get("method", "GET") or "GET").upper()
    path = _path(str(action.get("url", "") or ""))
    state_changing = bool(action.get("likely_state_changing"))

    if policy.read_only_mode and state_changing:
        return False, "read_only_mode"
    if policy.allowed_methods and method not in set(policy.allowed_methods):
        return False, "method_not_in_allowlist"
    if method in set(policy.denied_methods):
        return False, "method_in_denylist"
    if policy.allow_path_patterns and not _matches_any(path, policy.allow_path_patterns):
        return False, "path_not_in_allowlist"
    if policy.deny_path_patterns and _matches_any(path, policy.deny_path_patterns):
        return False, "path_in_denylist"
    return True, "allowed"


def should_verify_side_effects(policy: ReplayPolicy, action: dict[str, Any]) -> bool:
    return bool(policy.verify_state_changes) and bool(action.get("likely_state_changing"))
