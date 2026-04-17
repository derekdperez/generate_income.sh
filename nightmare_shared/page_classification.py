#!/usr/bin/env python3
"""Page classification and false-positive reduction helpers.

This module provides a reusable classifier that can distinguish likely real pages
from soft-404/catch-all/error placeholders using multiple signals and learned
negative baselines.
"""

from __future__ import annotations

import hashlib
import random
import re
import string
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Callable, Optional
from urllib.parse import urlparse


PAGE_CLASS_EXISTS = "exists"
PAGE_CLASS_LIKELY_SOFT_404 = "likely_soft_404"
PAGE_CLASS_UNKNOWN = "unknown"
PAGE_CLASS_REDIRECT_PLACEHOLDER = "redirect_placeholder"
PAGE_CLASS_API_ERROR = "api_error"
PAGE_CLASS_BLOCKED = "blocked"


_TAG_RE = re.compile(r"<[^>]+>", flags=re.IGNORECASE)
_SCRIPT_STYLE_COMMENT_RE = re.compile(
    r"<script\b[^>]*>.*?</script>|<style\b[^>]*>.*?</style>|<!--.*?-->",
    flags=re.IGNORECASE | re.DOTALL,
)
_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title>", flags=re.IGNORECASE | re.DOTALL)
_WS_RE = re.compile(r"\s+")
_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b", re.IGNORECASE)
_ISO_TS_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[tT ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
_LONG_NUM_RE = re.compile(r"\b\d{8,}\b")
_LONG_HEX_RE = re.compile(r"\b[0-9a-f]{16,}\b", re.IGNORECASE)
_REQ_ID_RE = re.compile(r"\b(?:request[_-]?id|trace[_-]?id|correlation[_-]?id)\b\s*[:=]\s*[A-Za-z0-9._:-]+", re.IGNORECASE)
_CSRF_RE = re.compile(r"\b(?:csrf|xsrf|authenticity[_-]?token|nonce)\b\s*[:=]\s*[A-Za-z0-9._:-]{8,}", re.IGNORECASE)
_BASE64ISH_RE = re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b")

_SOFT_404_PHRASES = {
    "not found",
    "page not found",
    "doesn't exist",
    "does not exist",
    "no such",
    "could not be found",
    "requested url was not found",
}
_LOGIN_PHRASES = {
    "sign in",
    "log in",
    "login",
    "authentication required",
    "please sign in",
}
_BLOCKED_PHRASES = {
    "access denied",
    "forbidden",
    "you have been blocked",
    "temporarily blocked",
    "captcha",
}
_API_ERROR_KEYS = {
    "error",
    "errors",
    "message",
    "detail",
    "code",
    "status",
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _extract_title(text: str) -> str:
    m = _TITLE_RE.search(text or "")
    if not m:
        return ""
    return _WS_RE.sub(" ", m.group(1)).strip()


def _strip_tags(text: str) -> str:
    without_tags = _TAG_RE.sub(" ", text or "")
    return _WS_RE.sub(" ", without_tags).strip()


def _tokenize(text: str) -> set[str]:
    raw = re.findall(r"[a-zA-Z0-9_]{2,}", (text or "").lower())
    return set(raw)


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    if union <= 0:
        return 0.0
    return float(inter) / float(union)


def _simhash64(text: str) -> str:
    tokens = list(_tokenize(text))
    if not tokens:
        return "0" * 16
    vec = [0] * 64
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8", errors="replace")).digest()
        hv = int.from_bytes(digest[:8], byteorder="big", signed=False)
        for idx in range(64):
            bit = (hv >> idx) & 1
            vec[idx] += 1 if bit else -1
    out = 0
    for idx, value in enumerate(vec):
        if value >= 0:
            out |= 1 << idx
    return f"{out:016x}"


def normalize_body(
    body_text: str,
    *,
    requested_url: str = "",
    strip_script_style_comments: bool = True,
) -> str:
    text = str(body_text or "")
    if strip_script_style_comments:
        text = _SCRIPT_STYLE_COMMENT_RE.sub(" ", text)
    # Mask dynamic values that create noisy false differences.
    text = _ISO_TS_RE.sub(" <ts> ", text)
    text = _UUID_RE.sub(" <uuid> ", text)
    text = _LONG_NUM_RE.sub(" <num> ", text)
    text = _LONG_HEX_RE.sub(" <hex> ", text)
    text = _REQ_ID_RE.sub(" <request-id> ", text)
    text = _CSRF_RE.sub(" <csrf> ", text)
    text = _BASE64ISH_RE.sub(" <blob> ", text)
    req = str(requested_url or "").strip()
    if req:
        escaped = re.escape(req)
        text = re.sub(escaped, " <requested-url> ", text, flags=re.IGNORECASE)
        parsed = urlparse(req)
        path = str(parsed.path or "").strip()
        if path:
            text = re.sub(re.escape(path), " <requested-path> ", text, flags=re.IGNORECASE)
    text = _WS_RE.sub(" ", text).strip().lower()
    return text


@dataclass
class PageFingerprint:
    requested_url: str
    final_url: str
    status_code: int
    redirect_chain: list[str]
    content_type: str
    title: str
    visible_text: str
    response_length: int
    raw_hash: str
    normalized_hash: str
    fuzzy_hash: str
    normalized_body: str
    markers: list[str]

    @property
    def fingerprint_id(self) -> str:
        key = "|".join(
            [
                str(self.status_code),
                self.final_url,
                self.normalized_hash,
                self.fuzzy_hash,
                ",".join(self.redirect_chain),
            ]
        )
        return hashlib.sha256(key.encode("utf-8", errors="replace")).hexdigest()[:24]


def build_page_fingerprint(
    *,
    requested_url: str,
    status_code: int,
    final_url: str,
    redirect_chain: list[str] | None,
    content_type: str,
    response_body: bytes | bytearray | None,
) -> PageFingerprint:
    raw_bytes = bytes(response_body or b"")
    body_text = raw_bytes.decode("utf-8", errors="replace")
    title = _extract_title(body_text)
    visible_text = _strip_tags(body_text)
    normalized_body = normalize_body(body_text, requested_url=requested_url)
    compact_visible = _WS_RE.sub(" ", visible_text).strip().lower()

    markers: list[str] = []
    visible_lower = compact_visible
    for phrase in _SOFT_404_PHRASES:
        if phrase in visible_lower or phrase in title.lower():
            markers.append(f"soft_404_phrase:{phrase}")
    for phrase in _LOGIN_PHRASES:
        if phrase in visible_lower or phrase in title.lower():
            markers.append(f"login_phrase:{phrase}")
    for phrase in _BLOCKED_PHRASES:
        if phrase in visible_lower or phrase in title.lower():
            markers.append(f"blocked_phrase:{phrase}")

    raw_hash = hashlib.sha256(raw_bytes).hexdigest() if raw_bytes else ""
    normalized_hash = hashlib.sha256(normalized_body.encode("utf-8", errors="replace")).hexdigest() if normalized_body else ""
    fuzzy_hash = _simhash64(normalized_body or compact_visible)
    chain = [str(x or "").strip() for x in (redirect_chain or []) if str(x or "").strip()]

    return PageFingerprint(
        requested_url=str(requested_url or ""),
        final_url=str(final_url or requested_url or ""),
        status_code=_safe_int(status_code, 0),
        redirect_chain=chain,
        content_type=str(content_type or ""),
        title=title,
        visible_text=visible_text,
        response_length=len(raw_bytes),
        raw_hash=raw_hash,
        normalized_hash=normalized_hash,
        fuzzy_hash=fuzzy_hash,
        normalized_body=normalized_body,
        markers=sorted(set(markers)),
    )


def default_classification_config() -> dict[str, Any]:
    return {
        "soft_404_statuses": [404, 410],
        "baseline_similarity_threshold": 0.88,
        "baseline_jaccard_threshold": 0.80,
        "title_similarity_threshold": 0.90,
        "sibling_similarity_threshold": 0.92,
        "sibling_jaccard_threshold": 0.88,
        "run_rules_for_classes": [PAGE_CLASS_EXISTS, PAGE_CLASS_UNKNOWN],
        "suppress_classes_by_default": [
            PAGE_CLASS_LIKELY_SOFT_404,
            PAGE_CLASS_REDIRECT_PLACEHOLDER,
            PAGE_CLASS_API_ERROR,
            PAGE_CLASS_BLOCKED,
        ],
    }


def _looks_like_api_error(fp: PageFingerprint) -> bool:
    ctype = str(fp.content_type or "").lower()
    text = str(fp.normalized_body or "")
    if "json" not in ctype and not text.startswith("{") and not text.startswith("["):
        return False
    key_hits = 0
    for key in _API_ERROR_KEYS:
        quoted = f'"{key}"'
        if quoted in text or (key in text and text.startswith("{")):
            key_hits += 1
    if fp.status_code >= 400 and key_hits >= 1:
        return True
    return key_hits >= 2


def _similarity_signals(a: PageFingerprint, b: PageFingerprint) -> dict[str, float]:
    a_tokens = _tokenize(a.visible_text)
    b_tokens = _tokenize(b.visible_text)
    text_a = a.normalized_body or a.visible_text.lower()
    text_b = b.normalized_body or b.visible_text.lower()
    ratio = SequenceMatcher(a=text_a, b=text_b, autojunk=False).ratio()
    title_ratio = SequenceMatcher(a=a.title.lower(), b=b.title.lower(), autojunk=False).ratio()
    jacc = _jaccard(a_tokens, b_tokens)
    chain_equal = 1.0 if a.redirect_chain == b.redirect_chain and a.redirect_chain else 0.0
    final_equal = 1.0 if a.final_url == b.final_url and a.final_url else 0.0
    hash_equal = 1.0 if a.normalized_hash and a.normalized_hash == b.normalized_hash else 0.0
    len_dist = abs(int(a.response_length) - int(b.response_length))
    max_len = max(1, int(a.response_length), int(b.response_length))
    len_similarity = max(0.0, 1.0 - (float(len_dist) / float(max_len)))
    return {
        "normalized_ratio": ratio,
        "title_ratio": title_ratio,
        "jaccard": jacc,
        "redirect_chain_equal": chain_equal,
        "final_url_equal": final_equal,
        "normalized_hash_equal": hash_equal,
        "length_similarity": len_similarity,
    }


def _score_against_baseline(candidate: PageFingerprint, baseline: PageFingerprint) -> tuple[float, dict[str, float]]:
    signals = _similarity_signals(candidate, baseline)
    score = (
        signals["normalized_ratio"] * 0.35
        + signals["jaccard"] * 0.20
        + signals["normalized_hash_equal"] * 0.20
        + signals["redirect_chain_equal"] * 0.10
        + signals["final_url_equal"] * 0.10
        + signals["title_ratio"] * 0.05
    )
    return score, signals


def classify_page(
    *,
    candidate: PageFingerprint,
    baselines: list[PageFingerprint],
    sibling_variants: list[PageFingerprint] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = {**default_classification_config(), **(config or {})}
    soft_statuses = {int(x) for x in (cfg.get("soft_404_statuses") or [404, 410])}
    baseline_thresh = _safe_float(cfg.get("baseline_similarity_threshold"), 0.88)
    baseline_jacc_thresh = _safe_float(cfg.get("baseline_jaccard_threshold"), 0.80)
    title_thresh = _safe_float(cfg.get("title_similarity_threshold"), 0.90)
    sibling_thresh = _safe_float(cfg.get("sibling_similarity_threshold"), 0.92)
    sibling_jacc = _safe_float(cfg.get("sibling_jaccard_threshold"), 0.88)

    reasons: list[str] = []
    confidence = 0.55

    if candidate.status_code in soft_statuses:
        reasons.append("status_explicit_not_found")
        return {
            "classification": PAGE_CLASS_LIKELY_SOFT_404,
            "confidence_score": 0.99,
            "reasons": reasons,
            "baseline_similarity": None,
            "matched_baseline_fingerprint_id": None,
        }

    if any(m.startswith("blocked_phrase:") for m in candidate.markers):
        reasons.append("blocked_phrase_detected")
        return {
            "classification": PAGE_CLASS_BLOCKED,
            "confidence_score": 0.95,
            "reasons": reasons,
            "baseline_similarity": None,
            "matched_baseline_fingerprint_id": None,
        }

    if _looks_like_api_error(candidate):
        reasons.append("api_error_envelope_detected")
        return {
            "classification": PAGE_CLASS_API_ERROR,
            "confidence_score": 0.90,
            "reasons": reasons,
            "baseline_similarity": None,
            "matched_baseline_fingerprint_id": None,
        }

    best_score = 0.0
    best_signals: dict[str, float] = {}
    best_baseline: Optional[PageFingerprint] = None
    for baseline in baselines:
        score, signals = _score_against_baseline(candidate, baseline)
        if score > best_score:
            best_score = score
            best_signals = signals
            best_baseline = baseline

    if best_baseline is not None:
        if best_signals.get("redirect_chain_equal", 0.0) >= 1.0 and best_signals.get("final_url_equal", 0.0) >= 1.0:
            reasons.append("same_redirect_chain_as_random_miss")
            return {
                "classification": PAGE_CLASS_REDIRECT_PLACEHOLDER,
                "confidence_score": min(0.99, 0.80 + best_score * 0.2),
                "reasons": reasons,
                "baseline_similarity": round(best_score, 5),
                "matched_baseline_fingerprint_id": best_baseline.fingerprint_id,
            }
        if (
            best_score >= baseline_thresh
            or best_signals.get("normalized_hash_equal", 0.0) >= 1.0
            or (
                best_signals.get("jaccard", 0.0) >= baseline_jacc_thresh
                and best_signals.get("title_ratio", 0.0) >= title_thresh
            )
        ):
            reasons.append("matches_negative_baseline")
            return {
                "classification": PAGE_CLASS_LIKELY_SOFT_404,
                "confidence_score": min(0.99, 0.70 + best_score * 0.3),
                "reasons": reasons,
                "baseline_similarity": round(best_score, 5),
                "matched_baseline_fingerprint_id": best_baseline.fingerprint_id,
            }

    if any(m.startswith("soft_404_phrase:") for m in candidate.markers):
        reasons.append("soft_404_phrase_detected")
        return {
            "classification": PAGE_CLASS_LIKELY_SOFT_404,
            "confidence_score": 0.86,
            "reasons": reasons,
            "baseline_similarity": round(best_score, 5) if best_baseline is not None else None,
            "matched_baseline_fingerprint_id": best_baseline.fingerprint_id if best_baseline is not None else None,
        }

    for sibling in sibling_variants or []:
        signals = _similarity_signals(candidate, sibling)
        if signals["normalized_ratio"] >= sibling_thresh or (
            signals["jaccard"] >= sibling_jacc and signals["title_ratio"] >= title_thresh
        ):
            reasons.append("sibling_mutation_matches_candidate")
            return {
                "classification": PAGE_CLASS_UNKNOWN,
                "confidence_score": 0.72,
                "reasons": reasons,
                "baseline_similarity": round(best_score, 5) if best_baseline is not None else None,
                "matched_baseline_fingerprint_id": best_baseline.fingerprint_id if best_baseline is not None else None,
            }

    if candidate.status_code >= 500:
        reasons.append("server_error_status")
        return {
            "classification": PAGE_CLASS_API_ERROR,
            "confidence_score": 0.82,
            "reasons": reasons,
            "baseline_similarity": round(best_score, 5) if best_baseline is not None else None,
            "matched_baseline_fingerprint_id": best_baseline.fingerprint_id if best_baseline is not None else None,
        }

    if 400 <= candidate.status_code < 500:
        reasons.append("client_error_status")
        return {
            "classification": PAGE_CLASS_UNKNOWN,
            "confidence_score": 0.68,
            "reasons": reasons,
            "baseline_similarity": round(best_score, 5) if best_baseline is not None else None,
            "matched_baseline_fingerprint_id": best_baseline.fingerprint_id if best_baseline is not None else None,
        }

    reasons.append("unique_content_vs_baseline")
    confidence += min(0.35, max(0.0, 1.0 - best_score) * 0.35)
    return {
        "classification": PAGE_CLASS_EXISTS,
        "confidence_score": round(max(0.0, min(1.0, confidence)), 4),
        "reasons": reasons,
        "baseline_similarity": round(best_score, 5) if best_baseline is not None else None,
        "matched_baseline_fingerprint_id": best_baseline.fingerprint_id if best_baseline is not None else None,
    }


def generate_random_missing_urls(*, base_url: str, count: int = 5) -> list[str]:
    parsed = urlparse(str(base_url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return []
    prefix = parsed.path or "/"
    if not prefix.startswith("/"):
        prefix = "/" + prefix
    if prefix != "/" and not prefix.endswith("/"):
        prefix = prefix + "/"
    out: list[str] = []
    for _idx in range(max(1, int(count or 5))):
        token = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(20))
        out.append(f"{parsed.scheme}://{parsed.netloc}{prefix}__nightmare_missing__{token}")
    return out


def learn_negative_baselines(
    *,
    host: str,
    prefixes: list[str],
    fetch_fingerprint: Callable[[str], Optional[PageFingerprint]],
    samples_per_prefix: int = 4,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_fp: set[str] = set()
    host_text = str(host or "").strip().lower()
    prefixes_norm = [str(p or "/").strip() or "/" for p in prefixes] or ["/"]
    for prefix in prefixes_norm:
        if not prefix.startswith("/"):
            prefix = "/" + prefix
        fake_root = f"https://{host_text}{prefix}"
        sample_urls = generate_random_missing_urls(base_url=fake_root, count=samples_per_prefix)
        fps: list[PageFingerprint] = []
        for url in sample_urls:
            fp = fetch_fingerprint(url)
            if fp is None:
                continue
            fps.append(fp)
        if not fps:
            continue
        # Choose representative by most frequent normalized hash, fallback first.
        by_hash: dict[str, list[PageFingerprint]] = {}
        for fp in fps:
            by_hash.setdefault(fp.normalized_hash, []).append(fp)
        representative = sorted(by_hash.values(), key=lambda group: len(group), reverse=True)[0][0]
        if representative.fingerprint_id in seen_fp:
            continue
        seen_fp.add(representative.fingerprint_id)
        out.append(
            {
                "baseline_id": hashlib.sha256(
                    f"{host_text}|{prefix}|{representative.fingerprint_id}".encode("utf-8", errors="replace")
                ).hexdigest()[:24],
                "host": host_text,
                "scope_prefix": prefix,
                "fingerprint_id": representative.fingerprint_id,
                "baseline_type": "negative_random_miss",
                "sample_urls": sample_urls,
                "representative_status_code": representative.status_code,
                "representative_final_url": representative.final_url,
                "representative_title": representative.title,
                "normalized_hash": representative.normalized_hash,
                "fuzzy_hash": representative.fuzzy_hash,
                "markers": list(representative.markers),
                "created_at": _iso_now(),
                "updated_at": _iso_now(),
            }
        )
    return out

