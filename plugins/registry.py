from __future__ import annotations

from typing import Callable

from plugins.auth0r_plugin import Auth0rPlugin
from plugins.base import CoordinatorPlugin
from plugins.extractor_plugin import ExtractorPlugin
from plugins.fozzy_plugin import FozzyPlugin
from plugins.nightmare_artifact_gate_plugin import NightmareArtifactGatePlugin
from plugins.recon.extractor_high_value_plugin import ReconExtractorHighValuePlugin
from plugins.recon.spider.ai_plugin import ReconSpiderAiPlugin
from plugins.recon.spider.script_links_plugin import ReconSpiderScriptLinksPlugin
from plugins.recon.spider.source_tags_plugin import ReconSpiderSourceTagsPlugin
from plugins.recon.spider.wordlist_plugin import ReconSpiderWordlistPlugin
from plugins.recon.subdomain_enumeration_plugin import ReconSubdomainEnumerationPlugin


_PLUGIN_FACTORIES: dict[str, Callable[[], CoordinatorPlugin]] = {
    "auth0r": Auth0rPlugin,
    "extractor": ExtractorPlugin,
    "fozzy": FozzyPlugin,
    "recon_subdomain_enumeration": ReconSubdomainEnumerationPlugin,
    "recon_spider_source_tags": ReconSpiderSourceTagsPlugin,
    "recon_spider_script_links": ReconSpiderScriptLinksPlugin,
    "recon_spider_wordlist": ReconSpiderWordlistPlugin,
    "recon_spider_ai": ReconSpiderAiPlugin,
    "recon_extractor_high_value": ReconExtractorHighValuePlugin,
}


def resolve_plugin(plugin_name: str) -> CoordinatorPlugin | None:
    normalized = str(plugin_name or "").strip().lower()
    if not normalized:
        return None
    if normalized.startswith("nightmare_"):
        return NightmareArtifactGatePlugin()
    factory = _PLUGIN_FACTORIES.get(normalized)
    if not factory:
        return None
    return factory()


def list_registered_plugins() -> list[str]:
    """Return plugin keys available to coordinator workers."""
    return sorted(_PLUGIN_FACTORIES.keys())


def list_plugin_contracts() -> list[dict[str, object]]:
    """Return lightweight built-in plugin contracts for DB bootstrap/UI authoring.

    Contract rows can later be edited in the database without changing the runtime
    registry. The registry remains the source for executable Python classes.
    """
    contracts: list[dict[str, object]] = []
    for key, factory in sorted(_PLUGIN_FACTORIES.items()):
        cls = factory
        module = getattr(cls, "__module__", "")
        name = getattr(cls, "__name__", "")
        plugin = factory()
        try:
            stage_contract = plugin.contract()
        except Exception:
            stage_contract = None
        contracts.append(
            {
                "plugin_key": key,
                "display_name": key.replace("_", " ").title(),
                "description": f"Coordinator plugin implemented by {module}.{name}.",
                "category": key.split("_", 1)[0] if "_" in key else "general",
                "python_module": module,
                "python_class": name,
                "contract_version": "1.0.0",
                "input_schema": {
                    "type": "object",
                    "additionalProperties": True,
                    "properties": {
                        "root_domain": {"type": "string"},
                    },
                },
                "output_schema": {
                    "type": "object",
                    "additionalProperties": True,
                    "properties": {
                        "artifacts": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "config_schema": {
                    "type": "object",
                    "additionalProperties": True,
                },
                "ui_schema": {},
                "examples": [],
                "tags": [key.split("_", 1)[0]] if "_" in key else [],
                "enabled": True,
                "source_path": (str(module).replace(".", "/") + ".py") if module else "",
                "input_artifacts": list(getattr(stage_contract, "input_artifacts", ()) or ()),
                "output_artifacts": list(getattr(stage_contract, "output_artifacts", ()) or ()),
            }
        )
    return contracts
