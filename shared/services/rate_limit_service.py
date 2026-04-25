"""In-memory per-target rate limiting helper for plugin services."""

from __future__ import annotations

import threading
import time


class RateLimitService:
    """Track target request rates using a fixed-window counter."""

    def __init__(self, *, requests_per_minute: int = 120) -> None:
        """Initialize service with a default per-target request budget."""
        self._rpm = max(1, int(requests_per_minute or 120))
        self._lock = threading.Lock()
        self._state: dict[str, tuple[int, float]] = {}

    def try_acquire(self, target_id: str, *, now: float | None = None) -> bool:
        """Return True when a request is allowed for the target."""
        target = str(target_id or "").strip().lower()
        if not target:
            return False
        ts = float(now if now is not None else time.time())
        with self._lock:
            count, window_start = self._state.get(target, (0, ts))
            if ts - window_start >= 60.0:
                count, window_start = 0, ts
            if count >= self._rpm:
                self._state[target] = (count, window_start)
                return False
            self._state[target] = (count + 1, window_start)
            return True
