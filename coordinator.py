#!/usr/bin/env python3
"""Distributed worker coordinator for Nightmare/Fozzy/Extractor.

This process runs on worker VMs and coordinates work through the central server APIs:
- claims next Nightmare target,
- heartbeats active leases,
- checkpoints crawl session state to Postgres via server APIs,
- enqueues and runs Fozzy + Extractor stage jobs,
- uploads key artifacts so any VM can resume the pipeline,
- polls fleet output_clear_generation and wipes local output/ when the operator bumps it on the central host.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

from http_client import request_json

from output_cleanup import FLEET_GEN_APPLIED_FILENAME, clear_output_root_children
from nightmare_shared.config import CoordinatorSettings, atomic_write_json, load_env_file_into_os, merged_value, read_json_dict, safe_float, safe_int
from nightmare_shared.error_reporting import install_error_reporting, report_error
from nightmare_shared.logging_utils import configure_logging, get_logger
from coordinator_app.runtime import CoordinatorClient, CoordinatorConfig, SessionUploader, LeaseHeartbeat, _zip_directory_bytes, _unzip_bytes_to_directory, run_subprocess, summarize_subprocess_failure, load_config

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH_DEFAULT = BASE_DIR / "config" / "coordinator.json"
OUTPUT_ROOT_DEFAULT = BASE_DIR / "output"


def _read_json_dict(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8-sig")
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _merged_value(cli_value: Any, cfg: dict[str, Any], key: str, default: Any) -> Any:
    if cli_value is not None:
        return cli_value
    if key in cfg:
        value = cfg[key]
        if value is not None and value != "":
            return value
    return default


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _domain_output_dir(root_domain: str, output_root: Path) -> Path:
    return output_root / root_domain


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _default_workflow_entries() -> list[dict[str, Any]]:
    return [
        {
            "name": "nightmare_crawl_core",
            "display_name": "Nightmare Crawl Core",
            "description": "Marks crawl completion once target queue intake finishes for a domain.",
            "handler": "nightmare_artifact_gate",
            "config_schema": {"type": "object", "additionalProperties": True},
            "stage": "nightmare_crawl_core",
            "enabled": True,
            "prerequisites": {"target_statuses": ["completed"]},
            "retry_failed": False,
            "max_attempts": 1,
            "resume_mode": "exact",
            "parameters": {},
        },
        {
            "name": "nightmare_ai_discovery",
            "display_name": "Nightmare AI Discovery",
            "description": "Marks AI discovery-ready state once nightmare session data exists.",
            "handler": "nightmare_artifact_gate",
            "config_schema": {"type": "object", "additionalProperties": True},
            "stage": "nightmare_ai_discovery",
            "enabled": True,
            "prerequisites": {
                "artifacts_all": ["nightmare_session_json"],
                "requires_plugins_all": ["nightmare_crawl_core"],
            },
            "retry_failed": False,
            "max_attempts": 1,
            "resume_mode": "exact",
            "parameters": {},
        },
        {
            "name": "nightmare_ai_probe_execution",
            "display_name": "Nightmare AI Probe Execution",
            "description": "Marks probe execution readiness once URL inventory exists.",
            "handler": "nightmare_artifact_gate",
            "config_schema": {"type": "object", "additionalProperties": True},
            "stage": "nightmare_ai_probe_execution",
            "enabled": True,
            "prerequisites": {
                "artifacts_all": ["nightmare_url_inventory_json"],
                "requires_plugins_all": ["nightmare_ai_discovery"],
            },
            "retry_failed": False,
            "max_attempts": 1,
            "resume_mode": "exact",
            "parameters": {},
        },
        {
            "name": "nightmare_url_verification",
            "display_name": "Nightmare URL Verification",
            "description": "Marks URL verification readiness once request inventory exists.",
            "handler": "nightmare_artifact_gate",
            "config_schema": {"type": "object", "additionalProperties": True},
            "stage": "nightmare_url_verification",
            "enabled": True,
            "prerequisites": {
                "artifacts_all": ["nightmare_requests_json"],
                "requires_plugins_all": ["nightmare_ai_probe_execution"],
            },
            "retry_failed": False,
            "max_attempts": 1,
            "resume_mode": "exact",
            "parameters": {},
        },
        {
            "name": "nightmare_inventory_export",
            "display_name": "Nightmare Inventory Export",
            "description": "Marks export readiness once nightmare parameter inventory exists.",
            "handler": "nightmare_artifact_gate",
            "config_schema": {"type": "object", "additionalProperties": True},
            "stage": "nightmare_inventory_export",
            "enabled": True,
            "prerequisites": {
                "artifacts_all": ["nightmare_parameters_json"],
                "requires_plugins_all": ["nightmare_url_verification"],
            },
            "retry_failed": False,
            "max_attempts": 1,
            "resume_mode": "exact",
            "parameters": {},
        },
        {
            "name": "nightmare_report_generation",
            "display_name": "Nightmare Report Generation",
            "description": "Marks report generation readiness once report artifact exists.",
            "handler": "nightmare_artifact_gate",
            "config_schema": {"type": "object", "additionalProperties": True},
            "stage": "nightmare_report_generation",
            "enabled": True,
            "prerequisites": {
                "artifacts_all": ["nightmare_report_html"],
                "requires_plugins_all": ["nightmare_inventory_export"],
            },
            "retry_failed": False,
            "max_attempts": 1,
            "resume_mode": "exact",
            "parameters": {},
        },
        {
            "name": "auth0r",
            "display_name": "Auth0r",
            "description": "Run auth0r once nightmare session data is available.",
            "handler": "legacy_step_adapter",
            "config_schema": {"type": "object", "additionalProperties": True},
            "stage": "auth0r",
            "enabled": True,
            "prerequisites": {
                "artifacts_all": ["nightmare_session_json"],
                "requires_plugins_all": ["nightmare_crawl_core"],
            },
            "retry_failed": False,
            "max_attempts": 1,
            "resume_mode": "exact",
            "parameters": {
                "min_delay_seconds": 0.25,
                "max_seed_actions": 200,
                "timeout_seconds": 20.0,
            },
        },
        {
            "name": "fozzy",
            "display_name": "Fozzy",
            "description": "Run fozzy once nightmare parameter data is available.",
            "handler": "legacy_step_adapter",
            "config_schema": {"type": "object", "additionalProperties": True},
            "stage": "fozzy",
            "enabled": True,
            "prerequisites": {
                "artifacts_all": ["nightmare_parameters_json"],
                "requires_plugins_all": ["nightmare_inventory_export"],
            },
            "retry_failed": False,
            "max_attempts": 1,
            "resume_mode": "exact",
            "parameters": {
                "max_background_workers": 1,
                "max_workers_per_domain": 1,
                "max_workers_per_subdomain": 1,
            },
        },
        {
            "name": "extractor",
            "display_name": "Extractor",
            "description": "Run extractor after fozzy output exists.",
            "handler": "legacy_step_adapter",
            "config_schema": {"type": "object", "additionalProperties": True},
            "stage": "extractor",
            "enabled": True,
            "prerequisites": {
                "artifacts_all": ["fozzy_results_zip"],
                "requires_plugins_all": ["fozzy"],
            },
            "retry_failed": False,
            "max_attempts": 1,
            "resume_mode": "exact",
            "parameters": {"force": True},
        },
    ]


def _normalize_workflow_entry(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or raw.get("type") or raw.get("plugin_name") or raw.get("stage") or "").strip().lower()
    plugin_name = str(raw.get("plugin_name") or raw.get("stage") or raw.get("name") or name).strip().lower()
    if not plugin_name:
        return None
    prereq = raw.get("prerequisites", {})
    if not isinstance(prereq, dict):
        prereq = {}
    artifacts_all = prereq.get("artifacts_all", raw.get("requires_artifacts_all", []))
    artifacts_any = prereq.get("artifacts_any", raw.get("requires_artifacts_any", []))
    requires_plugins_all = prereq.get("requires_plugins_all", prereq.get("plugins_all", []))
    requires_plugins_any = prereq.get("requires_plugins_any", prereq.get("plugins_any", []))
    target_statuses = prereq.get("target_statuses", raw.get("target_statuses", []))
    require_target_completed = bool(
        prereq.get("require_target_completed", raw.get("require_target_completed", False))
    )
    params = raw.get("parameters", raw.get("config", {}))
    return {
        "name": name or plugin_name,
        "display_name": str(raw.get("display_name") or raw.get("name") or plugin_name).strip(),
        "description": str(raw.get("description") or "").strip(),
        "handler": str(raw.get("handler") or "legacy_step_adapter").strip().lower(),
        "config_schema": (
            dict(raw.get("config_schema"))
            if isinstance(raw.get("config_schema"), dict)
            else {"type": "object", "additionalProperties": True}
        ),
        "stage": plugin_name,
        "plugin_name": plugin_name,
        "enabled": bool(raw.get("enabled", True)),
        "prerequisites": {
            "artifacts_all": [str(item).strip().lower() for item in (artifacts_all if isinstance(artifacts_all, list) else []) if str(item).strip()],
            "artifacts_any": [str(item).strip().lower() for item in (artifacts_any if isinstance(artifacts_any, list) else []) if str(item).strip()],
            "requires_plugins_all": [str(item).strip().lower() for item in (requires_plugins_all if isinstance(requires_plugins_all, list) else []) if str(item).strip()],
            "requires_plugins_any": [str(item).strip().lower() for item in (requires_plugins_any if isinstance(requires_plugins_any, list) else []) if str(item).strip()],
            "target_statuses": [str(item).strip().lower() for item in (target_statuses if isinstance(target_statuses, list) else []) if str(item).strip()],
            "require_target_completed": bool(require_target_completed),
        },
        "retry_failed": bool(raw.get("retry_failed", False)),
        "max_attempts": max(0, _safe_int(raw.get("max_attempts", 0), 0)),
        "inputs": dict(raw.get("inputs") or {}) if isinstance(raw.get("inputs"), dict) else {},
        "outputs": dict(raw.get("outputs") or {}) if isinstance(raw.get("outputs"), dict) else {},
        "resume_mode": str(raw.get("resume_mode") or "exact").strip().lower() or "exact",
        "parameters": dict(params) if isinstance(params, dict) else {},
    }


def _load_workflow_entries(path: Path, logger: Any) -> tuple[str, list[dict[str, Any]]]:
    payload = _read_json_dict(path)
    workflow_id = str(payload.get("workflow_id") or payload.get("id") or "default").strip().lower() or "default"
    candidates: list[Any] = []
    if isinstance(payload.get("plugins"), list):
        candidates = list(payload.get("plugins") or [])
    elif isinstance(payload.get("stages"), list):
        candidates = list(payload.get("stages") or [])
    elif isinstance(payload.get("steps"), list):
        candidates = list(payload.get("steps") or [])
    if not candidates:
        logger.warning(
            "workflow_config_missing_or_empty_using_defaults",
            workflow_config=str(path),
        )
        return workflow_id, _default_workflow_entries()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in candidates:
        normalized = _normalize_workflow_entry(raw)
        if not normalized:
            continue
        plugin_name = str(normalized.get("plugin_name") or normalized.get("stage") or "").strip().lower()
        if not plugin_name or plugin_name in seen:
            continue
        seen.add(plugin_name)
        out.append(normalized)
    if not out:
        logger.warning(
            "workflow_config_entries_invalid_using_defaults",
            workflow_config=str(path),
        )
        return workflow_id, _default_workflow_entries()
    return workflow_id, out


class DistributedCoordinator:
    def __init__(self, cfg: CoordinatorConfig, *, logger: Any | None = None):
        self.cfg = cfg
        # Allow opt-out for local/self-signed coordinator deployments.
        verify_ssl = not bool(cfg.insecure_tls)
        self.client = CoordinatorClient(cfg.server_base_url, cfg.api_token, verify_ssl=verify_ssl)
        host = socket.gethostname().strip() or "worker"
        self.worker_prefix = f"{host}-{uuid.uuid4().hex[:8]}"
        base_logger = logger or get_logger("coordinator_runner", component="distributed_coordinator")
        self.logger = base_logger.bind(worker_prefix=self.worker_prefix)
        self.stop_event = threading.Event()
        self._job_lock = threading.Lock()
        self._active_jobs = 0
        self._fleet_lock = threading.Lock()
        self._worker_state_lock = threading.Lock()
        self._worker_states: dict[str, str] = {}
        self._workflow_id, self._workflow_entries = _load_workflow_entries(self.cfg.workflow_config, self.logger)
        self._workflow_stage_map: dict[str, dict[str, Any]] = {
            str(item.get("plugin_name") or item.get("stage") or "").strip().lower(): item
            for item in self._workflow_entries
        }
        self.logger.info(
            "workflow_scheduler_config_loaded",
            workflow_config=str(self.cfg.workflow_config),
            workflow_id=self._workflow_id,
            workflow_scheduler_enabled=bool(self.cfg.workflow_scheduler_enabled),
            workflow_scheduler_interval_seconds=float(self.cfg.workflow_scheduler_interval_seconds),
            workflow_stage_count=len(self._workflow_entries),
            workflow_stages=[str(item.get("plugin_name") or item.get("stage") or "") for item in self._workflow_entries],
        )

    def _begin_job(self) -> None:
        with self._job_lock:
            self._active_jobs += 1

    def _end_job(self) -> None:
        with self._job_lock:
            self._active_jobs = max(0, self._active_jobs - 1)

    def _set_worker_state(self, worker_id: str, state: str) -> None:
        safe_state = str(state or "").strip().lower()
        if safe_state not in {"running", "paused", "stopped", "errored", "idle"}:
            safe_state = "idle"
        with self._worker_state_lock:
            self._worker_states[str(worker_id)] = safe_state

    def _get_worker_state(self, worker_id: str) -> str:
        with self._worker_state_lock:
            return str(self._worker_states.get(str(worker_id), "running") or "running")

    def _poll_worker_commands(self, worker_id: str) -> str:
        state = self._get_worker_state(worker_id)
        while not self.stop_event.is_set():
            try:
                command_entry = self.client.claim_worker_command(worker_id, worker_state=state)
            except Exception as exc:
                self.logger.error(
                    "worker_command_poll_failed",
                    worker_id=worker_id,
                    worker_state=state,
                    error=str(exc),
                )
                return state
            if not command_entry:
                return state

            command_id = _safe_int(command_entry.get("id", 0), 0)
            command = str(command_entry.get("command", "") or "").strip().lower()
            self.logger.info(
                "worker_command_claimed",
                worker_id=worker_id,
                command_id=command_id,
                command=command,
                prior_state=state,
            )
            ok = True
            err = ""
            if command == "pause":
                state = "paused"
            elif command == "stop":
                state = "stopped"
            elif command == "start":
                state = "running"
            else:
                ok = False
                err = f"unsupported command: {command!r}"
                state = "errored"
            self._set_worker_state(worker_id, state)
            self.logger.info(
                "worker_command_applied",
                worker_id=worker_id,
                command_id=command_id,
                command=command,
                success=ok,
                error=err,
                next_state=state,
            )
            if command_id > 0:
                try:
                    self.client.complete_worker_command(worker_id, command_id, success=ok, error=err)
                except Exception as exc:
                    self.logger.error(
                        "worker_command_complete_failed",
                        worker_id=worker_id,
                        command_id=command_id,
                        command=command,
                        error=str(exc),
                    )
            if not ok:
                return state

    def _read_local_fleet_generation(self) -> int:
        path = self.cfg.output_root / FLEET_GEN_APPLIED_FILENAME
        try:
            text = path.read_text(encoding="utf-8").strip()
            return max(0, int((text.splitlines() or ["0"])[0]))
        except Exception:
            return 0

    def _write_local_fleet_generation(self, generation: int) -> None:
        path = self.cfg.output_root / FLEET_GEN_APPLIED_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{max(0, int(generation))}\n", encoding="utf-8")

    def _maybe_apply_fleet_output_clear(self) -> None:
        """When central bumps output_clear_generation, delete local output/ children (best-effort).

        Intended for drained or idle periods; clearing while jobs run can delete active work.
        """
        with self._fleet_lock:
            with self._job_lock:
                if self._active_jobs > 0:
                    return
            try:
                remote = int(self.client.get_fleet_settings().get("output_clear_generation", 0))
            except Exception as exc:
                self.logger.error(
                    "fleet_settings_fetch_failed",
                    output_root=str(self.cfg.output_root),
                    error=str(exc),
                )
                return
            local = self._read_local_fleet_generation()
            if remote <= local:
                return
            with self._job_lock:
                if self._active_jobs > 0:
                    return
            self.logger.warning(
                "fleet_output_clear_generation_applied",
                previous_generation=local,
                next_generation=remote,
                output_root=str(self.cfg.output_root),
            )
            clear_output_root_children(
                self.cfg.output_root,
                preserve_names=frozenset({FLEET_GEN_APPLIED_FILENAME}),
            )
            self._write_local_fleet_generation(remote)

    def _is_stage_runtime_enabled(self, stage: str) -> bool:
        stg = str(stage or "").strip().lower()
        if stg.startswith("nightmare_"):
            # These are workflow gate/milestone plugins, not direct crawler workers.
            return True
        if stg == "fozzy":
            return bool(self.cfg.enable_fozzy)
        if stg == "extractor":
            return bool(self.cfg.enable_extractor)
        if stg == "auth0r":
            return bool(self.cfg.enable_auth0r)
        return True

    def _workflow_stage_parameters(self, stage: str) -> dict[str, Any]:
        entry = self._workflow_stage_map.get(str(stage or "").strip().lower(), {})
        params = entry.get("parameters") if isinstance(entry, dict) else {}
        return dict(params) if isinstance(params, dict) else {}

    @staticmethod
    def _has_stage_prerequisites(
        artifacts: set[str],
        entry: dict[str, Any],
        *,
        workflow_tasks: dict[str, dict[str, Any]],
        target_counts: dict[str, Any],
    ) -> bool:
        prereq = entry.get("prerequisites") if isinstance(entry, dict) else {}
        if not isinstance(prereq, dict):
            prereq = {}
        required_all = {
            str(item or "").strip().lower()
            for item in (prereq.get("artifacts_all") or [])
            if str(item or "").strip()
        }
        required_any = {
            str(item or "").strip().lower()
            for item in (prereq.get("artifacts_any") or [])
            if str(item or "").strip()
        }
        required_plugins_all = {
            str(item or "").strip().lower()
            for item in (prereq.get("requires_plugins_all") or [])
            if str(item or "").strip()
        }
        required_plugins_any = {
            str(item or "").strip().lower()
            for item in (prereq.get("requires_plugins_any") or [])
            if str(item or "").strip()
        }
        required_target_statuses = {
            str(item or "").strip().lower()
            for item in (prereq.get("target_statuses") or [])
            if str(item or "").strip()
        }
        require_target_completed = bool(prereq.get("require_target_completed", False))

        def _task_status(name: str) -> str:
            row = workflow_tasks.get(name) if isinstance(workflow_tasks.get(name), dict) else {}
            return str(row.get("status") or "").strip().lower()

        pending_targets = _safe_int(target_counts.get("pending"), 0)
        running_targets = _safe_int(target_counts.get("running"), 0)
        completed_targets = _safe_int(target_counts.get("completed"), 0)
        failed_targets = _safe_int(target_counts.get("failed"), 0)
        if running_targets > 0:
            current_target_status = "running"
        elif pending_targets > 0:
            current_target_status = "pending"
        elif failed_targets > 0 and completed_targets <= 0:
            current_target_status = "failed"
        elif completed_targets > 0:
            current_target_status = "completed"
        else:
            current_target_status = "unknown"

        if required_all and not required_all.issubset(artifacts):
            return False
        if required_any and artifacts.isdisjoint(required_any):
            return False
        if required_plugins_all and any(_task_status(item) != "completed" for item in required_plugins_all):
            return False
        if required_plugins_any and not any(_task_status(item) == "completed" for item in required_plugins_any):
            return False
        if required_target_statuses and current_target_status not in required_target_statuses:
            return False
        if require_target_completed and completed_targets <= 0:
            return False
        return True

    def _schedule_domain_workflows(self, domain_row: dict[str, Any], *, worker_id: str) -> int:
        root_domain = str(domain_row.get("root_domain", "") or "").strip().lower()
        if not root_domain:
            return 0
        artifacts = {
            str(item or "").strip().lower()
            for item in (domain_row.get("artifact_types") if isinstance(domain_row.get("artifact_types"), list) else [])
            if str(item or "").strip()
        }
        plugin_tasks_all = domain_row.get("plugin_tasks") if isinstance(domain_row.get("plugin_tasks"), dict) else {}
        workflow_tasks = plugin_tasks_all.get(self._workflow_id) if isinstance(plugin_tasks_all.get(self._workflow_id), dict) else {}
        target_counts = domain_row.get("targets") if isinstance(domain_row.get("targets"), dict) else {}
        scheduled_count = 0
        for entry in self._workflow_entries:
            plugin_name = str(entry.get("plugin_name") or entry.get("stage") or "").strip().lower()
            if not plugin_name or not bool(entry.get("enabled", True)):
                continue
            if not self._is_stage_runtime_enabled(plugin_name):
                continue
            if not self._has_stage_prerequisites(
                artifacts,
                entry,
                workflow_tasks=workflow_tasks,
                target_counts=target_counts,
            ):
                continue
            task_row = workflow_tasks.get(plugin_name) if isinstance(workflow_tasks.get(plugin_name), dict) else {}
            status = str(task_row.get("status", "") or "").strip().lower()
            attempt_count = _safe_int(task_row.get("attempt_count", 0), 0)
            retry_failed = bool(entry.get("retry_failed", False))
            max_attempts = max(0, _safe_int(entry.get("max_attempts", 0), 0))
            if status in {"pending", "running", "completed", "failed"}:
                continue
            allow_retry_failed = False
            resume_mode = str(entry.get("resume_mode") or "exact").strip().lower() or "exact"
            details = self.client.enqueue_stage_detailed(
                root_domain,
                plugin_name,
                workflow_id=self._workflow_id,
                worker_id=worker_id,
                reason=f"workflow:{entry.get('name', plugin_name)}",
                allow_retry_failed=allow_retry_failed,
                max_attempts=max_attempts,
                checkpoint={"schema_version": 1, "resume_mode": resume_mode, "state": "queued"},
                progress={"status": "queued", "plugin_name": plugin_name},
                progress_artifact_type=f"workflow_progress_{plugin_name}",
                resume_mode=resume_mode,
            )
            if bool(details.get("scheduled")):
                scheduled_count += 1
                self.logger.info(
                    "workflow_task_scheduled",
                    worker_id=worker_id,
                    workflow_id=self._workflow_id,
                    root_domain=root_domain,
                    stage=plugin_name,
                    prior_status=status or "none",
                    attempt_count=attempt_count,
                    max_attempts=max_attempts,
                    retry_failed=retry_failed,
                    artifacts_available=sorted(artifacts),
                )
        return scheduled_count

    def _workflow_scheduler_loop(self) -> None:
        worker_id = f"{self.worker_prefix}-scheduler-1"
        self._set_worker_state(worker_id, "running")
        self.logger.info(
            "workflow_scheduler_loop_started",
            worker_id=worker_id,
            workflow_id=self._workflow_id,
            workflow_config=str(self.cfg.workflow_config),
            interval_seconds=float(self.cfg.workflow_scheduler_interval_seconds),
        )
        while not self.stop_event.is_set():
            state = self._poll_worker_commands(worker_id)
            if state in {"paused", "stopped", "errored"}:
                self.logger.warning(
                    "workflow_scheduler_waiting_due_to_state",
                    worker_id=worker_id,
                    worker_state=state,
                    poll_interval_seconds=self.cfg.workflow_scheduler_interval_seconds,
                )
                self.stop_event.wait(self.cfg.workflow_scheduler_interval_seconds)
                continue
            try:
                snapshot = self.client.get_workflow_snapshot(limit=5000)
                domain_rows = snapshot.get("domains") if isinstance(snapshot.get("domains"), list) else []
            except Exception as exc:
                self.logger.error("workflow_scheduler_snapshot_failed", worker_id=worker_id, error=str(exc))
                self.stop_event.wait(self.cfg.workflow_scheduler_interval_seconds)
                continue
            scheduled = 0
            for row in domain_rows:
                if not isinstance(row, dict):
                    continue
                try:
                    scheduled += self._schedule_domain_workflows(row, worker_id=worker_id)
                except Exception as exc:
                    self.logger.error(
                        "workflow_scheduler_domain_failed",
                        worker_id=worker_id,
                        root_domain=str(row.get("root_domain", "") or "").strip().lower(),
                        error=str(exc),
                    )
            self.logger.info(
                "workflow_scheduler_cycle_complete",
                worker_id=worker_id,
                workflow_id=self._workflow_id,
                domains_checked=len(domain_rows),
                tasks_scheduled=scheduled,
            )
            self.stop_event.wait(self.cfg.workflow_scheduler_interval_seconds)

    def _artifact_paths(self, root_domain: str) -> dict[str, Path]:
        domain_dir = _domain_output_dir(root_domain, self.cfg.output_root)
        fozzy_domain_dir = domain_dir / "fozzy-output" / root_domain
        high_value_dir = self.cfg.output_root / "high_value" / root_domain
        return {
            "nightmare_session_json": domain_dir / f"{root_domain}_crawl_session.json",
            "nightmare_url_inventory_json": domain_dir / f"{root_domain}_url_inventory.json",
            "nightmare_requests_json": domain_dir / "requests.json",
            "nightmare_parameters_json": domain_dir / f"{root_domain}.parameters.json",
            "nightmare_post_requests_json": domain_dir / f"{root_domain}.post.requests.json",
            "nightmare_parameters_txt": domain_dir / f"{root_domain}.parameters.txt",
            "nightmare_source_of_truth_json": domain_dir / f"{root_domain}_source_of_truth.json",
            "nightmare_report_html": domain_dir / "report.html",
            "nightmare_log": domain_dir / f"{root_domain}_nightmare.log",
            "nightmare_scrapy_log": domain_dir / f"{root_domain}_scrapy.log",
            "fozzy_summary_json": fozzy_domain_dir / f"{root_domain}.fozzy.summary.json",
            "fozzy_inventory_json": fozzy_domain_dir / f"{root_domain}.fozzy.inventory.json",
            "fozzy_log": fozzy_domain_dir / f"{root_domain}.fozzy.log",
            "fozzy_results_dir": fozzy_domain_dir / "results",
            "extractor_summary_json": fozzy_domain_dir / "extractor" / "summary.json",
            "extractor_matches_dir": fozzy_domain_dir / "extractor" / "matches",
            "auth0r_summary_json": domain_dir / "auth0r.summary.json",
            "auth0r_log": domain_dir / "coordinator.auth0r.log",
            "nightmare_high_value_dir": high_value_dir,
        }

    def _upload_file_artifact(self, root_domain: str, artifact_type: str, path: Path, worker_id: str) -> None:
        if not path.is_file():
            self.logger.info(
                "artifact_file_not_found",
                root_domain=root_domain,
                worker_id=worker_id,
                artifact_type=artifact_type,
                artifact_path=str(path),
            )
            return
        content = path.read_bytes()
        self.logger.info(
            "artifact_file_upload_start",
            root_domain=root_domain,
            worker_id=worker_id,
            artifact_type=artifact_type,
            artifact_path=str(path),
            bytes=len(content),
        )
        self.client.upload_artifact(root_domain, artifact_type, content, source_worker=worker_id)
        self.logger.info(
            "artifact_file_upload_complete",
            root_domain=root_domain,
            worker_id=worker_id,
            artifact_type=artifact_type,
            artifact_path=str(path),
            bytes=len(content),
        )

    def _upload_zip_artifact(self, root_domain: str, artifact_type: str, path: Path, worker_id: str) -> None:
        if not path.is_dir():
            self.logger.info(
                "artifact_zip_source_not_found",
                root_domain=root_domain,
                worker_id=worker_id,
                artifact_type=artifact_type,
                source_path=str(path),
            )
            return
        self.logger.info(
            "artifact_zip_upload_start",
            root_domain=root_domain,
            worker_id=worker_id,
            artifact_type=artifact_type,
            source_path=str(path),
        )
        payload = _zip_directory_bytes(path)
        self.client.upload_artifact(
            root_domain,
            artifact_type,
            payload,
            source_worker=worker_id,
            content_encoding="zip",
        )
        self.logger.info(
            "artifact_zip_upload_complete",
            root_domain=root_domain,
            worker_id=worker_id,
            artifact_type=artifact_type,
            source_path=str(path),
            bytes=len(payload),
        )

    def _download_file_artifact(self, root_domain: str, artifact_type: str, path: Path) -> bool:
        self.logger.info(
            "artifact_file_download_start",
            root_domain=root_domain,
            artifact_type=artifact_type,
            target_path=str(path),
        )
        artifact = self.client.download_artifact(root_domain, artifact_type)
        if artifact is None:
            self.logger.warning(
                "artifact_file_download_missing",
                root_domain=root_domain,
                artifact_type=artifact_type,
                target_path=str(path),
            )
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        content = bytes(artifact["content"])
        path.write_bytes(content)
        self.logger.info(
            "artifact_file_download_complete",
            root_domain=root_domain,
            artifact_type=artifact_type,
            target_path=str(path),
            bytes=len(content),
        )
        return True

    def _download_zip_artifact(self, root_domain: str, artifact_type: str, target_dir: Path) -> bool:
        self.logger.info(
            "artifact_zip_download_start",
            root_domain=root_domain,
            artifact_type=artifact_type,
            target_dir=str(target_dir),
        )
        artifact = self.client.download_artifact(root_domain, artifact_type)
        if artifact is None:
            self.logger.warning(
                "artifact_zip_download_missing",
                root_domain=root_domain,
                artifact_type=artifact_type,
                target_dir=str(target_dir),
            )
            return False
        content = bytes(artifact["content"])
        _unzip_bytes_to_directory(content, target_dir)
        self.logger.info(
            "artifact_zip_download_complete",
            root_domain=root_domain,
            artifact_type=artifact_type,
            target_dir=str(target_dir),
            bytes=len(content),
        )
        return True

    def _nightmare_worker_loop(self, idx: int) -> None:
        worker_id = f"{self.worker_prefix}-nightmare-{idx}"
        self._set_worker_state(worker_id, "running")
        self.logger.info("nightmare_worker_loop_started", worker_id=worker_id, worker_index=idx)
        while not self.stop_event.is_set():
            state = self._poll_worker_commands(worker_id)
            if state in {"paused", "stopped", "errored"}:
                self.logger.warning(
                    "nightmare_worker_waiting_due_to_state",
                    worker_id=worker_id,
                    worker_state=state,
                    poll_interval_seconds=self.cfg.poll_interval_seconds,
                )
                self.stop_event.wait(self.cfg.poll_interval_seconds)
                continue
            self._maybe_apply_fleet_output_clear()
            try:
                entry = self.client.claim_target(worker_id, self.cfg.lease_seconds)
            except Exception as exc:
                self.logger.error("nightmare_claim_failed", worker_id=worker_id, error=str(exc))
                self.stop_event.wait(self.cfg.poll_interval_seconds)
                continue
            if not entry:
                self.logger.info("nightmare_claim_empty", worker_id=worker_id)
                self.stop_event.wait(self.cfg.poll_interval_seconds)
                continue

            entry_id = str(entry.get("entry_id", "") or "")
            start_url = str(entry.get("start_url", "") or "")
            root_domain = str(entry.get("root_domain", "") or "").strip().lower()
            if not entry_id or not start_url or not root_domain:
                self.logger.error(
                    "nightmare_claim_invalid_entry",
                    worker_id=worker_id,
                    entry=entry,
                )
                self.stop_event.wait(1.0)
                continue
            self._begin_job()
            try:
                self.logger.info(
                    "nightmare_job_start",
                    worker_id=worker_id,
                    entry_id=entry_id,
                    root_domain=root_domain,
                    start_url=start_url,
                )
                paths = self._artifact_paths(root_domain)
                domain_dir = _domain_output_dir(root_domain, self.cfg.output_root)
                domain_dir.mkdir(parents=True, exist_ok=True)

                try:
                    remote_session = self.client.load_session(root_domain)
                    if isinstance(remote_session, dict) and remote_session:
                        session_payload = dict(remote_session.get("payload", remote_session))
                        if session_payload:
                            _atomic_write_json(paths["nightmare_session_json"], session_payload)
                            self.logger.info(
                                "nightmare_session_restored",
                                worker_id=worker_id,
                                entry_id=entry_id,
                                root_domain=root_domain,
                                session_path=str(paths["nightmare_session_json"]),
                            )
                except Exception as exc:
                    self.logger.error(
                        "nightmare_session_restore_failed",
                        worker_id=worker_id,
                        entry_id=entry_id,
                        root_domain=root_domain,
                        error=str(exc),
                    )

                heartbeat = LeaseHeartbeat(
                    tick_fn=lambda: self.client.heartbeat_target(entry_id, worker_id, self.cfg.lease_seconds),
                    interval_seconds=self.cfg.heartbeat_interval_seconds,
                    logger=self.logger,
                    heartbeat_kind="nightmare_target",
                )
                heartbeat.start()

                uploader_stop = threading.Event()
                session_uploader = SessionUploader(
                    self.client,
                    root_domain=root_domain,
                    session_path=paths["nightmare_session_json"],
                    interval_seconds=self.cfg.upload_session_every_seconds,
                    stop_event=uploader_stop,
                )
                session_uploader.start()

                cmd = [
                    self.cfg.python_executable,
                    "nightmare.py",
                    start_url,
                    "--config",
                    str(self.cfg.nightmare_config),
                    "--resume",
                ]
                log_path = domain_dir / "coordinator.nightmare.log"
                exit_code = 1
                err_text = ""
                self.logger.info(
                    "nightmare_subprocess_start",
                    worker_id=worker_id,
                    entry_id=entry_id,
                    root_domain=root_domain,
                    command=cmd,
                    log_path=str(log_path),
                )
                try:
                    exit_code = run_subprocess(cmd, cwd=BASE_DIR, log_path=log_path)
                    if exit_code != 0:
                        err_text = summarize_subprocess_failure("nightmare", log_path, exit_code)
                    self.logger.info(
                        "nightmare_subprocess_complete",
                        worker_id=worker_id,
                        entry_id=entry_id,
                        root_domain=root_domain,
                        exit_code=exit_code,
                        error=err_text,
                    )
                except Exception as exc:
                    err_text = str(exc)
                    exit_code = 1
                    self.logger.error(
                        "nightmare_subprocess_error",
                        worker_id=worker_id,
                        entry_id=entry_id,
                        root_domain=root_domain,
                        error=err_text,
                    )
                finally:
                    uploader_stop.set()
                    session_uploader.upload_once()
                    heartbeat.stop()

                try:
                    self._upload_file_artifact(root_domain, "nightmare_session_json", paths["nightmare_session_json"], worker_id)
                    self._upload_file_artifact(root_domain, "nightmare_url_inventory_json", paths["nightmare_url_inventory_json"], worker_id)
                    self._upload_file_artifact(root_domain, "nightmare_requests_json", paths["nightmare_requests_json"], worker_id)
                    self._upload_file_artifact(root_domain, "nightmare_parameters_json", paths["nightmare_parameters_json"], worker_id)
                    self._upload_file_artifact(
                        root_domain, "nightmare_post_requests_json", paths["nightmare_post_requests_json"], worker_id
                    )
                    self._upload_file_artifact(root_domain, "nightmare_parameters_txt", paths["nightmare_parameters_txt"], worker_id)
                    self._upload_file_artifact(root_domain, "nightmare_source_of_truth_json", paths["nightmare_source_of_truth_json"], worker_id)
                    self._upload_file_artifact(root_domain, "nightmare_report_html", paths["nightmare_report_html"], worker_id)
                    self._upload_file_artifact(root_domain, "nightmare_log", paths["nightmare_log"], worker_id)
                    self._upload_file_artifact(root_domain, "nightmare_scrapy_log", paths["nightmare_scrapy_log"], worker_id)
                    hv_dir = paths.get("nightmare_high_value_dir")
                    if hv_dir is not None and hv_dir.is_dir():
                        self._upload_zip_artifact(
                            root_domain, "nightmare_high_value_zip", hv_dir, worker_id
                        )
                except Exception as exc:
                    self.logger.error(
                        "nightmare_artifact_upload_failed",
                        worker_id=worker_id,
                        entry_id=entry_id,
                        root_domain=root_domain,
                        error=str(exc),
                    )

                if exit_code == 0 and not self.cfg.workflow_scheduler_enabled:
                    try:
                        self.client.enqueue_stage(root_domain, "auth0r", workflow_id=self._workflow_id)
                        self.logger.info(
                            "nightmare_stage_enqueued",
                            worker_id=worker_id,
                            entry_id=entry_id,
                            root_domain=root_domain,
                            stage="auth0r",
                        )
                    except Exception as exc:
                        self.logger.error(
                            "nightmare_stage_enqueue_failed",
                            worker_id=worker_id,
                            entry_id=entry_id,
                            root_domain=root_domain,
                            stage="auth0r",
                            error=str(exc),
                        )
                    try:
                        self.client.enqueue_stage(root_domain, "fozzy", workflow_id=self._workflow_id)
                        self.logger.info(
                            "nightmare_stage_enqueued",
                            worker_id=worker_id,
                            entry_id=entry_id,
                            root_domain=root_domain,
                            stage="fozzy",
                        )
                    except Exception as exc:
                        self.logger.error(
                            "nightmare_stage_enqueue_failed",
                            worker_id=worker_id,
                            entry_id=entry_id,
                            root_domain=root_domain,
                            stage="fozzy",
                            error=str(exc),
                        )
                try:
                    self.client.complete_target(entry_id, worker_id, exit_code, err_text)
                    self.logger.info(
                        "nightmare_job_complete",
                        worker_id=worker_id,
                        entry_id=entry_id,
                        root_domain=root_domain,
                        exit_code=exit_code,
                        error=err_text,
                    )
                except Exception as exc:
                    self.logger.error(
                        "nightmare_complete_failed",
                        worker_id=worker_id,
                        entry_id=entry_id,
                        root_domain=root_domain,
                        exit_code=exit_code,
                        error=str(exc),
                    )
            finally:
                self._end_job()

    def _update_plugin_progress(
        self,
        *,
        worker_id: str,
        workflow_id: str,
        root_domain: str,
        plugin_name: str,
        checkpoint: dict[str, Any],
        progress: dict[str, Any],
    ) -> None:
        try:
            self.client.update_stage_progress(
                worker_id=worker_id,
                workflow_id=workflow_id,
                root_domain=root_domain,
                stage=plugin_name,
                checkpoint=checkpoint,
                progress=progress,
                progress_artifact_type=f"workflow_progress_{plugin_name}",
            )
        except Exception as exc:
            self.logger.error(
                "workflow_task_progress_update_failed",
                worker_id=worker_id,
                workflow_id=workflow_id,
                root_domain=root_domain,
                stage=plugin_name,
                error=str(exc),
            )

    def _run_nightmare_artifact_gate_plugin(
        self,
        *,
        root_domain: str,
        plugin_name: str,
    ) -> tuple[int, str]:
        entry = self._workflow_stage_map.get(plugin_name, {})
        prereq = entry.get("prerequisites") if isinstance(entry, dict) else {}
        if not isinstance(prereq, dict):
            prereq = {}
        required_artifacts = [
            str(item or "").strip().lower()
            for item in (prereq.get("artifacts_all") or [])
            if str(item or "").strip()
        ]
        for artifact_type in required_artifacts:
            artifact = self.client.download_artifact(root_domain, artifact_type)
            if artifact is None:
                return 1, f"missing required artifact {artifact_type}"
        return 0, ""

    def _run_fozzy_plugin_task(
        self,
        *,
        worker_id: str,
        root_domain: str,
        plugin_name: str,
    ) -> tuple[int, str]:
        paths = self._artifact_paths(root_domain)
        params_path = paths["nightmare_parameters_json"]
        if not params_path.is_file():
            self._download_file_artifact(root_domain, "nightmare_parameters_json", params_path)
        if not params_path.is_file():
            return 1, "missing parameters artifact"

        hv_dir = paths.get("nightmare_high_value_dir")
        if hv_dir is not None:
            hv_dir.mkdir(parents=True, exist_ok=True)
            self._download_zip_artifact(root_domain, "nightmare_high_value_zip", hv_dir)
        self._download_file_artifact(root_domain, "nightmare_post_requests_json", paths["nightmare_post_requests_json"])

        fozzy_params = self._workflow_stage_parameters(plugin_name)
        max_background_workers = max(
            1,
            _safe_int(
                fozzy_params.get("max_background_workers", self.cfg.fozzy_process_workers),
                max(1, int(self.cfg.fozzy_process_workers)),
            ),
        )
        max_workers_per_domain = max(1, _safe_int(fozzy_params.get("max_workers_per_domain", 1), 1))
        max_workers_per_subdomain = max(1, _safe_int(fozzy_params.get("max_workers_per_subdomain", 1), 1))

        cmd = [
            self.cfg.python_executable,
            "fozzy.py",
            str(params_path),
            "--config",
            str(self.cfg.fozzy_config),
            "--max-background-workers",
            str(max_background_workers),
            "--max-workers-per-domain",
            str(max_workers_per_domain),
            "--max-workers-per-subdomain",
            str(max_workers_per_subdomain),
        ]
        log_path = _domain_output_dir(root_domain, self.cfg.output_root) / "coordinator.fozzy.log"
        self.logger.info(
            "fozzy_subprocess_start",
            worker_id=worker_id,
            root_domain=root_domain,
            stage=plugin_name,
            command=cmd,
            log_path=str(log_path),
        )
        try:
            exit_code = run_subprocess(cmd, cwd=BASE_DIR, log_path=log_path)
            err_text = summarize_subprocess_failure("fozzy", log_path, exit_code) if exit_code != 0 else ""
        except Exception as exc:
            return 1, str(exc)

        try:
            self._upload_file_artifact(root_domain, "fozzy_summary_json", paths["fozzy_summary_json"], worker_id)
            self._upload_file_artifact(root_domain, "fozzy_inventory_json", paths["fozzy_inventory_json"], worker_id)
            self._upload_file_artifact(root_domain, "fozzy_log", paths["fozzy_log"], worker_id)
            self._upload_zip_artifact(root_domain, "fozzy_results_zip", paths["fozzy_results_dir"], worker_id)
        except Exception as exc:
            self.logger.error(
                "fozzy_artifact_upload_failed",
                worker_id=worker_id,
                root_domain=root_domain,
                stage=plugin_name,
                error=str(exc),
            )
        return int(exit_code), str(err_text or "")

    def _run_auth0r_plugin_task(
        self,
        *,
        worker_id: str,
        root_domain: str,
        plugin_name: str,
    ) -> tuple[int, str]:
        paths = self._artifact_paths(root_domain)
        nightmare_session_path = paths["nightmare_session_json"]
        if not nightmare_session_path.is_file():
            self._download_file_artifact(root_domain, "nightmare_session_json", nightmare_session_path)
        if not nightmare_session_path.is_file():
            return 1, "missing nightmare session artifact"

        cmd = [
            self.cfg.python_executable,
            "auth0r.py",
            root_domain,
            "--nightmare-session",
            str(nightmare_session_path),
            "--summary-json",
            str(paths["auth0r_summary_json"]),
        ]
        auth0r_database_url = os.getenv("AUTH0R_DATABASE_URL", "") or os.getenv("DATABASE_URL", "") or os.getenv("COORDINATOR_DATABASE_URL", "")
        if auth0r_database_url:
            cmd.extend(["--database-url", auth0r_database_url])
        if not self.client.verify_ssl:
            cmd.append("--insecure-tls")
        auth0r_cfg = _read_json_dict(self.cfg.auth0r_config)
        auth0r_params = self._workflow_stage_parameters(plugin_name)
        min_delay = auth0r_params.get("min_delay_seconds", auth0r_cfg.get("min_delay_seconds", 0.25))
        max_seed_actions = auth0r_params.get("max_seed_actions", auth0r_cfg.get("max_seed_actions", 200))
        timeout_seconds = auth0r_params.get("timeout_seconds", auth0r_cfg.get("timeout_seconds", 20.0))
        cmd.extend(["--min-delay-seconds", str(min_delay)])
        cmd.extend(["--max-seed-actions", str(max_seed_actions)])
        cmd.extend(["--timeout-seconds", str(timeout_seconds)])
        log_path = paths["auth0r_log"]
        self.logger.info(
            "auth0r_subprocess_start",
            worker_id=worker_id,
            root_domain=root_domain,
            stage=plugin_name,
            command=cmd,
            log_path=str(log_path),
        )
        try:
            exit_code = run_subprocess(cmd, cwd=BASE_DIR, log_path=log_path)
            err_text = summarize_subprocess_failure("auth0r", log_path, exit_code) if exit_code != 0 else ""
        except Exception as exc:
            return 1, str(exc)
        try:
            self._upload_file_artifact(root_domain, "auth0r_summary_json", paths["auth0r_summary_json"], worker_id)
            self._upload_file_artifact(root_domain, "auth0r_log", paths["auth0r_log"], worker_id)
        except Exception as exc:
            self.logger.error(
                "auth0r_artifact_upload_failed",
                worker_id=worker_id,
                root_domain=root_domain,
                stage=plugin_name,
                error=str(exc),
            )
        return int(exit_code), str(err_text or "")

    def _run_extractor_plugin_task(
        self,
        *,
        worker_id: str,
        root_domain: str,
        plugin_name: str,
    ) -> tuple[int, str]:
        paths = self._artifact_paths(root_domain)
        domain_dir = _domain_output_dir(root_domain, self.cfg.output_root)
        fozzy_results_dir = paths["fozzy_results_dir"]
        if not fozzy_results_dir.is_dir():
            self._download_zip_artifact(root_domain, "fozzy_results_zip", fozzy_results_dir)
        if not fozzy_results_dir.is_dir():
            return 1, "missing fozzy results artifact"

        extractor_params = self._workflow_stage_parameters(plugin_name)
        extractor_workers = max(
            1,
            _safe_int(
                extractor_params.get("workers", self.cfg.extractor_process_workers),
                max(1, int(self.cfg.extractor_process_workers)),
            ),
        )
        force_extractor = bool(extractor_params.get("force", True))
        cmd = [
            self.cfg.python_executable,
            "extractor.py",
            root_domain,
            "--config",
            str(self.cfg.extractor_config),
            "--scan-root",
            str(self.cfg.output_root),
            "--workers",
            str(extractor_workers),
        ]
        if force_extractor:
            cmd.append("--force")
        log_path = domain_dir / "coordinator.extractor.log"
        self.logger.info(
            "extractor_subprocess_start",
            worker_id=worker_id,
            root_domain=root_domain,
            stage=plugin_name,
            command=cmd,
            log_path=str(log_path),
        )
        try:
            exit_code = run_subprocess(cmd, cwd=BASE_DIR, log_path=log_path)
            err_text = summarize_subprocess_failure("extractor", log_path, exit_code) if exit_code != 0 else ""
        except Exception as exc:
            return 1, str(exc)
        try:
            self._upload_file_artifact(root_domain, "extractor_summary_json", paths["extractor_summary_json"], worker_id)
            self._upload_zip_artifact(root_domain, "extractor_matches_zip", paths["extractor_matches_dir"], worker_id)
        except Exception as exc:
            self.logger.error(
                "extractor_artifact_upload_failed",
                worker_id=worker_id,
                root_domain=root_domain,
                stage=plugin_name,
                error=str(exc),
            )
        return int(exit_code), str(err_text or "")

    def _run_plugin_task(self, *, worker_id: str, entry: dict[str, Any]) -> None:
        workflow_id = str(entry.get("workflow_id") or self._workflow_id or "default").strip().lower() or "default"
        root_domain = str(entry.get("root_domain", "") or "").strip().lower()
        plugin_name = str(entry.get("plugin_name") or entry.get("stage") or "").strip().lower()
        if not root_domain or not plugin_name:
            self.logger.error(
                "workflow_task_invalid_entry",
                worker_id=worker_id,
                entry=entry,
            )
            return
        checkpoint = dict(entry.get("checkpoint") or {}) if isinstance(entry.get("checkpoint"), dict) else {}
        progress = dict(entry.get("progress") or {}) if isinstance(entry.get("progress"), dict) else {}
        checkpoint.update({"status": "running", "started_at_utc": _now_iso(), "plugin_name": plugin_name})
        progress.update({"status": "running", "plugin_name": plugin_name, "root_domain": root_domain})
        self._begin_job()
        heartbeat = LeaseHeartbeat(
            tick_fn=lambda: self.client.heartbeat_stage(
                worker_id,
                root_domain,
                plugin_name,
                self.cfg.lease_seconds,
                workflow_id=workflow_id,
                checkpoint=checkpoint,
                progress=progress,
                progress_artifact_type=f"workflow_progress_{plugin_name}",
            ),
            interval_seconds=self.cfg.heartbeat_interval_seconds,
            logger=self.logger,
            heartbeat_kind=f"{plugin_name}_stage",
        )
        heartbeat.start()
        try:
            self._update_plugin_progress(
                worker_id=worker_id,
                workflow_id=workflow_id,
                root_domain=root_domain,
                plugin_name=plugin_name,
                checkpoint=checkpoint,
                progress=progress,
            )
            if plugin_name == "fozzy":
                exit_code, err_text = self._run_fozzy_plugin_task(worker_id=worker_id, root_domain=root_domain, plugin_name=plugin_name)
            elif plugin_name == "auth0r":
                exit_code, err_text = self._run_auth0r_plugin_task(worker_id=worker_id, root_domain=root_domain, plugin_name=plugin_name)
            elif plugin_name == "extractor":
                exit_code, err_text = self._run_extractor_plugin_task(worker_id=worker_id, root_domain=root_domain, plugin_name=plugin_name)
            elif plugin_name.startswith("nightmare_"):
                exit_code, err_text = self._run_nightmare_artifact_gate_plugin(root_domain=root_domain, plugin_name=plugin_name)
            else:
                exit_code, err_text = 1, f"unsupported plugin: {plugin_name}"

            checkpoint["completed_at_utc"] = _now_iso()
            checkpoint["status"] = "completed" if int(exit_code) == 0 else "failed"
            progress["status"] = checkpoint["status"]
            progress["error"] = str(err_text or "")
            self._update_plugin_progress(
                worker_id=worker_id,
                workflow_id=workflow_id,
                root_domain=root_domain,
                plugin_name=plugin_name,
                checkpoint=checkpoint,
                progress=progress,
            )
            self.client.complete_stage(
                worker_id,
                root_domain,
                plugin_name,
                int(exit_code),
                str(err_text or ""),
                workflow_id=workflow_id,
                checkpoint=checkpoint,
                progress=progress,
                progress_artifact_type=f"workflow_progress_{plugin_name}",
                resume_mode="exact",
            )
            self.logger.info(
                "workflow_task_complete",
                worker_id=worker_id,
                workflow_id=workflow_id,
                root_domain=root_domain,
                stage=plugin_name,
                exit_code=int(exit_code),
                error=str(err_text or ""),
            )
        except Exception as exc:
            self.logger.error(
                "workflow_task_run_failed",
                worker_id=worker_id,
                workflow_id=workflow_id,
                root_domain=root_domain,
                stage=plugin_name,
                error=str(exc),
            )
            try:
                self.client.complete_stage(
                    worker_id,
                    root_domain,
                    plugin_name,
                    1,
                    str(exc),
                    workflow_id=workflow_id,
                    checkpoint={"status": "failed", "error": str(exc), "completed_at_utc": _now_iso()},
                    progress={"status": "failed", "error": str(exc)},
                    progress_artifact_type=f"workflow_progress_{plugin_name}",
                    resume_mode="exact",
                )
            except Exception as complete_exc:
                self.logger.error(
                    "workflow_task_complete_failed_after_exception",
                    worker_id=worker_id,
                    workflow_id=workflow_id,
                    root_domain=root_domain,
                    stage=plugin_name,
                    error=str(complete_exc),
                )
        finally:
            heartbeat.stop()
            self._end_job()

    def _plugin_worker_loop(self, idx: int) -> None:
        worker_id = f"{self.worker_prefix}-plugin-{idx}"
        self._set_worker_state(worker_id, "running")
        self.logger.info(
            "workflow_plugin_worker_loop_started",
            worker_id=worker_id,
            worker_index=idx,
            workflow_id=self._workflow_id,
            plugin_allowlist=self.cfg.plugin_allowlist,
        )
        while not self.stop_event.is_set():
            state = self._poll_worker_commands(worker_id)
            if state in {"paused", "stopped", "errored"}:
                self.logger.warning(
                    "workflow_plugin_worker_waiting_due_to_state",
                    worker_id=worker_id,
                    worker_state=state,
                    poll_interval_seconds=self.cfg.poll_interval_seconds,
                )
                self.stop_event.wait(self.cfg.poll_interval_seconds)
                continue
            self._maybe_apply_fleet_output_clear()
            try:
                entry = self.client.claim_next_stage(
                    worker_id=worker_id,
                    lease_seconds=self.cfg.lease_seconds,
                    workflow_id=self._workflow_id,
                    plugin_allowlist=self.cfg.plugin_allowlist,
                )
            except Exception as exc:
                self.logger.error("workflow_plugin_claim_failed", worker_id=worker_id, error=str(exc))
                self.stop_event.wait(self.cfg.poll_interval_seconds)
                continue
            if not entry:
                self.stop_event.wait(self.cfg.poll_interval_seconds)
                continue
            self._run_plugin_task(worker_id=worker_id, entry=entry)

    def _fozzy_worker_loop(self, idx: int) -> None:
        worker_id = f"{self.worker_prefix}-fozzy-{idx}"
        self._set_worker_state(worker_id, "running")
        self.logger.info("fozzy_worker_loop_started", worker_id=worker_id, worker_index=idx)
        while not self.stop_event.is_set():
            state = self._poll_worker_commands(worker_id)
            if state in {"paused", "stopped", "errored"}:
                self.logger.warning(
                    "fozzy_worker_waiting_due_to_state",
                    worker_id=worker_id,
                    worker_state=state,
                    poll_interval_seconds=self.cfg.poll_interval_seconds,
                )
                self.stop_event.wait(self.cfg.poll_interval_seconds)
                continue
            self._maybe_apply_fleet_output_clear()
            try:
                entry = self.client.claim_stage(
                    worker_id,
                    "fozzy",
                    self.cfg.lease_seconds,
                    workflow_id=self._workflow_id,
                )
            except Exception as exc:
                self.logger.error("fozzy_claim_failed", worker_id=worker_id, error=str(exc))
                self.stop_event.wait(self.cfg.poll_interval_seconds)
                continue
            if not entry:
                self.logger.info("fozzy_claim_empty", worker_id=worker_id)
                self.stop_event.wait(self.cfg.poll_interval_seconds)
                continue
            root_domain = str(entry.get("root_domain", "") or "").strip().lower()
            if not root_domain:
                self.logger.error("fozzy_claim_invalid_entry", worker_id=worker_id, entry=entry)
                self.stop_event.wait(1.0)
                continue
            self.logger.info(
                "fozzy_job_start",
                worker_id=worker_id,
                root_domain=root_domain,
            )
            paths = self._artifact_paths(root_domain)
            params_path = paths["nightmare_parameters_json"]
            if not params_path.is_file():
                self._download_file_artifact(root_domain, "nightmare_parameters_json", params_path)
            if not params_path.is_file():
                self.client.complete_stage(
                    worker_id,
                    root_domain,
                    "fozzy",
                    1,
                    "missing parameters artifact",
                    workflow_id=self._workflow_id,
                )
                self.logger.error(
                    "fozzy_parameters_missing",
                    worker_id=worker_id,
                    root_domain=root_domain,
                    parameters_path=str(params_path),
                )
                continue

            hv_dir = paths.get("nightmare_high_value_dir")
            if hv_dir is not None:
                hv_dir.mkdir(parents=True, exist_ok=True)
                self._download_zip_artifact(root_domain, "nightmare_high_value_zip", hv_dir)
            self._download_file_artifact(root_domain, "nightmare_post_requests_json", paths["nightmare_post_requests_json"])

            self._begin_job()
            try:
                heartbeat = LeaseHeartbeat(
                    tick_fn=lambda: self.client.heartbeat_stage(
                        worker_id,
                        root_domain,
                        "fozzy",
                        self.cfg.lease_seconds,
                        workflow_id=self._workflow_id,
                    ),
                    interval_seconds=self.cfg.heartbeat_interval_seconds,
                    logger=self.logger,
                    heartbeat_kind="fozzy_stage",
                )
                heartbeat.start()
                fozzy_params = self._workflow_stage_parameters("fozzy")
                max_background_workers = max(
                    1,
                    _safe_int(
                        fozzy_params.get("max_background_workers", self.cfg.fozzy_process_workers),
                        max(1, int(self.cfg.fozzy_process_workers)),
                    ),
                )
                max_workers_per_domain = max(
                    1,
                    _safe_int(fozzy_params.get("max_workers_per_domain", 1), 1),
                )
                max_workers_per_subdomain = max(
                    1,
                    _safe_int(fozzy_params.get("max_workers_per_subdomain", 1), 1),
                )
                cmd = [
                    self.cfg.python_executable,
                    "fozzy.py",
                    str(params_path),
                    "--config",
                    str(self.cfg.fozzy_config),
                    "--max-background-workers",
                    str(max_background_workers),
                    "--max-workers-per-domain",
                    str(max_workers_per_domain),
                    "--max-workers-per-subdomain",
                    str(max_workers_per_subdomain),
                ]
                log_path = _domain_output_dir(root_domain, self.cfg.output_root) / "coordinator.fozzy.log"
                exit_code = 1
                err_text = ""
                self.logger.info(
                    "fozzy_subprocess_start",
                    worker_id=worker_id,
                    root_domain=root_domain,
                    command=cmd,
                    log_path=str(log_path),
                )
                try:
                    exit_code = run_subprocess(cmd, cwd=BASE_DIR, log_path=log_path)
                    if exit_code != 0:
                        err_text = summarize_subprocess_failure("fozzy", log_path, exit_code)
                    self.logger.info(
                        "fozzy_subprocess_complete",
                        worker_id=worker_id,
                        root_domain=root_domain,
                        exit_code=exit_code,
                        error=err_text,
                    )
                except Exception as exc:
                    err_text = str(exc)
                    exit_code = 1
                    self.logger.error(
                        "fozzy_subprocess_error",
                        worker_id=worker_id,
                        root_domain=root_domain,
                        error=err_text,
                    )
                finally:
                    heartbeat.stop()

                try:
                    self._upload_file_artifact(root_domain, "fozzy_summary_json", paths["fozzy_summary_json"], worker_id)
                    self._upload_file_artifact(root_domain, "fozzy_inventory_json", paths["fozzy_inventory_json"], worker_id)
                    self._upload_file_artifact(root_domain, "fozzy_log", paths["fozzy_log"], worker_id)
                    self._upload_zip_artifact(root_domain, "fozzy_results_zip", paths["fozzy_results_dir"], worker_id)
                except Exception as exc:
                    self.logger.error(
                        "fozzy_artifact_upload_failed",
                        worker_id=worker_id,
                        root_domain=root_domain,
                        error=str(exc),
                    )

                if exit_code == 0 and not self.cfg.workflow_scheduler_enabled:
                    try:
                        self.client.enqueue_stage(root_domain, "extractor", workflow_id=self._workflow_id)
                        self.logger.info(
                            "fozzy_stage_enqueued",
                            worker_id=worker_id,
                            root_domain=root_domain,
                            stage="extractor",
                        )
                    except Exception as exc:
                        self.logger.error(
                            "fozzy_stage_enqueue_failed",
                            worker_id=worker_id,
                            root_domain=root_domain,
                            stage="extractor",
                            error=str(exc),
                        )
                try:
                    self.client.complete_stage(
                        worker_id,
                        root_domain,
                        "fozzy",
                        exit_code,
                        err_text,
                        workflow_id=self._workflow_id,
                    )
                    self.logger.info(
                        "fozzy_job_complete",
                        worker_id=worker_id,
                        root_domain=root_domain,
                        exit_code=exit_code,
                        error=err_text,
                    )
                except Exception as exc:
                    self.logger.error(
                        "fozzy_complete_failed",
                        worker_id=worker_id,
                        root_domain=root_domain,
                        exit_code=exit_code,
                        error=str(exc),
                    )
            finally:
                self._end_job()


    def _auth0r_worker_loop(self, idx: int) -> None:
        worker_id = f"{self.worker_prefix}-auth0r-{idx}"
        self._set_worker_state(worker_id, "running")
        self.logger.info("auth0r_worker_loop_started", worker_id=worker_id, worker_index=idx)
        while not self.stop_event.is_set():
            state = self._poll_worker_commands(worker_id)
            if state in {"paused", "stopped", "errored"}:
                self.logger.warning(
                    "auth0r_worker_waiting_due_to_state",
                    worker_id=worker_id,
                    worker_state=state,
                    poll_interval_seconds=self.cfg.poll_interval_seconds,
                )
                self.stop_event.wait(self.cfg.poll_interval_seconds)
                continue
            self._maybe_apply_fleet_output_clear()
            try:
                entry = self.client.claim_stage(
                    worker_id,
                    "auth0r",
                    self.cfg.lease_seconds,
                    workflow_id=self._workflow_id,
                )
            except Exception as exc:
                self.logger.error("auth0r_claim_failed", worker_id=worker_id, error=str(exc))
                self.stop_event.wait(self.cfg.poll_interval_seconds)
                continue
            if not entry:
                self.logger.info("auth0r_claim_empty", worker_id=worker_id)
                self.stop_event.wait(self.cfg.poll_interval_seconds)
                continue
            root_domain = str(entry.get("root_domain", "") or "").strip().lower()
            if not root_domain:
                self.logger.error("auth0r_claim_invalid_entry", worker_id=worker_id, entry=entry)
                self.stop_event.wait(1.0)
                continue
            paths = self._artifact_paths(root_domain)
            nightmare_session_path = paths["nightmare_session_json"]
            if not nightmare_session_path.is_file():
                self._download_file_artifact(root_domain, "nightmare_session_json", nightmare_session_path)
            if not nightmare_session_path.is_file():
                self.client.complete_stage(
                    worker_id,
                    root_domain,
                    "auth0r",
                    1,
                    "missing nightmare session artifact",
                    workflow_id=self._workflow_id,
                )
                self.logger.error(
                    "auth0r_session_missing",
                    worker_id=worker_id,
                    root_domain=root_domain,
                    session_path=str(nightmare_session_path),
                )
                continue

            self._begin_job()
            try:
                heartbeat = LeaseHeartbeat(
                    tick_fn=lambda: self.client.heartbeat_stage(
                        worker_id,
                        root_domain,
                        "auth0r",
                        self.cfg.lease_seconds,
                        workflow_id=self._workflow_id,
                    ),
                    interval_seconds=self.cfg.heartbeat_interval_seconds,
                    logger=self.logger,
                    heartbeat_kind="auth0r_stage",
                )
                heartbeat.start()
                cmd = [
                    self.cfg.python_executable,
                    "auth0r.py",
                    root_domain,
                    "--nightmare-session",
                    str(nightmare_session_path),
                    "--summary-json",
                    str(paths["auth0r_summary_json"]),
                ]
                auth0r_database_url = os.getenv("AUTH0R_DATABASE_URL", "") or os.getenv("DATABASE_URL", "") or os.getenv("COORDINATOR_DATABASE_URL", "")
                if auth0r_database_url:
                    cmd.extend(["--database-url", auth0r_database_url])
                if not self.client.verify_ssl:
                    cmd.append("--insecure-tls")
                auth0r_cfg = _read_json_dict(self.cfg.auth0r_config)
                auth0r_params = self._workflow_stage_parameters("auth0r")
                min_delay = auth0r_params.get("min_delay_seconds", auth0r_cfg.get("min_delay_seconds", 0.25))
                max_seed_actions = auth0r_params.get("max_seed_actions", auth0r_cfg.get("max_seed_actions", 200))
                timeout_seconds = auth0r_params.get("timeout_seconds", auth0r_cfg.get("timeout_seconds", 20.0))
                cmd.extend(["--min-delay-seconds", str(min_delay)])
                cmd.extend(["--max-seed-actions", str(max_seed_actions)])
                cmd.extend(["--timeout-seconds", str(timeout_seconds)])
                log_path = paths["auth0r_log"]
                exit_code = 1
                err_text = ""
                self.logger.info(
                    "auth0r_subprocess_start",
                    worker_id=worker_id,
                    root_domain=root_domain,
                    command=cmd,
                    log_path=str(log_path),
                )
                try:
                    exit_code = run_subprocess(cmd, cwd=BASE_DIR, log_path=log_path)
                    if exit_code != 0:
                        err_text = summarize_subprocess_failure("auth0r", log_path, exit_code)
                    self.logger.info(
                        "auth0r_subprocess_complete",
                        worker_id=worker_id,
                        root_domain=root_domain,
                        exit_code=exit_code,
                        error=err_text,
                    )
                except Exception as exc:
                    err_text = str(exc)
                    exit_code = 1
                    self.logger.error(
                        "auth0r_subprocess_error",
                        worker_id=worker_id,
                        root_domain=root_domain,
                        error=err_text,
                    )
                finally:
                    heartbeat.stop()

                try:
                    self._upload_file_artifact(root_domain, "auth0r_summary_json", paths["auth0r_summary_json"], worker_id)
                    self._upload_file_artifact(root_domain, "auth0r_log", paths["auth0r_log"], worker_id)
                except Exception as exc:
                    self.logger.error(
                        "auth0r_artifact_upload_failed",
                        worker_id=worker_id,
                        root_domain=root_domain,
                        error=str(exc),
                    )
                try:
                    self.client.complete_stage(
                        worker_id,
                        root_domain,
                        "auth0r",
                        exit_code,
                        err_text,
                        workflow_id=self._workflow_id,
                    )
                except Exception as exc:
                    self.logger.error(
                        "auth0r_complete_failed",
                        worker_id=worker_id,
                        root_domain=root_domain,
                        exit_code=exit_code,
                        error=str(exc),
                    )
            finally:
                self._end_job()
    def _extractor_worker_loop(self, idx: int) -> None:
        worker_id = f"{self.worker_prefix}-extractor-{idx}"
        self._set_worker_state(worker_id, "running")
        self.logger.info("extractor_worker_loop_started", worker_id=worker_id, worker_index=idx)
        while not self.stop_event.is_set():
            state = self._poll_worker_commands(worker_id)
            if state in {"paused", "stopped", "errored"}:
                self.logger.warning(
                    "extractor_worker_waiting_due_to_state",
                    worker_id=worker_id,
                    worker_state=state,
                    poll_interval_seconds=self.cfg.poll_interval_seconds,
                )
                self.stop_event.wait(self.cfg.poll_interval_seconds)
                continue
            self._maybe_apply_fleet_output_clear()
            try:
                entry = self.client.claim_stage(
                    worker_id,
                    "extractor",
                    self.cfg.lease_seconds,
                    workflow_id=self._workflow_id,
                )
            except Exception as exc:
                self.logger.error("extractor_claim_failed", worker_id=worker_id, error=str(exc))
                self.stop_event.wait(self.cfg.poll_interval_seconds)
                continue
            if not entry:
                self.logger.info("extractor_claim_empty", worker_id=worker_id)
                self.stop_event.wait(self.cfg.poll_interval_seconds)
                continue
            root_domain = str(entry.get("root_domain", "") or "").strip().lower()
            if not root_domain:
                self.logger.error("extractor_claim_invalid_entry", worker_id=worker_id, entry=entry)
                self.stop_event.wait(1.0)
                continue
            self.logger.info(
                "extractor_job_start",
                worker_id=worker_id,
                root_domain=root_domain,
            )
            paths = self._artifact_paths(root_domain)
            domain_dir = _domain_output_dir(root_domain, self.cfg.output_root)
            fozzy_results_dir = paths["fozzy_results_dir"]
            if not fozzy_results_dir.is_dir():
                self._download_zip_artifact(root_domain, "fozzy_results_zip", fozzy_results_dir)
            if not fozzy_results_dir.is_dir():
                self.client.complete_stage(
                    worker_id,
                    root_domain,
                    "extractor",
                    1,
                    "missing fozzy results artifact",
                    workflow_id=self._workflow_id,
                )
                self.logger.error(
                    "extractor_results_missing",
                    worker_id=worker_id,
                    root_domain=root_domain,
                    results_dir=str(fozzy_results_dir),
                )
                continue

            self._begin_job()
            try:
                heartbeat = LeaseHeartbeat(
                    tick_fn=lambda: self.client.heartbeat_stage(
                        worker_id,
                        root_domain,
                        "extractor",
                        self.cfg.lease_seconds,
                        workflow_id=self._workflow_id,
                    ),
                    interval_seconds=self.cfg.heartbeat_interval_seconds,
                    logger=self.logger,
                    heartbeat_kind="extractor_stage",
                )
                heartbeat.start()
                extractor_params = self._workflow_stage_parameters("extractor")
                extractor_workers = max(
                    1,
                    _safe_int(
                        extractor_params.get("workers", self.cfg.extractor_process_workers),
                        max(1, int(self.cfg.extractor_process_workers)),
                    ),
                )
                force_extractor = bool(extractor_params.get("force", True))
                cmd = [
                    self.cfg.python_executable,
                    "extractor.py",
                    root_domain,
                    "--config",
                    str(self.cfg.extractor_config),
                    "--scan-root",
                    str(self.cfg.output_root),
                    "--workers",
                    str(extractor_workers),
                ]
                if force_extractor:
                    cmd.append("--force")
                log_path = domain_dir / "coordinator.extractor.log"
                exit_code = 1
                err_text = ""
                self.logger.info(
                    "extractor_subprocess_start",
                    worker_id=worker_id,
                    root_domain=root_domain,
                    command=cmd,
                    log_path=str(log_path),
                )
                try:
                    exit_code = run_subprocess(cmd, cwd=BASE_DIR, log_path=log_path)
                    if exit_code != 0:
                        err_text = summarize_subprocess_failure("extractor", log_path, exit_code)
                    self.logger.info(
                        "extractor_subprocess_complete",
                        worker_id=worker_id,
                        root_domain=root_domain,
                        exit_code=exit_code,
                        error=err_text,
                    )
                except Exception as exc:
                    err_text = str(exc)
                    exit_code = 1
                    self.logger.error(
                        "extractor_subprocess_error",
                        worker_id=worker_id,
                        root_domain=root_domain,
                        error=err_text,
                    )
                finally:
                    heartbeat.stop()

                try:
                    self._upload_file_artifact(root_domain, "extractor_summary_json", paths["extractor_summary_json"], worker_id)
                    self._upload_zip_artifact(root_domain, "extractor_matches_zip", paths["extractor_matches_dir"], worker_id)
                except Exception as exc:
                    self.logger.error(
                        "extractor_artifact_upload_failed",
                        worker_id=worker_id,
                        root_domain=root_domain,
                        error=str(exc),
                    )

                try:
                    self.client.complete_stage(
                        worker_id,
                        root_domain,
                        "extractor",
                        exit_code,
                        err_text,
                        workflow_id=self._workflow_id,
                    )
                    self.logger.info(
                        "extractor_job_complete",
                        worker_id=worker_id,
                        root_domain=root_domain,
                        exit_code=exit_code,
                        error=err_text,
                    )
                except Exception as exc:
                    self.logger.error(
                        "extractor_complete_failed",
                        worker_id=worker_id,
                        root_domain=root_domain,
                        exit_code=exit_code,
                        error=str(exc),
                    )
            finally:
                self._end_job()

    def run(self) -> int:
        threads: list[threading.Thread] = []
        if self.cfg.workflow_scheduler_enabled:
            threads.append(threading.Thread(target=self._workflow_scheduler_loop, daemon=True))
        if self.cfg.enable_nightmare:
            for idx in range(1, max(1, self.cfg.nightmare_workers) + 1):
                t = threading.Thread(target=self._nightmare_worker_loop, args=(idx,), daemon=True)
                threads.append(t)
        # Unified plugin worker pool replaces per-tool stage loops.
        if self.cfg.plugin_workers > 0:
            for idx in range(1, max(1, int(self.cfg.plugin_workers)) + 1):
                t = threading.Thread(target=self._plugin_worker_loop, args=(idx,), daemon=True)
                threads.append(t)
        else:
            # Backward-compatible fallback for one release.
            if self.cfg.enable_fozzy:
                for idx in range(1, max(1, self.cfg.fozzy_workers) + 1):
                    t = threading.Thread(target=self._fozzy_worker_loop, args=(idx,), daemon=True)
                    threads.append(t)
            if self.cfg.enable_auth0r:
                for idx in range(1, max(1, self.cfg.auth0r_workers) + 1):
                    t = threading.Thread(target=self._auth0r_worker_loop, args=(idx,), daemon=True)
                    threads.append(t)
            if self.cfg.enable_extractor:
                for idx in range(1, max(1, self.cfg.extractor_workers) + 1):
                    t = threading.Thread(target=self._extractor_worker_loop, args=(idx,), daemon=True)
                    threads.append(t)

        for t in threads:
            t.start()
        self.logger.info(
            "coordinator_workers_started",
            worker_prefix=self.worker_prefix,
            workflow_scheduler_enabled=bool(self.cfg.workflow_scheduler_enabled),
            workflow_scheduler_interval_seconds=float(self.cfg.workflow_scheduler_interval_seconds),
            workflow_id=self._workflow_id,
            nightmare_workers=(self.cfg.nightmare_workers if self.cfg.enable_nightmare else 0),
            plugin_workers=int(self.cfg.plugin_workers or 0),
            plugin_allowlist=self.cfg.plugin_allowlist,
            legacy_stage_workers_enabled=bool(int(self.cfg.plugin_workers or 0) <= 0),
            fozzy_workers=(self.cfg.fozzy_workers if self.cfg.enable_fozzy else 0),
            auth0r_workers=(self.cfg.auth0r_workers if self.cfg.enable_auth0r else 0),
            extractor_workers=(self.cfg.extractor_workers if self.cfg.enable_extractor else 0),
        )
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            self.logger.warning("coordinator_interrupt_received")
            self.stop_event.set()
            return 0


def parse_args(argv: Optional[list[str] ] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Distributed coordinator worker for Nightmare/Fozzy/Auth0r/Extractor")
    p.add_argument("--config", default=str(CONFIG_PATH_DEFAULT), help="Path to coordinator config JSON")
    p.add_argument("--server-base-url", default=None, help="Coordinator server base URL (e.g. https://coord.example.com)")
    p.add_argument("--api-token", default=None, help="Coordinator API bearer token")
    p.add_argument("--output-root", default=None, help="Output root (default: ./output)")
    return p.parse_args(argv)



def main(argv: Optional[list[str] ] = None) -> int:
    install_error_reporting(program_name="coordinator", component_name="distributed_worker", source_type="worker")
    configure_logging()
    logger = get_logger("coordinator")
    args = parse_args(argv)
    cfg = load_config(args)
    logger.info(
        "coordinator_starting",
        server_base_url=cfg.server_base_url,
        output_root=str(cfg.output_root),
        workflow_config=str(cfg.workflow_config),
        workflow_scheduler_enabled=bool(cfg.workflow_scheduler_enabled),
        workflow_scheduler_interval_seconds=float(cfg.workflow_scheduler_interval_seconds),
        plugin_workers=int(cfg.plugin_workers or 0),
        plugin_allowlist=cfg.plugin_allowlist,
        nightmare_workers=cfg.nightmare_workers,
        fozzy_workers=cfg.fozzy_workers,
        auth0r_workers=cfg.auth0r_workers,
        extractor_workers=cfg.extractor_workers,
    )
    runner = DistributedCoordinator(cfg, logger=logger)
    return runner.run()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        report_error(
            "Unhandled coordinator exception",
            program_name="coordinator",
            component_name="distributed_worker",
            source_type="worker",
            exception=exc,
            raw_line=str(exc),
        )
        raise
