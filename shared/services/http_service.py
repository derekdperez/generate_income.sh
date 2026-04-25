"""Controlled HTTP client operations exposed to workflow plugins."""

from __future__ import annotations

from typing import Any

import httpx


class HttpService:
    """Provide timeout-controlled HTTP requests for plugins."""

    def __init__(self, *, timeout_seconds: float = 30.0) -> None:
        """Configure a default timeout for outbound plugin HTTP requests."""
        self.timeout_seconds = float(timeout_seconds or 30.0)

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Execute one HTTP request with a bounded timeout."""
        kwargs.setdefault("timeout", self.timeout_seconds)
        return httpx.request(str(method or "GET").upper(), url, **kwargs)
