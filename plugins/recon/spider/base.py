from __future__ import annotations

from plugins.base import CoordinatorPlugin, PluginExecutionContext, RunResult


class ReconSpiderPlugin(CoordinatorPlugin):
    plugin_name = ""

    def run(self, context: PluginExecutionContext) -> RunResult:
        active_plugin_name = self.plugin_name or context.plugin_name
        return context.coordinator._run_recon_spider_plugin_task(
            worker_id=context.worker_id,
            root_domain=context.root_domain,
            plugin_name=active_plugin_name,
        )

