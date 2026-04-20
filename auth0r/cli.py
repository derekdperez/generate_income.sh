
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import httpx

from auth0r.canonicalize import canonicalize_url, likely_state_changing
from auth0r.differential_analyzer import compare_responses
from auth0r.login_orchestrator import establish_session
from auth0r.profile_store import Auth0rProfileStore
from auth0r.replay_engine import DomainThrottle, replay_variants
from auth0r.reporting import write_summary


def _read_json_dict(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _discover_seed_actions(root_domain: str, nightmare_session_path: Path) -> list[dict]:
    payload = _read_json_dict(nightmare_session_path)
    state = payload.get("state", {}) if isinstance(payload, dict) else {}
    out = []
    seen = set()
    for url in state.get("discovered_urls", []) or []:
        text = str(url or "").strip()
        if not text:
            continue
        parsed = urlparse(text)
        if root_domain not in (parsed.hostname or "").lower():
            continue
        canon = canonicalize_url(text)
        if canon in seen:
            continue
        seen.add(canon)
        out.append({"url": text, "method": "GET", "source": "nightmare_seed", "metadata": {"canonical_url": canon}})
    return out


def run(root_domain: str, nightmare_session_path: Path, summary_path: Path, *, database_url: str, min_delay_seconds: float, verify_tls: bool) -> int:
    store = Auth0rProfileStore(database_url)
    identities = store.list_enabled_identities(root_domain)
    if not identities:
        write_summary(summary_path, {"root_domain": root_domain, "status": "skipped", "reason": "no_enabled_auth_profiles"})
        return 0

    seed_actions = _discover_seed_actions(root_domain, nightmare_session_path)
    throttle = DomainThrottle(min_delay_seconds=min_delay_seconds)
    findings = []
    session_count = 0
    replay_count = 0

    for identity in identities:
        base_url = identity.authenticated_probe_url or (seed_actions[0]["url"] if seed_actions else identity.login_url)
        client, source_type, verification_summary = establish_session(identity, base_url, verify_tls=verify_tls)
        try:
            runtime_session_id = store.save_runtime_session(
                root_domain,
                identity.id,
                source_type,
                1,
                verified=True,
                verification_summary=verification_summary,
                cookies=[{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path} for c in client.cookies.jar],
                auth_headers=dict(client.headers),
            )
            session_count += 1
            for action in seed_actions[:200]:
                action["likely_state_changing"] = likely_state_changing(action.get("method"))
                recorded_action_id = store.save_recorded_action(root_domain, identity.id, runtime_session_id, action)
                throttle.wait()
                baseline = client.request(action.get("method", "GET"), action["url"])
                variants = replay_variants(
                    client,
                    action,
                    throttle=throttle,
                    timeout_seconds=20.0,
                    verify_tls=verify_tls,
                    success_markers=identity.success_markers,
                    denial_markers=identity.denial_markers,
                    logout_url=identity.logout_url,
                )
                for variant, candidate, auth_hits, denial_hits in variants:
                    summary = compare_responses(baseline, candidate, authenticated_hits=auth_hits, denial_hits=denial_hits)
                    replay_count += 1
                    store.save_replay_attempt(
                        root_domain,
                        identity.id,
                        recorded_action_id,
                        variant,
                        status_code=(candidate.status_code if candidate is not None else None),
                        suspicious=bool(summary.get("suspicious")),
                        summary=summary,
                    )
                    if summary.get("suspicious") and variant != "original":
                        title = f"Possible authorization bypass via {variant}"
                        finding_summary = {
                            "identity_label": identity.identity_label,
                            "role_label": identity.role_label,
                            "tenant_label": identity.tenant_label,
                            "expected_behavior": "degraded auth variant should not match authenticated baseline",
                            "observed_behavior": "response remained materially equivalent to authenticated baseline",
                            "comparison": summary,
                        }
                        store.save_finding(root_domain, identity.id, "authorization", "high", title, action["url"], variant, 0.88, finding_summary)
                        findings.append({"title": title, "endpoint": action["url"], "variant": variant, "identity_label": identity.identity_label, "comparison": summary})
        finally:
            client.close()

    payload = {
        "root_domain": root_domain,
        "status": "completed",
        "identity_count": len(identities),
        "runtime_session_count": session_count,
        "seed_action_count": len(seed_actions),
        "replay_attempt_count": replay_count,
        "finding_count": len(findings),
        "findings": findings[:100],
        "minimum_delay_seconds": min_delay_seconds,
    }
    write_summary(summary_path, payload)
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="auth0r authenticated authorization testing stage")
    p.add_argument("root_domain")
    p.add_argument("--nightmare-session", required=True)
    p.add_argument("--summary-json", required=True)
    p.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    p.add_argument("--min-delay-seconds", type=float, default=0.25)
    p.add_argument("--insecure-tls", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    return run(
        str(args.root_domain).strip().lower(),
        Path(args.nightmare_session),
        Path(args.summary_json),
        database_url=args.database_url,
        min_delay_seconds=max(0.25, float(args.min_delay_seconds or 0.25)),
        verify_tls=not bool(args.insecure_tls),
    )


if __name__ == "__main__":
    raise SystemExit(main())
