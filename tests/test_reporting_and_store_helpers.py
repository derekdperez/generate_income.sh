from __future__ import annotations

import re

import pytest

from reporting.extractor_reports import build_javascript_extractor_matches_report_html
from reporting.server_pages import render_dashboard_html, render_workers_html
from server_app.store import _get_root_domain, _make_target_entry_id, _normalize_target_url


def test_render_dashboard_html_contains_expected_heading():
    html = render_dashboard_html()
    assert "Nightmare Live Dashboard" in html


def test_render_workers_html_contains_expected_heading():
    html = render_workers_html()
    assert "Worker Control Center" in html


def test_extractor_report_html_escapes_script_content():
    html = build_javascript_extractor_matches_report_html(
        "example.com",
        [
            {
                "rule_name": "r1",
                "regex": "<script>",
                "match_text": "<script>alert(1)</script>",
                "score": 5,
                "url": "https://example.com/a.js",
                "source_file": "a.js",
            }
        ],
    )
    assert "JavaScript extractor" in html
    assert "\\u003cscript>" in html


def test_get_root_domain_extracts_last_two_labels():
    assert _get_root_domain("a.b.example.com") == "example.com"
    assert _get_root_domain("localhost") == "localhost"
    assert _get_root_domain("") == ""


def test_normalize_target_url_accepts_host_and_strips_fragment():
    normalized, root_domain = _normalize_target_url("Example.COM/path#frag")
    assert normalized == "https://example.com/path"
    assert root_domain == "example.com"


def test_normalize_target_url_rejects_invalid_target():
    with pytest.raises(ValueError):
        _normalize_target_url("://bad target")


def test_make_target_entry_id_is_stable_and_short():
    a = _make_target_entry_id(12, "https://example.com")
    b = _make_target_entry_id(12, "https://example.com")
    c = _make_target_entry_id(13, "https://example.com")
    assert a == b
    assert a != c
    assert re.fullmatch(r"[0-9a-f]{16}", a)
