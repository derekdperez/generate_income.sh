from __future__ import annotations

from plugins.base import CoordinatorPlugin, PluginExecutionContext, RunResult


class NightmareArtifactGatePlugin(CoordinatorPlugin):
    plugin_name = "nightmare_artifact_gate"

    def run(self, context: PluginExecutionContext) -> RunResult:
        return context.coordinator._run_nightmare_artifact_gate_plugin(
            root_domain=context.root_domain,
            plugin_name=context.plugin_name,
        )

