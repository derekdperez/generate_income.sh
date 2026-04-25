"""In-memory per-target budget tracker for plugin services."""

from __future__ import annotations

import threading


class BudgetService:
    """Track and enforce per-target integer budgets."""

    def __init__(self, *, default_budget: int = 1000) -> None:
        """Initialize service with a default budget for unseen targets."""
        self._default_budget = max(1, int(default_budget or 1000))
        self._lock = threading.Lock()
        self._remaining: dict[str, int] = {}

    def remaining(self, target_id: str) -> int:
        """Return remaining budget units for a target."""
        target = str(target_id or "").strip().lower()
        if not target:
            return 0
        with self._lock:
            return int(self._remaining.get(target, self._default_budget))

    def consume(self, target_id: str, units: int = 1) -> bool:
        """Consume budget units and return True if allowed."""
        target = str(target_id or "").strip().lower()
        debit = max(1, int(units or 1))
        if not target:
            return False
        with self._lock:
            left = int(self._remaining.get(target, self._default_budget))
            if left < debit:
                self._remaining[target] = left
                return False
            self._remaining[target] = left - debit
            return True
