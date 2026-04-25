"""Lightweight telemetry facade with OpenTelemetry-ready extension points."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from time import perf_counter
from typing import Any, Iterator


class TelemetryClient:
    """Minimal telemetry client used at service boundaries."""

    def __init__(self, scope: str) -> None:
        self._scope = str(scope or "app")
        self._logger = logging.getLogger(f"telemetry.{self._scope}")

    def incr(self, metric_name: str, value: int = 1, *, tags: dict[str, str] | None = None) -> None:
        """Increment a counter metric."""
        self._log_metric("counter", metric_name, value, tags=tags)

    def gauge(self, metric_name: str, value: float, *, tags: dict[str, str] | None = None) -> None:
        """Record a gauge metric."""
        self._log_metric("gauge", metric_name, value, tags=tags)

    def observe_ms(self, metric_name: str, duration_ms: float, *, tags: dict[str, str] | None = None) -> None:
        """Record a timing metric in milliseconds."""
        self._log_metric("timing_ms", metric_name, duration_ms, tags=tags)

    @contextmanager
    def span(self, name: str, *, tags: dict[str, str] | None = None) -> Iterator[None]:
        """Measure a block duration and emit a timing metric."""
        start = perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (perf_counter() - start) * 1000.0
            self.observe_ms(f"{name}.duration_ms", elapsed_ms, tags=tags)

    def _log_metric(self, metric_type: str, metric_name: str, value: float, *, tags: dict[str, str] | None) -> None:
        payload: dict[str, Any] = {
            "scope": self._scope,
            "metric_type": str(metric_type or "counter"),
            "metric_name": str(metric_name or "unnamed"),
            "value": value,
            "tags": tags or {},
        }
        self._logger.debug("telemetry_metric", extra={"telemetry": payload})


def get_telemetry(scope: str) -> TelemetryClient:
    """Return a telemetry client for the provided scope."""
    return TelemetryClient(scope)
