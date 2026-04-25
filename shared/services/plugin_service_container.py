"""Factory for the system-level services exposed to plugin execution contexts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .artifact_service import ArtifactService
from .event_service import EventService
from .file_service import FileService
from .http_service import HttpService
from .subprocess_service import SubprocessService


class PluginServiceContainer:
    """Bundle controlled system services for a single plugin task."""

    def __init__(self, store: Any, *, root_domain: str, workspace: str | Path, source: str) -> None:
        """Create plugin services bound to a root domain and workspace."""
        self.files = FileService(workspace)
        self.http = HttpService()
        self.subprocesses = SubprocessService()
        self.events = EventService(store, source=source)
        self.artifacts = ArtifactService(store, root_domain)
