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
    # "service": the entry starts Jig. "tray": it starts the tray icon, which starts Jig (Windows installs).
    launcher: str = "service"

    @classmethod
    def from_config(cls, config: Config, *, data_dir: Path | None = None, port: int | None = None) -> LaunchSpec:
        return cls(config_path=config.source, data_dir=Path(data_dir or config.data_dir).resolve(),
                   host=config.server.host, port=port or config.server.port, python=Path(sys.executable),
                   launcher=config.autostart.launcher)

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
    # Whether the registered entry starts THIS install (same data folder). None when nothing is registered.
    owned: bool | None = None
    # What the registered entry starts: its data folder, config and port, as far as they can be read.
    owner: dict[str, Any] | None = None
    # One plain sentence for people, for example "On. Jig will start the next time you sign in to Windows."
    summary: str = ""

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

    @abstractmethod
    def registered_owner(self) -> dict[str, Any] | None:
        """What the registered entry starts: ``{"data_dir", "config", "port"}`` (each may be None if it can't
        be read), or None when nothing is registered."""

    def owned(self, owner: dict[str, Any] | None = None) -> bool | None:
        """Whether the registered entry starts this install: the same data folder. None if nothing is registered.
        One data folder is one Jig (``jig.instance``), so the folder is what identifies an install."""
        owner = owner if owner is not None else self.registered_owner()
        if owner is None:
            return None
        return bool(owner.get("data_dir")) and same_path(owner["data_dir"], self.spec.data_dir)

    def refuse_if_other_install(self) -> None:
        """Every backend calls this first in enable() and disable(): an entry that starts another install is
        never replaced or removed from here."""
        owner = self.registered_owner()
        if owner is not None and not self.owned(owner):
            raise AutostartError(self.other_install_message(owner))

    def other_install_message(self, owner: dict[str, Any]) -> str:
        folder = owner.get("data_dir") or "a data folder Jig can't read from the entry"
        return (f"Start with Windows ({self.entry}) is set up for another Jig, with its data in {folder}, not this "
                f"one ({self.spec.data_dir}). Jig leaves that entry alone. To change it, use that Jig. To start this "
                "one at sign-in too, give it its own entry: [autostart] entry in its config, or --entry.")

    def status(self) -> Status:
        registered = self.is_registered()
        st = Status(backend=self.backend_name, entry=self.entry, registered=registered,
                    log_path=str(self.spec.log_path))
        if registered:
            info = self._query()
            st.last_run, st.last_result, st.state = info.pop("last_run"), info.pop("last_result"), info.pop("state")
            st.details = info
            st.owner = self.registered_owner()
            st.owned = self.owned(st.owner)
        st.instance = running_instance(self.spec.data_dir)
        st.running = st.instance is not None
        if st.running:
            port = (st.instance or {}).get("port") or self.spec.port
            try:
                st.answering = httpx.get(f"http://{self.spec.host}:{port}/health", timeout=3).status_code == 200
            except httpx.HTTPError:
                st.answering = False
        st.summary = self._summary(st)
        return st

    def _summary(self, st: Status) -> str:
        if not st.registered:
            return "Off."
        if not st.owned:
            return (f"Set up for another Jig folder ({(st.owner or {}).get('data_dir') or 'unknown'}), not this one. "
                    "Jig leaves it alone.")
        problem = st.details.get("problem")
        if problem:
            return f"On, but {problem} See {st.log_path}."
        return f"On. Jig will start the next time you {self.sign_in_words}."

    sign_in_words: str = "sign in"


def same_path(a: str | Path, b: str | Path) -> bool:
    """Whether two paths name the same folder (case-insensitively on Windows, as its file system is)."""
    pa, pb = Path(a).expanduser().resolve(), Path(b).expanduser().resolve()
    return str(pa).lower() == str(pb).lower() if sys.platform == "win32" else pa == pb


def record_audit(data_dir: Path, kind: str, summary: str, **data: Any) -> None:
    """Append to the audit log of ``data_dir``. Safe while the server runs (SQLite WAL, busy timeout)."""
    db = Database(Path(data_dir) / "jig.db")
    try:
        AuditLog(db).record(kind, summary, actor="user", **data)
    finally:
        db.close()
