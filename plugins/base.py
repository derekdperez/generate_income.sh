from __future__ import annotations

from dataclasses import dataclass
from typing import Any


RunResult = tuple[int, str]


@dataclass(frozen=True)
class PluginExecutionContext:
    coordinator: Any
    worker_id: str
    root_domain: str
    workflow_id: str
    plugin_name: str
    entry: dict[str, Any]


class CoordinatorPlugin:
    plugin_name = ""

    def run(self, context: PluginExecutionContext) -> RunResult:
        raise NotImplementedError

