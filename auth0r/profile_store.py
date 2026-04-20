
from __future__ import annotations

import json
import uuid
from typing import Any

try:
    import psycopg
except Exception:  # pragma: no cover
    psycopg = None

from auth0r.crypto import decrypt_text, encrypt_text
from auth0r.policy import ReplayPolicy
from auth0r.types import AuthIdentity, AuthVerificationMarker


def _json_load(value: Any, default: Any):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value)
    except Exception:
        return default
    return parsed if isinstance(parsed, type(default)) else default


class Auth0rProfileStore:
    def __init__(self, database_url: str):
        if psycopg is None:
            raise RuntimeError("psycopg is required")
        self.database_url = str(database_url or "").strip()
        if not self.database_url:
            raise ValueError("database_url is required")
        self._ensure_schema()

    def _connect(self):
        return psycopg.connect(self.database_url, autocommit=False)

    def _ensure_schema(self) -> None:
        ddl = """
CREATE TABLE IF NOT EXISTS auth0r_profiles (
  id UUID PRIMARY KEY,
  root_domain TEXT NOT NULL,
  profile_label TEXT NOT NULL,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  allowed_hosts_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  default_headers_encrypted BYTEA,
  replay_policy_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_auth0r_profiles_domain_enabled ON auth0r_profiles(root_domain, enabled);

CREATE TABLE IF NOT EXISTS auth0r_identities (
  id UUID PRIMARY KEY,
  profile_id UUID NOT NULL REFERENCES auth0r_profiles(id) ON DELETE CASCADE,
  identity_label TEXT NOT NULL,
  role_label TEXT NOT NULL DEFAULT '',
  tenant_label TEXT NOT NULL DEFAULT '',
  login_strategy TEXT NOT NULL DEFAULT 'cookie_import',
  username_encrypted BYTEA,
  password_encrypted BYTEA,
  login_config_json_encrypted BYTEA,
  custom_headers_json_encrypted BYTEA,
  success_markers_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  denial_markers_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_auth0r_identities_profile_enabled ON auth0r_identities(profile_id, enabled);

CREATE TABLE IF NOT EXISTS auth0r_cookie_jars (
  id UUID PRIMARY KEY,
  identity_id UUID NOT NULL REFERENCES auth0r_identities(id) ON DELETE CASCADE,
  jar_label TEXT NOT NULL DEFAULT 'default',
  cookies_json_encrypted BYTEA NOT NULL,
  host_scope_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  path_scope_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS auth0r_runtime_sessions (
  id UUID PRIMARY KEY,
  root_domain TEXT NOT NULL,
  identity_id UUID NOT NULL,
  source_type TEXT NOT NULL,
  generation_number INTEGER NOT NULL DEFAULT 1,
  verified BOOLEAN NOT NULL DEFAULT FALSE,
  verification_summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  cookies_encrypted BYTEA,
  auth_headers_encrypted BYTEA,
  created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_auth0r_runtime_sessions_domain ON auth0r_runtime_sessions(root_domain, created_at_utc DESC);

CREATE TABLE IF NOT EXISTS auth0r_recorded_actions (
  id UUID PRIMARY KEY,
  root_domain TEXT NOT NULL,
  identity_id UUID NOT NULL,
  runtime_session_id UUID NOT NULL,
  url TEXT NOT NULL,
  method TEXT NOT NULL,
  source TEXT NOT NULL,
  content_type TEXT NOT NULL DEFAULT '',
  likely_state_changing BOOLEAN NOT NULL DEFAULT FALSE,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_auth0r_recorded_actions_domain ON auth0r_recorded_actions(root_domain, created_at_utc DESC);

CREATE TABLE IF NOT EXISTS auth0r_replay_attempts (
  id UUID PRIMARY KEY,
  root_domain TEXT NOT NULL,
  identity_id UUID NOT NULL,
  recorded_action_id UUID NOT NULL,
  replay_variant TEXT NOT NULL,
  status_code INTEGER,
  suspicious BOOLEAN NOT NULL DEFAULT FALSE,
  summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_auth0r_replay_attempts_domain ON auth0r_replay_attempts(root_domain, created_at_utc DESC);

CREATE TABLE IF NOT EXISTS auth0r_findings (
  id UUID PRIMARY KEY,
  root_domain TEXT NOT NULL,
  identity_id UUID,
  finding_type TEXT NOT NULL,
  severity TEXT NOT NULL DEFAULT 'medium',
  title TEXT NOT NULL,
  endpoint TEXT NOT NULL DEFAULT '',
  replay_variant TEXT NOT NULL DEFAULT '',
  confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
  summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_auth0r_findings_domain ON auth0r_findings(root_domain, created_at_utc DESC);

CREATE TABLE IF NOT EXISTS auth0r_evidence (
  id UUID PRIMARY KEY,
  root_domain TEXT NOT NULL,
  identity_id UUID,
  category TEXT NOT NULL,
  ref_id TEXT NOT NULL DEFAULT '',
  payload_encrypted BYTEA NOT NULL,
  created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_auth0r_evidence_domain ON auth0r_evidence(root_domain, created_at_utc DESC);
"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(ddl)
            conn.commit()

    def list_enabled_identities(self, root_domain: str) -> list[AuthIdentity]:
        sql = """
SELECT
  i.id::text,
  i.profile_id::text,
  i.identity_label,
  i.role_label,
  i.tenant_label,
  i.login_strategy,
  i.username_encrypted,
  i.password_encrypted,
  i.login_config_json_encrypted,
  i.custom_headers_json_encrypted,
  i.success_markers_json,
  i.denial_markers_json,
  p.allowed_hosts_json,
  p.replay_policy_json
FROM auth0r_profiles p
JOIN auth0r_identities i ON i.profile_id = p.id
WHERE p.root_domain = %s
  AND p.enabled = TRUE
  AND i.enabled = TRUE
ORDER BY i.created_at_utc ASC
"""
        identities: list[AuthIdentity] = []
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (root_domain,))
                rows = cur.fetchall()
                for row in rows:
                    login_cfg = _json_load(decrypt_text(row[8]), {}) if row[8] else {}
                    custom_headers = _json_load(decrypt_text(row[9]), {}) if row[9] else {}
                    success_markers = [
                        AuthVerificationMarker(kind=str(item.get("kind", "text")), value=str(item.get("value", "")))
                        for item in _json_load(row[10], [])
                        if isinstance(item, dict)
                    ]
                    denial_markers = [
                        AuthVerificationMarker(kind=str(item.get("kind", "text")), value=str(item.get("value", "")))
                        for item in _json_load(row[11], [])
                        if isinstance(item, dict)
                    ]
                    identity = AuthIdentity(
                        id=row[0],
                        profile_id=row[1],
                        identity_label=row[2],
                        role_label=row[3] or "",
                        tenant_label=row[4] or "",
                        login_strategy=row[5] or "cookie_import",
                        username=decrypt_text(row[6]) if row[6] else "",
                        password=decrypt_text(row[7]) if row[7] else "",
                        login_url=str(login_cfg.get("login_url", "") or ""),
                        login_method=str(login_cfg.get("login_method", "POST") or "POST").upper(),
                        login_username_field=str(login_cfg.get("username_field", "username") or "username"),
                        login_password_field=str(login_cfg.get("password_field", "password") or "password"),
                        login_extra_fields=(login_cfg.get("extra_fields", {}) if isinstance(login_cfg.get("extra_fields", {}), dict) else {}),
                        custom_headers={str(k): str(v) for k, v in custom_headers.items()} if isinstance(custom_headers, dict) else {},
                        allowed_hosts=[str(v) for v in _json_load(row[12], []) if str(v).strip()],
                        success_markers=success_markers,
                        denial_markers=denial_markers,
                        authenticated_probe_url=str(login_cfg.get("authenticated_probe_url", "") or ""),
                        logout_url=str(login_cfg.get("logout_url", "") or ""),
                        replay_policy=ReplayPolicy.from_dict(_json_load(row[13], {})),
                    )
                    identity.imported_cookies = self._load_cookie_jar(identity.id)
                    identities.append(identity)
        return identities

    def _load_cookie_jar(self, identity_id: str) -> list[dict[str, Any]]:
        sql = """
SELECT cookies_json_encrypted
FROM auth0r_cookie_jars
WHERE identity_id = %s
ORDER BY created_at_utc DESC
LIMIT 1
"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (identity_id,))
                row = cur.fetchone()
                if not row:
                    return []
                return _json_load(decrypt_text(row[0]), []) if row[0] else []

    def save_runtime_session(
        self,
        root_domain: str,
        identity_id: str,
        source_type: str,
        generation_number: int,
        *,
        verified: bool,
        verification_summary: dict[str, Any],
        cookies: list[dict[str, Any]],
        auth_headers: dict[str, Any],
    ) -> str:
        session_id = str(uuid.uuid4())
        sql = """
INSERT INTO auth0r_runtime_sessions
(id, root_domain, identity_id, source_type, generation_number, verified, verification_summary_json, cookies_encrypted, auth_headers_encrypted)
VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        session_id,
                        root_domain,
                        identity_id,
                        source_type,
                        int(generation_number),
                        bool(verified),
                        json.dumps(verification_summary or {}),
                        encrypt_text(json.dumps(cookies or [])),
                        encrypt_text(json.dumps(auth_headers or {})),
                    ),
                )
            conn.commit()
        return session_id

    def save_recorded_action(self, root_domain: str, identity_id: str, runtime_session_id: str, action: dict[str, Any]) -> str:
        action_id = str(uuid.uuid4())
        sql = """
INSERT INTO auth0r_recorded_actions
(id, root_domain, identity_id, runtime_session_id, url, method, source, content_type, likely_state_changing, metadata_json)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        action_id,
                        root_domain,
                        identity_id,
                        runtime_session_id,
                        str(action.get("url", "")),
                        str(action.get("method", "GET")).upper(),
                        str(action.get("source", "nightmare_seed")),
                        str(action.get("content_type", "") or ""),
                        bool(action.get("likely_state_changing")),
                        json.dumps(action.get("metadata", {}) or {}),
                    ),
                )
            conn.commit()
        return action_id

    def save_replay_attempt(
        self,
        root_domain: str,
        identity_id: str,
        recorded_action_id: str,
        replay_variant: str,
        *,
        status_code: int | None,
        suspicious: bool,
        summary: dict[str, Any],
    ) -> str:
        replay_id = str(uuid.uuid4())
        sql = """
INSERT INTO auth0r_replay_attempts
(id, root_domain, identity_id, recorded_action_id, replay_variant, status_code, suspicious, summary_json)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        replay_id,
                        root_domain,
                        identity_id,
                        recorded_action_id,
                        replay_variant,
                        status_code,
                        bool(suspicious),
                        json.dumps(summary or {}),
                    ),
                )
            conn.commit()
        return replay_id

    def save_finding(
        self,
        root_domain: str,
        identity_id: str | None,
        finding_type: str,
        severity: str,
        title: str,
        endpoint: str,
        replay_variant: str,
        confidence: float,
        summary: dict[str, Any],
    ) -> str:
        finding_id = str(uuid.uuid4())
        sql = """
INSERT INTO auth0r_findings
(id, root_domain, identity_id, finding_type, severity, title, endpoint, replay_variant, confidence, summary_json)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        finding_id,
                        root_domain,
                        identity_id,
                        finding_type,
                        severity,
                        title,
                        endpoint,
                        replay_variant,
                        float(confidence),
                        json.dumps(summary or {}),
                    ),
                )
            conn.commit()
        return finding_id

    def save_evidence(self, root_domain: str, identity_id: str | None, category: str, ref_id: str, payload: dict[str, Any]) -> str:
        evidence_id = str(uuid.uuid4())
        sql = """
INSERT INTO auth0r_evidence
(id, root_domain, identity_id, category, ref_id, payload_encrypted)
VALUES (%s, %s, %s, %s, %s, %s)
"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        evidence_id,
                        root_domain,
                        identity_id,
                        category,
                        ref_id,
                        encrypt_text(json.dumps(payload or {})),
                    ),
                )
            conn.commit()
        return evidence_id
