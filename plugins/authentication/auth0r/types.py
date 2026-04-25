
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from plugins.authentication.auth0r.policy import ReplayPolicy


@dataclass
class AuthVerificationMarker:
    kind: str
    value: str


@dataclass
class AuthIdentity:
    id: str
    profile_id: str
    identity_label: str
    role_label: str
    tenant_label: str
    login_strategy: str
    username: str = ""
    password: str = ""
    login_url: str = ""
    login_method: str = "POST"
    login_username_field: str = "username"
    login_password_field: str = "password"
    login_extra_fields: dict[str, Any] = field(default_factory=dict)
    imported_cookies: list[dict[str, Any]] = field(default_factory=list)
    custom_headers: dict[str, str] = field(default_factory=dict)
    allowed_hosts: list[str] = field(default_factory=list)
    success_markers: list[AuthVerificationMarker] = field(default_factory=list)
    denial_markers: list[AuthVerificationMarker] = field(default_factory=list)
    authenticated_probe_url: str = ""
    logout_url: str = ""
    replay_policy: ReplayPolicy = field(default_factory=ReplayPolicy)


@dataclass
class RuntimeSession:
    id: str
    identity_id: str
    source_type: str
    session_generation: int
    cookie_names: list[str]
    auth_header_names: list[str]
    verified: bool
    verification_summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class RecordedAction:
    url: str
    method: str
    source: str
    content_type: str = ""
    likely_state_changing: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReplayResult:
    variant: str
    url: str
    method: str
    status_code: int
    body_similarity: float
    redirect_count: int
    denial_markers_matched: list[str]
    authenticated_markers_matched: list[str]
    suspicious: bool
    summary: dict[str, Any] = field(default_factory=dict)
