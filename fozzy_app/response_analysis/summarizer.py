from __future__ import annotations

from .schemas import DiffResult, Finding


def build_summary(*, diff: DiffResult, findings: list[Finding]) -> str:
    if not findings and diff.noisy_only:
        return "No meaningful deviation from baseline (volatile/noise-only changes)."
    parts: list[str] = []
    if diff.status_changed:
        parts.append(f"Status changed from {diff.status_from} to {diff.status_to}")
    ordered = sorted(findings, key=lambda item: (int(item.score_contribution), float(item.confidence)), reverse=True)
    for finding in ordered[:4]:
        text = str(finding.title or "").strip()
        if text:
            if text[0].islower():
                text = text[0].upper() + text[1:]
            parts.append(text)
    if diff.redirect_changed:
        parts.append("Redirect behavior changed")
    if diff.auth_behavior_changed and diff.auth_change_reason:
        parts.append(f"Auth/session behavior changed ({diff.auth_change_reason})")
    if not parts:
        return "No significant differences detected."
    return "; ".join(dict.fromkeys(parts)) + "."

