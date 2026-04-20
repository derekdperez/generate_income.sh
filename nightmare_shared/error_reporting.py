#!/usr/bin/env python3
"""Centralized best-effort error reporting helpers."""

from __future__ import annotations

import json
import logging
import os
import socket
import ssl
import sys
import threading
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _detect_machine() -> str:
    for key in ("EC2_INSTANCE_ID", "INSTANCE_ID", "HOSTNAME", "COMPUTERNAME"):
        value = str(os.getenv(key, "") or "").strip()
        if value:
            return value
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"


class ErrorReporter:
    def __init__(
        self,
        *,
        server_base_url: str = "",
        api_token: str = "",
        insecure_tls: bool | str = False,
        machine: str = "",
        program_name: str = "",
        component_name: str = "",
        source_type: str = "python",
    ) -> None:
        self.server_base_url = str(server_base_url or os.getenv("COORDINATOR_BASE_URL", "")).strip().rstrip("/")
        self.api_token = str(api_token or os.getenv("COORDINATOR_API_TOKEN", "")).strip()
        self.insecure_tls = _coerce_bool(insecure_tls if insecure_tls != "" else os.getenv("COORDINATOR_INSECURE_TLS", False))
        self.machine = str(machine or _detect_machine()).strip() or "unknown"
        self.program_name = str(program_name or "").strip() or "unknown"
        self.component_name = str(component_name or self.program_name).strip() or self.program_name
        self.source_type = str(source_type or "python").strip() or "python"

    @property
    def enabled(self) -> bool:
        return bool(self.server_base_url)

    def report(
        self,
        *,
        severity: str = "error",
        description: str,
        raw_line: str = "",
        exception: BaseException | None = None,
        class_name: str = "",
        function_name: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        if not self.enabled:
            return False
        stacktrace = ""
        exception_type = ""
        if exception is not None:
            exception_type = exception.__class__.__name__
            try:
                stacktrace = "".join(traceback.format_exception(type(exception), exception, exception.__traceback__))
            except Exception:
                stacktrace = repr(exception)
        payload = {
            "event_time_utc": _iso_now(),
            "severity": str(severity or "error"),
            "description": str(description or raw_line or exception_type or "error").strip(),
            "machine": self.machine,
            "source_id": f"{self.program_name}:{self.component_name}",
            "source_type": self.source_type,
            "program_name": self.program_name,
            "component_name": self.component_name,
            "class_name": str(class_name or "").strip(),
            "function_name": str(function_name or "").strip(),
            "exception_type": exception_type,
            "stacktrace": stacktrace,
            "raw_line": str(raw_line or "").strip(),
            "metadata_json": metadata or {},
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.server_base_url}/api/coord/errors/ingest",
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {self.api_token}"} if self.api_token else {}),
            },
        )
        try:
            context = None
            if self.insecure_tls:
                context = ssl._create_unverified_context()
            with urllib.request.urlopen(req, timeout=5, context=context):  # noqa: S310 - controlled URL
                return True
        except Exception:
            return False


class _CentralErrorLoggingHandler(logging.Handler):
    def __init__(self, reporter: ErrorReporter):
        super().__init__(level=logging.ERROR)
        self.reporter = reporter

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.levelno < logging.ERROR:
                return
            desc = self.format(record) if self.formatter else record.getMessage()
            exc = record.exc_info[1] if record.exc_info and len(record.exc_info) > 1 else None
            self.reporter.report(
                severity=record.levelname.lower(),
                description=desc,
                raw_line=desc,
                exception=exc if isinstance(exc, BaseException) else None,
                class_name=getattr(record, "classname", "") or "",
                function_name=str(getattr(record, "funcName", "") or ""),
                metadata={
                    "logger_name": str(record.name or ""),
                    "module": str(getattr(record, "module", "") or ""),
                    "pathname": str(getattr(record, "pathname", "") or ""),
                    "lineno": int(getattr(record, "lineno", 0) or 0),
                    "thread_name": str(getattr(record, "threadName", "") or ""),
                    "process_name": str(getattr(record, "processName", "") or ""),
                },
            )
        except Exception:
            return


def install_error_reporting(
    *,
    program_name: str,
    component_name: str = "",
    source_type: str = "python",
    machine: str = "",
) -> ErrorReporter:
    reporter = ErrorReporter(
        program_name=program_name,
        component_name=component_name or program_name,
        source_type=source_type,
        machine=machine,
    )
    root_logger = logging.getLogger()
    already = any(isinstance(handler, _CentralErrorLoggingHandler) for handler in root_logger.handlers)
    if not already:
        handler = _CentralErrorLoggingHandler(reporter)
        root_logger.addHandler(handler)

    prior_sys_hook = sys.excepthook

    def _hook(exc_type: type[BaseException], exc: BaseException, tb: Any) -> None:
        try:
            if exc_type is not KeyboardInterrupt:
                stack = "".join(traceback.format_exception(exc_type, exc, tb))
                reporter.report(
                    severity="critical",
                    description=f"Unhandled exception in {program_name}",
                    raw_line=str(exc),
                    exception=exc,
                    metadata={"unhandled": True, "stacktrace_preview": stack[-4000:]},
                )
        except Exception:
            pass
        if callable(prior_sys_hook):
            prior_sys_hook(exc_type, exc, tb)

    sys.excepthook = _hook

    if hasattr(threading, "excepthook"):
        prior_thread_hook = threading.excepthook

        def _thread_hook(args: threading.ExceptHookArgs) -> None:
            try:
                if args.exc_type is not KeyboardInterrupt:
                    reporter.report(
                        severity="critical",
                        description=f"Unhandled thread exception in {program_name}",
                        raw_line=str(args.exc_value),
                        exception=args.exc_value,
                        metadata={
                            "unhandled": True,
                            "thread_name": str(getattr(args.thread, "name", "") or ""),
                        },
                    )
            except Exception:
                pass
            try:
                prior_thread_hook(args)
            except Exception:
                pass

        threading.excepthook = _thread_hook

    return reporter


def report_error(
    description: str,
    *,
    program_name: str,
    component_name: str = "",
    source_type: str = "python",
    machine: str = "",
    exception: BaseException | None = None,
    raw_line: str = "",
    metadata: dict[str, Any] | None = None,
    severity: str = "error",
) -> bool:
    reporter = ErrorReporter(
        program_name=program_name,
        component_name=component_name or program_name,
        source_type=source_type,
        machine=machine,
    )
    return reporter.report(
        severity=severity,
        description=description,
        raw_line=raw_line,
        exception=exception,
        metadata=metadata,
    )
