from __future__ import annotations

import argparse
import base64
import json
import zipfile
from pathlib import Path

import pytest

import pack
import unpack


def test_build_pack_payload_excludes_output_dir_and_generated_outputs(tmp_path: Path):
    root = tmp_path / "repo"
    (root / "output").mkdir(parents=True)
    (root / "output" / "skip.txt").write_text("skip", encoding="utf-8")
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.txt").write_text("hello", encoding="utf-8")

    output_path = root / "packed.json"
    zip_path = root / "packed.zip"

    payload = pack.build_pack_payload(root, output_path, zip_path)
    paths = [item["path"] for item in payload["files"]]
    assert "src/a.txt" in paths
    assert "output/skip.txt" not in paths
    assert "packed.json" not in paths
    assert "packed.zip" not in paths


def test_write_zip_archive_writes_expected_entries(tmp_path: Path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.txt").write_text("hello", encoding="utf-8")
    output_path = root / "packed.json"
    zip_path = root / "packed.zip"

    file_count, _ = pack.write_zip_archive(root, zip_path, output_path)
    assert file_count == 1
    with zipfile.ZipFile(zip_path, "r") as zf:
        assert sorted(zf.namelist()) == ["src/a.txt"]


def test_safe_rel_path_rejects_traversal_and_absolute_paths():
    with pytest.raises(ValueError):
        unpack._safe_rel_path("/absolute/path.txt")
    with pytest.raises(ValueError):
        unpack._safe_rel_path("../escape.txt")


def test_read_pack_supports_transport_envelope(tmp_path: Path):
    payload = {"directories": ["a"], "files": []}
    inner = json.dumps(payload).encode("utf-8")
    envelope = {
        "transport_encoding": "base64-json",
        "payload_base64": base64.b64encode(inner).decode("ascii"),
    }
    packed_path = tmp_path / "packed.json"
    packed_path.write_text(json.dumps(envelope), encoding="utf-8")

    loaded = unpack._read_pack(packed_path)
    assert loaded == payload


def test_unpack_main_writes_files_from_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    packed_path = tmp_path / "packed.json"
    payload = {
        "directories": ["nested"],
        "files": [
            {
                "path": "nested/example.txt",
                "encoding": "base64",
                "mode": 0o644,
                "content": base64.b64encode(b"hello").decode("ascii"),
            }
        ],
    }
    packed_path.write_text(json.dumps(payload), encoding="utf-8")
    target = tmp_path / "out"

    monkeypatch.setattr(unpack, "parse_args", lambda: argparse.Namespace(input=str(packed_path), target=str(target)))
    rc = unpack.main()
    assert rc == 0
    assert (target / "nested" / "example.txt").read_bytes() == b"hello"

