"""Per-agent workspace that every file tool is confined to.

This is a directory jail, not an OS-level sandbox. A VM or container sandbox
is on the roadmap.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath, PureWindowsPath

from .errors import SandboxViolation

_WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.IGNORECASE)


class Sandbox:
    def __init__(self, root: Path, agent_id: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", agent_id):
            raise SandboxViolation(f"invalid agent id {agent_id!r}")
        self.root = (root / agent_id).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, path: str) -> Path:
        """Map a relative workspace path to an absolute path inside the jail."""
        if not isinstance(path, str) or not path.strip():
            raise SandboxViolation("path must be a non-empty string")
        if "\x00" in path:
            raise SandboxViolation("path contains a NUL byte")
        win = PureWindowsPath(path)
        if win.drive or win.root or PurePosixPath(path).is_absolute():
            raise SandboxViolation(f"absolute paths are not allowed: {path!r}")
        parts = win.parts
        if any(p == ".." for p in parts):
            raise SandboxViolation(f"path traversal is not allowed: {path!r}")
        for p in parts:
            if ":" in p:
                raise SandboxViolation(f"alternate data streams are not allowed: {path!r}")
            if _WINDOWS_RESERVED.match(p):
                raise SandboxViolation(f"reserved device name in path: {path!r}")
        candidate = (self.root / Path(*parts)).resolve()
        # resolve() follows symlinks and junctions, so this also catches links out of the jail.
        if candidate != self.root and not candidate.is_relative_to(self.root):
            raise SandboxViolation(f"path escapes the sandbox: {path!r}")
        return candidate

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix() or "."
