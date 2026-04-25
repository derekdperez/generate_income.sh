"""Plugin-facing helper for emitting structured coordinator events."""

from __future__ import annotations

from typing import Any


class EventService:
    """Emit structured events through the coordinator store without exposing store internals."""

    def __init__(self, store: Any, *, source: str) -> None:
        """Bind the service to a coordinator store and a stable source name."""
        self._store = store
        self._source = str(source or "plugin")

    def emit(self, event_type: str, aggregate_key: str, payload: dict[str, Any] | None = None) -> None:
        """Publish one event with a consistent source field."""
        body = dict(payload or {})
        body.setdefault("source", self._source)
        self._store.record_system_event(event_type, aggregate_key, body)
