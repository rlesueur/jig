"""Linux backend: a systemd user unit, ``~/.config/systemd/user/jig.service``.

UNTESTED: this has not been run on Linux. Only the generated unit file is covered by tests
(``tests/test_autostart_files.py``). Please report what you find.

The unit runs as the user, inside their systemd user manager, so the keyring vault works when a
Secret Service (for example GNOME Keyring) is unlocked. It starts when the user manager starts, which
is at login. To start it at boot before anyone logs in, run ``loginctl enable-linger $USER`` (and note
that the keyring is then usually locked until you log in). systemd restarts it on failure and sends
SIGTERM to stop it, which Jig handles gracefully.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Any

from .base import AutostartBackend, AutostartError, LaunchSpec, Plan, current_account

DEFAULT_UNIT = "jig.service"


def _quote(arg: str) -> str:
    """Quote one ExecStart argument for systemd (double quotes; backslash, quote and % escaped)."""
    if arg and not any(c in arg for c in ' \t"\'\\%$;'):
        return arg
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'


class SystemdUserUnit(AutostartBackend):
    backend_name = "systemd user unit"
    tested = False
    default_entry = DEFAULT_UNIT

    def __init__(self, spec: LaunchSpec, *, entry: str | None = None, config_home: Path | None = None):
        super().__init__(spec, entry=entry)
        if not self.entry.endswith(".service"):
            raise AutostartError(f"systemd unit name must end in .service, not {self.entry!r}")
        self.unit_path = (config_home or Path.home() / ".config") / "systemd" / "user" / self.entry

    def exec_start(self) -> list[str]:
        return [str(self.spec.python), *self.spec.serve_args()]

    def unit_text(self) -> str:
        return "\n".join([
            "[Unit]",
            "Description=Jig personal AI agent",
            "Documentation=https://github.com/rlesueur/jig#running-jig-always-on",
            "After=network-online.target",
            "StartLimitIntervalSec=900",
            "StartLimitBurst=4",
            "",
            "[Service]",
            "Type=simple",
            f"WorkingDirectory={_quote(str(self.spec.config_path.parent))}",
            f"Environment={_quote('JIG_DATA_DIR=' + str(self.spec.data_dir))}",
            "ExecStart=" + " ".join(_quote(a) for a in self.exec_start()),
            "Restart=on-failure",
            "RestartSec=60",
            "KillSignal=SIGTERM",
            "TimeoutStopSec=60",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ])

    def plan(self) -> Plan:
        return Plan(
            backend=self.backend_name, entry=self.entry, tested=self.tested,
            command_line=shlex.join(self.exec_start()),
            trigger="when your systemd user manager starts (at login); at boot only with "
                    "'loginctl enable-linger'",
            account=f"{current_account()} (your own user; systemd --user)",
            log_path=str(self.spec.log_path),
            files=[str(self.unit_path), f"{self.spec.data_dir / 'logs'} (jig.log rotated at 5 MB, 5 kept)"],
            settings=["Restart=on-failure, 60s apart, at most 4 starts in 15 minutes",
                      "SIGTERM on stop, 60s to exit", "WantedBy=default.target"],
            notes=["Needs no root.", "To start before login: loginctl enable-linger $USER (not done for you).",
                   "UNTESTED on real Linux."],
            definition=self.unit_text(),
        )

    def _systemctl(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)
        if check and proc.returncode != 0:
            raise AutostartError(f"systemctl --user {' '.join(args)} failed (exit {proc.returncode}): "
                                 f"{(proc.stdout + proc.stderr).strip()}")
        return proc

    def is_registered(self) -> bool:
        return self.unit_path.exists()

    def enable(self, *, start_now: bool = False) -> Plan:
        if self.is_registered():
            raise AutostartError(f"{self.unit_path} already exists; run 'jig autostart disable' first")
        plan = self.plan()
        self.unit_path.parent.mkdir(parents=True, exist_ok=True)
        self.spec.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.unit_path.write_text(self.unit_text(), encoding="utf-8")
        self._systemctl("daemon-reload")
        self._systemctl("enable", *(["--now"] if start_now else []), self.entry)
        return plan

    def disable(self) -> list[str]:
        removed = []
        if self.unit_path.exists():
            self._systemctl("disable", "--now", self.entry)
            self.unit_path.unlink()
            self._systemctl("daemon-reload")
            removed.append(str(self.unit_path))
        return removed

    def _query(self) -> dict[str, Any]:
        out = self._systemctl("show", self.entry, "-p",
                              "ActiveState,SubState,ExecMainStatus,ExecMainStartTimestamp,UnitFileState").stdout
        f = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        return {"last_run": f.get("ExecMainStartTimestamp") or None, "last_result": f.get("ExecMainStatus"),
                "state": f"{f.get('ActiveState')}/{f.get('SubState')} ({f.get('UnitFileState')})"}
