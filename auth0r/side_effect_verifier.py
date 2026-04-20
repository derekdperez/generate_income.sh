from __future__ import annotations

import json
import re
from typing import Any


STATUS_KEYS = {"status", "state", "enabled", "disabled", "active", "archived"}
COUNT_KEYS = {"count", "total", "size", "items", "results"}
TIME_PATTERNS = [re.compile(r"updated[_ -]?at", re.I), re.compile(r"timestamp", re.I)]


def _json_load(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None


def _flatten(value: Any, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = f"{prefix}.{key}" if prefix else str(key)
            out.update(_flatten(nested, key_text))
    elif isinstance(value, list):
        for idx, item in enumerate(value[:10]):
            key_text = f"{prefix}[{idx}]"
            out.update(_flatten(item, key_text))
    else:
        out[prefix or "value"] = str(value)
    return out


def verify_side_effect(
    action: dict[str, Any],
    baseline,
    candidate,
    *,
    comparison: dict[str, Any],
) -> dict[str, Any]:
    state_changing = bool(action.get("likely_state_changing"))
    if not state_changing:
        return {"checked": False, "reason": "not_state_changing"}

    baseline_text = baseline.text if baseline is not None else ""
    candidate_text = candidate.text if candidate is not None else ""
    baseline_json = _json_load(baseline_text)
    candidate_json = _json_load(candidate_text)

    baseline_flat = _flatten(baseline_json) if baseline_json is not None else {}
    candidate_flat = _flatten(candidate_json) if candidate_json is not None else {}
    overlapping_changed_indicators: list[str] = []

    for key, value in baseline_flat.items():
        lowered = key.lower()
        if lowered.split(".")[-1] in STATUS_KEYS or lowered.split(".")[-1] in COUNT_KEYS or any(p.search(lowered) for p in TIME_PATTERNS):
            cand_val = candidate_flat.get(key)
            if cand_val is not None and cand_val == value:
                overlapping_changed_indicators.append(key)

    strong_equivalence = bool(comparison.get("body_similarity", 0.0) >= 0.9 or comparison.get("json_shape_match"))
    suspicious = strong_equivalence and (bool(overlapping_changed_indicators) or bool(comparison.get("sensitive_fields_preserved")))

    return {
        "checked": True,
        "state_changing": True,
        "strong_equivalence": strong_equivalence,
        "overlapping_changed_indicators": overlapping_changed_indicators[:20],
        "suspicious_side_effect_equivalence": suspicious,
    }
