
from __future__ import annotations

from difflib import SequenceMatcher
import json


def body_similarity(a: str, b: str) -> float:
    return float(SequenceMatcher(None, a or "", b or "").ratio())


def compare_responses(baseline, candidate, *, authenticated_hits: list[str], denial_hits: list[str]) -> dict:
    baseline_text = baseline.text if baseline is not None else ""
    candidate_text = candidate.text if candidate is not None else ""
    similarity = body_similarity(baseline_text, candidate_text)
    suspicious = (
        candidate is not None
        and baseline is not None
        and baseline.status_code == candidate.status_code
        and similarity >= 0.90
        and not denial_hits
    )
    return {
        "baseline_status_code": getattr(baseline, "status_code", None),
        "candidate_status_code": getattr(candidate, "status_code", None),
        "body_similarity": similarity,
        "baseline_length": len(baseline_text),
        "candidate_length": len(candidate_text),
        "authenticated_markers_matched": list(authenticated_hits or []),
        "denial_markers_matched": list(denial_hits or []),
        "suspicious": suspicious,
    }
