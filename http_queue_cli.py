#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from http_request_queue import HttpRequestQueue


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Inspect or recover the local HTTP request queue")
    p.add_argument("--db-path", default="output/http_request_queue.sqlite3")
    p.add_argument("--spool-dir", default="output/http-request-spool")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("stats")
    sub.add_parser("requeue-expired")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    queue = HttpRequestQueue(args.db_path, args.spool_dir)
    if args.command == "stats":
        print(json.dumps(queue.stats(), indent=2, ensure_ascii=False))
        return 0
    if args.command == "requeue-expired":
        print(json.dumps({"requeued": queue.requeue_expired_leases()}, indent=2, ensure_ascii=False))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
