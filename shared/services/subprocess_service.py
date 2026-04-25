"""Controlled subprocess execution exposed to workflow plugins."""

from __future__ import annotations

import subprocess
from pathlib import Path


class SubprocessService:
    """Run external commands with timeout and captured output."""

    def run(self, command: list[str], *, cwd: str | Path | None = None, timeout_seconds: int = 300) -> subprocess.CompletedProcess[str]:
        """Execute a command without a shell and return captured text output."""
        if not command:
            raise ValueError("command is required")
        return subprocess.run(
            [str(part) for part in command],
            cwd=str(cwd) if cwd else None,
            timeout=max(1, int(timeout_seconds or 300)),
            capture_output=True,
            text=True,
            check=False,
        )
