from __future__ import annotations

from plugins.base import CoordinatorPlugin, PluginExecutionContext, RunResult


class FozzyPlugin(CoordinatorPlugin):
    plugin_name = "fozzy"

    def run(self, context: PluginExecutionContext) -> RunResult:
        return context.coordinator._run_fozzy_plugin_task(
            worker_id=context.worker_id,
            root_domain=context.root_domain,
            plugin_name=context.plugin_name,
        )

