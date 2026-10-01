"""The interface every autostart backend implements, and what they share."""

from __future__ import annotations

import getpass
import sys
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ..audit import AuditLog
from ..config import Config
from ..db import Database
from ..errors import JigError
from ..instance import running_instance


class AutostartError(JigError):
    """Registering, removing or querying the autostart entry failed. Nothing is changed silently."""


CONTAINER_MODE_HINT = ("Docker keeps Jig running (restart: unless-stopped). Make sure Docker Desktop starts "
                       "when you log in.")
CONTAINER_MODE_REASON = f"Autostart is not applicable in container mode: {CONTAINER_MODE_HINT}"


def not_applicable_reason(config: Config) -> str | None:
    """Why autostart does not apply to this deployment, or None when it does."""
    return CONTAINER_MODE_REASON if config.deployment == "container" else None


@dataclass(frozen=True)
class LaunchSpec:
    """What the autostart entry starts: one Jig, with one config, data directory and port."""

    config_path: Path
    data_dir: Path
    host: str
    port: int
    python: Path  # the interpreter of the environment Jig is installed in

    @classmethod
    def from_config(cls, config: Config, *, data_dir: Path | None = None, port: int | None = None) -> LaunchSpec:
        return cls(config_path=config.source, data_dir=Path(data_dir or config.data_dir).resolve(),
                   host=config.server.host, port=port or config.server.port, python=Path(sys.executable))

    @property
    def log_path(self) -> Path:
        return self.data_dir / "logs" / "jig.log"

    def serve_args(self) -> list[str]:
        """Arguments after the interpreter for running ``jig serve`` as an autostarted server."""
        return ["-m", "jig.cli", "--config", str(self.config_path), "serve", "--port", str(self.port),
                "--start-reason", "autostart", "--log-file"]


@dataclass
class Plan:
    """Everything the user is shown before anything is registered."""

    backend: str
    entry: str  # task name, launchd label or systemd unit
    command_line: str
    trigger: str
    account: str
    log_path: str
    files: list[str]
    settings: list[str]
    notes: list[str]
    definition: str
    tested: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def disclosure(self) -> str:
        lines = [f"Jig autostart will register this ({self.backend}):", "",
                 f"  Entry:        {self.entry}",
                 f"  Command line: {self.command_line}",
                 f"  Trigger:      {self.trigger}",
                 f"  Runs as:      {self.account}",
                 f"  Logs:         {self.log_path}"]
        lines += [f"  Writes:       {f}" for f in self.files]
        lines += ["", "  Settings:"] + [f"    - {s}" for s in self.settings]
        if self.notes:
            lines += ["", "  Notes:"] + [f"    - {n}" for n in self.notes]
        if not self.tested:
            lines += ["", "  WARNING: this backend has not been tested on a real machine yet."]
        return "\n".join(lines)


@dataclass
class Status:
    backend: str
    entry: str
    registered: bool
    last_run: str | None = None
    last_result: str | None = None
    state: str | None = None
    running: bool = False
    instance: dict[str, Any] | None = None
    answering: bool | None = None
    log_path: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def current_account() -> str:
    if sys.platform == "win32":
        import os

        return f"{os.environ.get('USERDOMAIN', '')}\\{getpass.getuser()}".lstrip("\\")
    return getpass.getuser()


class AutostartBackend(ABC):
    backend_name: str = ""
    tested: bool = False

    def __init__(self, spec: LaunchSpec, *, entry: str | None = None):
        self.spec = spec
        self.entry = entry or self.default_entry

    default_entry: str = ""

    @abstractmethod
    def plan(self) -> Plan:
        """Describe exactly what enable() would register. Changes nothing."""

    @abstractmethod
    def enable(self, *, start_now: bool = False) -> Plan:
        """Register the entry described by plan(). Fails loudly if it is already registered."""

    @abstractmethod
    def disable(self) -> list[str]:
        """Remove everything enable() registered. Returns what was removed."""

    @abstractmethod
    def is_registered(self) -> bool: ...

    @abstractmethod
    def _query(self) -> dict[str, Any]:
        """Backend-specific last run, last result and state, for a registered entry."""

    def status(self) -> Status:
        registered = self.is_registered()
        st = Status(backend=self.backend_name, entry=self.entry, registered=registered,
                    log_path=str(self.spec.log_path))
        if registered:
            info = self._query()
            st.last_run, st.last_result, st.state = info.pop("last_run"), info.pop("last_result"), info.pop("state")
            st.details = info
        st.instance = running_instance(self.spec.data_dir)
        st.running = st.instance is not None
        if st.running:
            port = (st.instance or {}).get("port") or self.spec.port
            try:
                st.answering = httpx.get(f"http://{self.spec.host}:{port}/health", timeout=3).status_code == 200
            except httpx.HTTPError:
                st.answering = False
        return st


def record_audit(data_dir: Path, kind: str, summary: str, **data: Any) -> None:
    """Append to the audit log of ``data_dir``. Safe while the server runs (SQLite WAL, busy timeout)."""
    db = Database(Path(data_dir) / "jig.db")
    try:
        AuditLog(db).record(kind, summary, actor="user", **data)
    finally:
        db.close()
