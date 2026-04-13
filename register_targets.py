#!/usr/bin/env python3
"""Register targets into the central coordinator queue."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _read_targets(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8-sig")
    out: list[str] = []
    for line in raw.splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        out.append(value)
    return out


def _post_json(base_url: str, token: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"
    req = Request(url=f"{base_url.rstrip('/')}{path}", method="POST", data=data, headers=headers)
    try:
        with urlopen(req, timeout=30) as rsp:
            raw = rsp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error: {exc}") from exc
    parsed = json.loads(raw or "{}")
    return parsed if isinstance(parsed, dict) else {}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Register targets into coordinator queue")
    p.add_argument("--server-base-url", required=True, help="Coordinator server base URL")
    p.add_argument("--api-token", default="", help="Coordinator API token")
    p.add_argument("--targets-file", default="targets.txt", help="Targets text file")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    targets = _read_targets(Path(args.targets_file).expanduser().resolve())
    rsp = _post_json(
        args.server_base_url,
        args.api_token,
        "/api/coord/register-targets",
        {"targets": targets},
    )
    print(json.dumps(rsp, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

