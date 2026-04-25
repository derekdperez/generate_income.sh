"""Controlled filesystem operations exposed to workflow plugins."""

from __future__ import annotations

from pathlib import Path


class FileService:
    """Provide sandbox-aware file reads, writes, and folder management."""

    def __init__(self, root: str | Path) -> None:
        """Create a file service rooted at a specific workspace directory."""
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, relative_path: str | Path) -> Path:
        """Resolve and validate a path so plugins cannot escape the workspace."""
        candidate = (self.root / Path(relative_path)).resolve()
        if self.root not in candidate.parents and candidate != self.root:
            raise ValueError(f"path escapes workspace: {relative_path}")
        return candidate

    def read_text(self, relative_path: str | Path, *, encoding: str = "utf-8") -> str:
        """Read text from a workspace-relative file."""
        return self.resolve(relative_path).read_text(encoding=encoding)

    def write_text(self, relative_path: str | Path, content: str, *, encoding: str = "utf-8") -> Path:
        """Write text to a workspace-relative file and return the resolved path."""
        path = self.resolve(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(content), encoding=encoding)
        return path

    def ensure_dir(self, relative_path: str | Path) -> Path:
        """Create a workspace-relative directory if it does not already exist."""
        path = self.resolve(relative_path)
        path.mkdir(parents=True, exist_ok=True)
        return path
