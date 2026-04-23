from __future__ import annotations

from plugins.base import CoordinatorPlugin, PluginExecutionContext, RunResult


class ReconExtractorHighValuePlugin(CoordinatorPlugin):
    plugin_name = "recon_extractor_high_value"

    def run(self, context: PluginExecutionContext) -> RunResult:
        return context.coordinator._run_recon_extractor_high_value_plugin_task(
            worker_id=context.worker_id,
            root_domain=context.root_domain,
            plugin_name=context.plugin_name,
        )

