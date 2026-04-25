"""Plugin-facing artifact metadata helper."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class ArtifactService:
    """Write and register plugin artifacts through the coordinator store."""

    def __init__(self, store: Any, root_domain: str) -> None:
        """Bind artifact operations to one root domain."""
        self._store = store
        self._root_domain = str(root_domain or "").strip().lower()

    def record_file(self, artifact_type: str, path: str | Path, *, metadata: dict[str, Any] | None = None) -> bool:
        """Register an existing file as an artifact using the store's artifact API."""
        if not self._root_domain:
            raise ValueError("root_domain is required")
        return bool(
            self._store.record_artifact(
                self._root_domain,
                str(artifact_type or "").strip().lower(),
                str(Path(path)),
                metadata=metadata or {},
            )
        )
