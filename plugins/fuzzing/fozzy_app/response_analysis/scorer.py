from __future__ import annotations

from .schemas import Finding


_SEVERITY_BONUS = {
    "critical": 8,
    "high": 5,
    "medium": 2,
    "low": 0,
}


def score_findings(findings: list[Finding], *, similarity: float, noisy_only: bool) -> int:
    score = 0
    for finding in findings:
        score += int(finding.score_contribution)
        score += int(_SEVERITY_BONUS.get(str(finding.severity or "").lower(), 0))
    if similarity < 0.5:
        score += 6
    elif similarity < 0.75:
        score += 3
    if noisy_only:
        score = min(score, 4)
    return max(0, min(100, int(score)))


def score_to_status(score: int, findings: list[Finding]) -> str:
    if score >= 45:
        return "high_signal"
    if score >= 20:
        return "interesting"
    if findings:
        return "low_signal"
    return "normal"

