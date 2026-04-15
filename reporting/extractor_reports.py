#!/usr/bin/env python3
"""Extractor report rendering helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from nightmare_shared.templating import render_template


def build_javascript_extractor_matches_report_html(domain_label: str, rows: list[dict[str, object]]) -> str:
    title = f"JavaScript extractor — {domain_label}"
    return render_template(
        "javascript_extractor_matches.html.j2",
        title=title,
        generated_at=datetime.now(timezone.utc).isoformat(),
        rows_json=json.dumps(rows, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c"),
    )
