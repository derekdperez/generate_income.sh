from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from nightmare_shared.config import (
    ClientSettings,
    CoordinatorSettings,
    ServerSettings,
    atomic_write_json,
    load_env_file_into_os,
    read_env_file,
    read_json_dict,
    resolve_config_path,
)


def test_read_json_dict_returns_empty_for_invalid_json(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text("{not-json", encoding="utf-8")
    assert read_json_dict(path) == {}


def test_read_env_file_parses_non_comment_assignments(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# comment",
                "FOO=bar",
                "EMPTY=",
                "SPACED = value ",
                "NO_EQUALS",
            ]
        ),
        encoding="utf-8",
    )
    parsed = read_env_file(env_file)
    assert parsed["FOO"] == "bar"
    assert parsed["EMPTY"] == ""
    assert parsed["SPACED"] == "value"
    assert "NO_EQUALS" not in parsed


def test_load_env_file_into_os_respects_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    env_file = tmp_path / ".env"
    env_file.write_text("A=from-file\nB=from-file\n", encoding="utf-8")

    monkeypatch.setenv("A", "existing")
    monkeypatch.delenv("B", raising=False)

    load_env_file_into_os(env_file, override=False)
    assert "existing" == os.environ["A"]
    assert "from-file" == os.environ["B"]

    load_env_file_into_os(env_file, override=True)
    assert "from-file" == os.environ["A"]


def test_atomic_write_json_writes_expected_payload(tmp_path: Path):
    out = tmp_path / "state" / "payload.json"
    payload = {"hello": "world", "n": 3}
    atomic_write_json(out, payload)
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded == payload


def test_resolve_config_path_prefers_config_subdir_for_bare_names(tmp_path: Path):
    base_dir = tmp_path / "repo"
    (base_dir / "config").mkdir(parents=True)
    resolved = resolve_config_path(base_dir, "coordinator.json", "ignored.json")
    assert resolved == (base_dir / "config" / "coordinator.json").resolve()


def test_server_settings_port_validation_rejects_out_of_range():
    with pytest.raises(Exception):
        ServerSettings.model_validate({"http_port": 70000, "https_port": 443})


def test_coordinator_settings_minimums_and_normalization():
    settings = CoordinatorSettings.model_validate(
        {
            "server_base_url": "coord.example.com",
            "lease_seconds": 1,
            "heartbeat_interval_seconds": 0.1,
            "poll_interval_seconds": 0.1,
            "nightmare_workers": 0,
            "fozzy_workers": 0,
            "extractor_workers": 0,
        }
    )
    assert settings.server_base_url == "https://coord.example.com"
    assert settings.lease_seconds >= 30
    assert settings.heartbeat_interval_seconds >= 5.0
    assert settings.poll_interval_seconds >= 1.0
    assert settings.nightmare_workers >= 1
    assert settings.fozzy_workers >= 1
    assert settings.extractor_workers >= 1


def test_client_settings_trims_api_token_and_normalizes_url():
    settings = ClientSettings.model_validate({"server_base_url": "example.com", "api_token": " abc "})
    assert settings.server_base_url == "https://example.com"
    assert settings.api_token == "abc"
