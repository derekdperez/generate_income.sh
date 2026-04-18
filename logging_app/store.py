#!/usr/bin/env python3
"""Optional structured log event storage (separate Postgres backend)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Optional

try:
    import psycopg
except Exception:  # pragma: no cover - optional dependency in some test envs
    psycopg = None  # type: ignore[assignment]


class LogStore:
    def __init__(self, database_url: str):
        self.database_url = str(database_url or "").strip()
        if not self.database_url:
            raise ValueError("database_url is required")
        if psycopg is None:
            raise RuntimeError("psycopg is required for LogStore")
        self._ensure_schema()

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url, autocommit=False)

    def _ensure_schema(self) -> None:
        ddl = """
CREATE TABLE IF NOT EXISTS application_logs (
  log_id BIGSERIAL PRIMARY KEY,
  event_time_utc TIMESTAMPTZ NOT NULL,
  event_time_est TEXT NOT NULL,
  severity TEXT NOT NULL,
  description TEXT NOT NULL,
  machine TEXT NOT NULL,
  source_id TEXT NOT NULL,
  source_type TEXT NOT NULL,
  raw_line TEXT,
  entry_hash TEXT NOT NULL UNIQUE,
  created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_application_logs_event_time ON application_logs(event_time_utc DESC);
CREATE INDEX IF NOT EXISTS idx_application_logs_severity ON application_logs(severity);
CREATE INDEX IF NOT EXISTS idx_application_logs_machine ON application_logs(machine);
CREATE INDEX IF NOT EXISTS idx_application_logs_source ON application_logs(source_id);
"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(ddl)
            conn.commit()

    @staticmethod
    def _entry_hash(entry: dict[str, Any]) -> str:
        material = "|".join(
            [
                str(entry.get("event_time_est", "") or ""),
                str(entry.get("severity", "") or ""),
                str(entry.get("description", "") or ""),
                str(entry.get("machine", "") or ""),
                str(entry.get("source_id", "") or ""),
                str(entry.get("source_type", "") or ""),
                str(entry.get("raw_line", "") or ""),
            ]
        )
        return hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()

    def insert_events(self, events: list[dict[str, Any]]) -> int:
        rows = []
        for item in events:
            if not isinstance(item, dict):
                continue
            event_time_utc = item.get("event_time_utc")
            if isinstance(event_time_utc, str):
                try:
                    event_time_utc = datetime.fromisoformat(event_time_utc.replace("Z", "+00:00"))
                except Exception:
                    event_time_utc = None
            if not isinstance(event_time_utc, datetime):
                event_time_utc = datetime.now(timezone.utc)
            rows.append(
                (
                    event_time_utc,
                    str(item.get("event_time_est", "") or ""),
                    str(item.get("severity", "info") or "info"),
                    str(item.get("description", "") or ""),
                    str(item.get("machine", "") or ""),
                    str(item.get("source_id", "") or ""),
                    str(item.get("source_type", "") or ""),
                    str(item.get("raw_line", "") or ""),
                    self._entry_hash(item),
                )
            )
        if not rows:
            return 0
        inserted = 0
        with self._connect() as conn:
            with conn.cursor() as cur:
                for row in rows:
                    cur.execute(
                        """
INSERT INTO application_logs (
  event_time_utc, event_time_est, severity, description, machine, source_id, source_type, raw_line, entry_hash
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (entry_hash) DO NOTHING;
""",
                        row,
                    )
                    inserted += int(cur.rowcount or 0)
            conn.commit()
        return inserted

    def query_events(
        self,
        *,
        source_id: str = "",
        search: str = "",
        severity: str = "",
        machine: str = "",
        limit: int = 500,
        offset: int = 0,
        sort_dir: str = "desc",
    ) -> dict[str, Any]:
        clauses: list[str] = ["1=1"]
        params: list[Any] = []
        if source_id:
            clauses.append("source_id = %s")
            params.append(source_id)
        if severity:
            clauses.append("LOWER(severity) = %s")
            params.append(severity.lower())
        if machine:
            clauses.append("LOWER(machine) LIKE %s")
            params.append(f"%{machine.lower()}%")
        if search:
            clauses.append("(LOWER(description) LIKE %s OR LOWER(raw_line) LIKE %s)")
            needle = f"%{search.lower()}%"
            params.extend([needle, needle])
        where_sql = " AND ".join(clauses)
        order_sql = "DESC" if str(sort_dir or "").lower() != "asc" else "ASC"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM application_logs WHERE {where_sql};", tuple(params))
                total = int((cur.fetchone() or [0])[0] or 0)
                cur.execute(
                    f"""
SELECT event_time_utc, event_time_est, severity, description, machine, source_id, source_type, raw_line
FROM application_logs
WHERE {where_sql}
ORDER BY event_time_utc {order_sql}
OFFSET %s LIMIT %s;
""",
                    tuple([*params, int(offset), int(limit)]),
                )
                rows = cur.fetchall()
        out = []
        for row in rows:
            out.append(
                {
                    "event_time_utc": row[0].isoformat() if isinstance(row[0], datetime) else str(row[0] or ""),
                    "event_time_est": str(row[1] or ""),
                    "severity": str(row[2] or "info"),
                    "description": str(row[3] or ""),
                    "machine": str(row[4] or ""),
                    "source_id": str(row[5] or ""),
                    "source_type": str(row[6] or ""),
                    "raw_line": str(row[7] or ""),
                }
            )
        return {"total": total, "offset": int(offset), "limit": int(limit), "events": out}
