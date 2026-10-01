"""macOS backend: a launchd LaunchAgent in ``~/Library/LaunchAgents``.

UNTESTED: this has not been run on a Mac. Only the generated property list is covered by tests
(``tests/test_autostart_files.py``). Please report what you find.

The agent runs as the logged-in user (so the keyring vault works), starts at login (``RunAtLoad``) and
is restarted by launchd if it exits with an error (``KeepAlive`` with ``SuccessfulExit = false``), so a
clean ``jig stop`` stays stopped. launchd sends SIGTERM to stop it, which Jig handles gracefully.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import subprocess
from pathlib import Path
from typing import Any

from .base import AutostartBackend, AutostartError, LaunchSpec, Plan, current_account

DEFAULT_LABEL = "io.github.rlesueur.jig"


class LaunchdAgent(AutostartBackend):
    backend_name = "macOS launchd LaunchAgent"
    tested = False
    default_entry = DEFAULT_LABEL

    def __init__(self, spec: LaunchSpec, *, entry: str | None = None, home: Path | None = None):
        super().__init__(spec, entry=entry)
        self.plist_path = (home or Path.home()) / "Library" / "LaunchAgents" / f"{self.entry}.plist"

    def program_arguments(self) -> list[str]:
        return [str(self.spec.python), *self.spec.serve_args()]

    def plist(self) -> dict[str, Any]:
        logs = self.spec.data_dir / "logs"
        return {
            "Label": self.entry,
            "ProgramArguments": self.program_arguments(),
            "WorkingDirectory": str(self.spec.config_path.parent),
            "EnvironmentVariables": {"JIG_DATA_DIR": str(self.spec.data_dir)},
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "ThrottleInterval": 60,
            "ProcessType": "Background",
            "ExitTimeOut": 60,
            # Jig writes its own rotating jig.log; this only catches output from before logging starts.
            "StandardOutPath": str(logs / "launchd.log"),
            "StandardErrorPath": str(logs / "launchd.log"),
        }

    def plist_bytes(self) -> bytes:
        return plistlib.dumps(self.plist(), fmt=plistlib.FMT_XML)

    def plan(self) -> Plan:
        return Plan(
            backend=self.backend_name, entry=self.entry, tested=self.tested,
            command_line=shlex.join(self.program_arguments()),
            trigger="at login of this user (RunAtLoad), and when the agent is loaded",
            account=f"{current_account()} (your own user, in your login session)",
            log_path=str(self.spec.log_path),
            files=[str(self.plist_path), f"{self.spec.data_dir / 'logs'} (jig.log rotated at 5 MB, 5 kept; launchd.log)"],
            settings=["restarted by launchd if it exits with an error (KeepAlive SuccessfulExit=false), at most "
                      "once a minute", "a clean 'jig stop' is not restarted", "SIGTERM on stop, 60s to exit"],
            notes=["Needs no administrator rights.", "UNTESTED on real macOS."],
            definition=self.plist_bytes().decode(),
        )

    def _domain(self) -> str:
        return f"gui/{os.getuid()}"

    def _launchctl(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(["launchctl", *args], capture_output=True, text=True)
        if check and proc.returncode != 0:
            raise AutostartError(f"launchctl {' '.join(args)} failed (exit {proc.returncode}): "
                                 f"{(proc.stdout + proc.stderr).strip()}")
        return proc

    def is_registered(self) -> bool:
        return self.plist_path.exists()

    def enable(self, *, start_now: bool = False) -> Plan:
        if self.is_registered():
            raise AutostartError(f"{self.plist_path} already exists; run 'jig autostart disable' first")
        plan = self.plan()
        self.plist_path.parent.mkdir(parents=True, exist_ok=True)
        self.spec.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.plist_path.write_bytes(self.plist_bytes())
        # bootstrap loads the agent, and RunAtLoad starts it straight away.
        if start_now:
            self._launchctl("bootstrap", self._domain(), str(self.plist_path))
        return plan

    def disable(self) -> list[str]:
        removed = []
        if self._launchctl("print", f"{self._domain()}/{self.entry}", check=False).returncode == 0:
            self._launchctl("bootout", f"{self._domain()}/{self.entry}")
            removed.append(f"launchd job {self._domain()}/{self.entry}")
        if self.plist_path.exists():
            self.plist_path.unlink()
            removed.append(str(self.plist_path))
        return removed

    def _query(self) -> dict[str, Any]:
        proc = self._launchctl("print", f"{self._domain()}/{self.entry}", check=False)
        if proc.returncode != 0:
            return {"last_run": None, "last_result": None, "state": "not loaded (starts at next login)"}
        fields = {}
        for line in proc.stdout.splitlines():
            key, sep, value = line.strip().partition(" = ")
            if sep:
                fields[key] = value
        return {"last_run": None, "last_result": fields.get("last exit code"), "state": fields.get("state"),
                "pid": fields.get("pid")}
