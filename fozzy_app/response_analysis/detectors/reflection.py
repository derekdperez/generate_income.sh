from __future__ import annotations

import html
import re
from urllib.parse import quote

from ..pattern_library import HTML_ATTR_REFLECTION_RE_TEMPLATE, HTML_SCRIPT_RE
from ..schemas import Finding
from .base import DetectorContext


class ReflectionDetector:
    detector_id = "reflection_detector"

    @staticmethod
    def _contexts(body: str, token: str) -> list[str]:
        contexts: set[str] = set()
        if not token:
            return []
        low_body = body.lower()
        if token.lower() in low_body:
            contexts.add("plain_text")
        enc = html.escape(token, quote=True)
        if enc and enc != token and enc.lower() in low_body:
            contexts.add("html_encoded")
        q = quote(token, safe="")
        if q and q.lower() in low_body:
            contexts.add("url_encoded")
        esc = token.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'")
        if esc and esc.lower() in low_body and esc != token:
            contexts.add("escaped")
        for script_body in HTML_SCRIPT_RE.findall(body):
            if token.lower() in script_body.lower() or enc.lower() in script_body.lower():
                contexts.add("script")
                break
        attr_re = re.compile(HTML_ATTR_REFLECTION_RE_TEMPLATE.format(marker=re.escape(token)), re.IGNORECASE)
        if attr_re.search(body):
            contexts.add("html_attribute")
        if "<" in body and ">" in body and "plain_text" in contexts and "script" not in contexts and "html_attribute" not in contexts:
            contexts.add("html_text")
        return sorted(contexts)

    def detect(self, ctx: DetectorContext) -> list[Finding]:
        body = ctx.fuzz_body_raw or ""
        if not body:
            return []
        header_values = " ".join(
            ", ".join(values) for values in (ctx.fuzz_headers_raw or {}).values() if isinstance(values, list)
        ).lower()
        matches: list[dict[str, str]] = []
        for marker in ctx.marker_candidates:
            token = str(marker or "")
            if not token:
                continue
            contexts = self._contexts(body, token)
            in_headers = token.lower() in header_values
            if in_headers:
                contexts = sorted(set(contexts + ["header"]))
            if not contexts:
                continue
            matches.append({"marker": token, "contexts": ",".join(contexts)})
        if not matches:
            return []
        severity = "high"
        score = 18
        context_blob = ",".join(item.get("contexts", "") for item in matches)
        if "script" in context_blob or "html_attribute" in context_blob:
            severity = "critical"
            score = 28
        return [
            Finding(
                id="reflection_detected",
                title="Fuzz marker reflected in response",
                category="reflection",
                severity=severity,
                score_contribution=score,
                confidence=0.95,
                evidence={"matches": matches},
                description="Injected marker token was reflected in response body/headers.",
            )
        ]

