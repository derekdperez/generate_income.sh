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
from plugins.recon.subdomain_takeover_plugin import ReconSubdomainTakeoverPlugin


_PLUGIN_FACTORIES: dict[str, Callable[[], CoordinatorPlugin]] = {
    "auth0r": Auth0rPlugin,
    "extractor": ExtractorPlugin,
    "fozzy": FozzyPlugin,
    "recon_subdomain_enumeration": ReconSubdomainEnumerationPlugin,
    "recon_subdomain_takeover": ReconSubdomainTakeoverPlugin,
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
