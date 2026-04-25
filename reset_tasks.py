#!/usr/bin/env python3
"""Bulk reset coordinator tasks directly against Postgres."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from shared.runtime_common.config import load_env_file_into_os
from app_platform.server.store import CoordinatorStore

BASE_DIR = Path(__file__).resolve().parent


def _parse_csv(value: str) -> list[str]:
    return [part.strip().lower() for part in str(value or "").split(",") if part.strip()]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    load_env_file_into_os(BASE_DIR / "deploy" / ".env", override=False)
    parser = argparse.ArgumentParser(description="Bulk reset coordinator stage/target tasks.")
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", ""),
        help="Coordinator Postgres URL (defaults to DATABASE_URL/deploy/.env).",
    )
    parser.add_argument(
        "--scope",
        default="all",
        choices=["stage_tasks", "targets", "all"],
        help="What to reset.",
    )
    parser.add_argument(
        "--workflow-id",
        default="",
        help="Workflow id filter for stage tasks (blank = all).",
    )
    parser.add_argument(
        "--root-domains",
        default="",
        help="Comma-separated root domains (blank = all).",
    )
    parser.add_argument(
        "--plugins",
        default="",
        help="Comma-separated stage/plugin names for stage task scope.",
    )
    parser.add_argument(
        "--statuses",
        default="failed",
        help="Comma-separated statuses. Use 'errored' as alias for 'failed'. Blank = all statuses.",
    )
    parser.add_argument(
        "--hard-delete",
        action="store_true",
        help="Delete matching rows instead of resetting to pending.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    database_url = str(args.database_url or "").strip()
    if not database_url:
        raise ValueError("database URL is required (use --database-url or DATABASE_URL)")

    scope = str(args.scope or "all").strip().lower()
    root_domains = _parse_csv(args.root_domains)
    plugins = _parse_csv(args.plugins)
    statuses = _parse_csv(args.statuses)
    workflow_id = str(args.workflow_id or "").strip().lower()
    hard_delete = bool(args.hard_delete)

    store = CoordinatorStore(database_url)
    try:
        stage_result: dict[str, Any] | None = None
        target_result: dict[str, Any] | None = None
        if scope in {"stage_tasks", "all"}:
            stage_result = store.reset_stage_tasks(
                workflow_id=workflow_id,
                root_domains=root_domains,
                plugins=plugins,
                statuses=statuses,
                hard_delete=hard_delete,
            )
        if scope in {"targets", "all"}:
            target_result = store.reset_targets(
                root_domains=root_domains,
                statuses=statuses,
                hard_delete=hard_delete,
            )
    finally:
        try:
            store.close()
        except Exception:
            pass

    payload = {
        "ok": True,
        "scope": scope,
        "workflow_id": workflow_id,
        "root_domains": root_domains,
        "plugins": plugins,
        "statuses": statuses,
        "hard_delete": hard_delete,
        "stage_tasks": stage_result,
        "targets": target_result,
        "total_affected_rows": int((stage_result or {}).get("affected_rows") or 0)
        + int((target_result or {}).get("affected_rows") or 0),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

