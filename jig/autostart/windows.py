"""Windows backend: a per-user Task Scheduler task that starts Jig at logon. Tested on Windows 11.

Why a logon task and not a service: the vault uses DPAPI, which needs the user's profile and logon
session. A Windows service runs in session 0 under a service account (or with stored credentials and
no interactive profile), so it could not decrypt the user's secrets. The task therefore runs as the
user, with their normal (non-elevated) rights, only while they are logged on, and needs no admin to
register. The cost is that Jig starts at logon, not at boot.

The task's action is ``pythonw.exe -m jig.autostart.launch ...`` (no console window). The launcher
runs ``jig serve`` as a child and restarts it if it fails, because Task Scheduler's own
restart-on-failure only covers a failure to launch the action: a non-zero exit code does not trigger
it (checked on Windows 11). The task keeps that setting too, for launch failures.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from .base import AutostartBackend, AutostartError, LaunchSpec, Plan, current_account

DEFAULT_TASK = "\\Jig\\Jig Agent"
LOGON_DELAY = "PT30S"
LAUNCHER_RETRIES = 3
LAUNCHER_RETRY_DELAY_S = 60

_RESULTS = {0: "success", 0x41300: "ready", 0x41301: "currently running", 0x41302: "disabled",
            0x41303: "has not run yet", 0x41306: "terminated by the user", 0x8004131F: "an instance is already running",
            0x800710E0: "the operator or administrator refused the request", 1: "failed (exit code 1)",
            75: "another Jig already uses the data directory"}


def _run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(args, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    if check and proc.returncode != 0:
        raise AutostartError(f"{subprocess.list2cmdline(args)} failed (exit {proc.returncode}): "
                             f"{(proc.stdout + proc.stderr).strip()}")
    return proc


def _powershell(script: str) -> str:
    return _run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]).stdout.strip()


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def current_user_sid() -> str:
    out = _run(["whoami", "/user", "/fo", "csv", "/nh"]).stdout.strip()
    sid = out.rsplit(",", 1)[-1].strip().strip('"')
    if not sid.startswith("S-1-"):
        raise AutostartError(f"could not read the current user's SID from whoami: {out!r}")
    return sid


def describe_result(code: int | None) -> str | None:
    if code is None:
        return None
    text = _RESULTS.get(code & 0xFFFFFFFF)
    return f"{code:#x} ({text})" if text else f"{code:#x}"


class WindowsTaskScheduler(AutostartBackend):
    backend_name = "Windows Task Scheduler (per-user logon task)"
    tested = True
    default_entry = DEFAULT_TASK

    def __init__(self, spec: LaunchSpec, *, entry: str | None = None):
        super().__init__(spec, entry=entry)
        if not self.entry.startswith("\\") or self.entry.endswith("\\"):
            raise AutostartError(f"task name must look like '\\Folder\\Name', not {self.entry!r}")
        folder, _, self.task_leaf = self.entry.rpartition("\\")
        self.task_folder = folder or "\\"

    # What gets registered ---------------------------------------------------------------------
    @property
    def pythonw(self) -> Path:
        exe = self.spec.python.with_name("pythonw.exe")
        if not exe.is_file():
            raise AutostartError(f"{exe} does not exist; Jig needs pythonw.exe next to {self.spec.python} so it can "
                                 "run without a console window")
        return exe

    def action_args(self) -> list[str]:
        return ["-m", "jig.autostart.launch", "--config", str(self.spec.config_path), "--data-dir",
                str(self.spec.data_dir), "--port", str(self.spec.port), "--retries", str(LAUNCHER_RETRIES),
                "--retry-delay", str(LAUNCHER_RETRY_DELAY_S)]

    def task_xml(self, sid: str) -> str:
        e = escape
        workdir = self.spec.config_path.parent
        return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>{e(current_account())}</Author>
    <Description>Starts the Jig personal AI agent at logon. Registered by 'jig autostart enable'; remove it with 'jig autostart disable'.</Description>
    <URI>{e(self.entry)}</URI>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{e(sid)}</UserId>
      <Delay>{LOGON_DELAY}</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{e(sid)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>5</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{e(str(self.pythonw))}</Command>
      <Arguments>{e(subprocess.list2cmdline(self.action_args()))}</Arguments>
      <WorkingDirectory>{e(str(workdir))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""

    def plan(self) -> Plan:
        sid = current_user_sid()
        return Plan(
            backend=self.backend_name, entry=self.entry, tested=self.tested,
            command_line=subprocess.list2cmdline([str(self.pythonw), *self.action_args()]),
            trigger=f"at logon of {current_account()} only, after a {LOGON_DELAY[2:].lower()} delay",
            account=f"{current_account()} ({sid}), interactive token, least privilege (not elevated); "
                    "runs only while you are logged on; no password is stored",
            log_path=str(self.spec.log_path),
            files=[f"Task Scheduler task {self.entry}", f"{self.spec.data_dir / 'logs'} (jig.log, autostart.log; "
                   "rotated at 5 MB, 5 kept)"],
            settings=["no console window (pythonw.exe)",
                      f"the launcher restarts Jig up to {LAUNCHER_RETRIES} times, {LAUNCHER_RETRY_DELAY_S}s apart, "
                      "if it exits with an error; a clean stop ('jig stop') is not restarted",
                      "Task Scheduler restart on launch failure: every 1 minute, up to 3 times",
                      "no execution time limit", "starts as soon as possible if a start was missed",
                      "runs on battery and keeps running when the power is unplugged",
                      "one instance at a time (a second start is ignored)"],
            notes=["Needs no administrator rights.",
                   "Runs as you so the vault can use DPAPI; a Windows service in session 0 could not.",
                   f"Jig listens on http://{self.spec.host}:{self.spec.port} with data in {self.spec.data_dir}."],
            definition=self.task_xml(sid),
        )

    # Registration -----------------------------------------------------------------------------
    def query_xml(self) -> str | None:
        proc = _run(["schtasks", "/Query", "/TN", self.entry, "/XML"], check=False)
        return proc.stdout if proc.returncode == 0 else None

    def is_registered(self) -> bool:
        return self.query_xml() is not None

    def enable(self, *, start_now: bool = False) -> Plan:
        if self.is_registered():
            raise AutostartError(f"the task {self.entry} is already registered; run 'jig autostart disable' first "
                                 "to replace it")
        plan = self.plan()
        self.spec.log_path.parent.mkdir(parents=True, exist_ok=True)
        xml_path = self.spec.data_dir / "logs" / "autostart-task.xml"
        xml_path.write_text(plan.definition, encoding="utf-16")
        try:
            _run(["schtasks", "/Create", "/TN", self.entry, "/XML", str(xml_path)])
        finally:
            xml_path.unlink(missing_ok=True)
        if start_now:
            self.run_now()
        return plan

    def run_now(self) -> None:
        _run(["schtasks", "/Run", "/TN", self.entry])

    def disable(self) -> list[str]:
        removed: list[str] = []
        if self.is_registered():
            _run(["schtasks", "/Delete", "/TN", self.entry, "/F"])
            if self.is_registered():
                raise AutostartError(f"schtasks reported success but {self.entry} is still registered")
            removed.append(f"Task Scheduler task {self.entry}")
        if self.task_folder != "\\" and self._delete_folder_if_empty():
            removed.append(f"empty Task Scheduler folder {self.task_folder}")
        return removed

    def _delete_folder_if_empty(self) -> bool:
        folder = self.task_folder
        parent, _, leaf = folder.rpartition("\\")
        out = _powershell(
            "$s = New-Object -ComObject Schedule.Service; $s.Connect(); "
            f"try {{ $f = $s.GetFolder({_ps_quote(folder)}) }} catch {{ 'absent'; exit 0 }}; "
            "if ($f.GetTasks(1).Count -eq 0 -and $f.GetFolders(0).Count -eq 0) { "
            f"$s.GetFolder({_ps_quote(parent or chr(92))}).DeleteFolder({_ps_quote(leaf)}, 0); 'deleted' }} "
            "else { 'kept' }")
        return out == "deleted"

    def _query(self) -> dict[str, Any]:
        out = _powershell(
            f"$t = Get-ScheduledTask -TaskPath {_ps_quote(self.task_folder.rstrip(chr(92)) + chr(92))} "
            f"-TaskName {_ps_quote(self.task_leaf)} -ErrorAction Stop; $i = $t | Get-ScheduledTaskInfo; "
            "[pscustomobject]@{ State = \"$($t.State)\"; LastRunTime = $i.LastRunTime.ToString('o'); "
            "LastTaskResult = $i.LastTaskResult; NextRunTime = $(if ($i.NextRunTime) { $i.NextRunTime.ToString('o') }); "
            "Missed = $i.NumberOfMissedRuns } | ConvertTo-Json -Compress")
        info = json.loads(out)
        code = int(info["LastTaskResult"])
        never = code == 0x41303
        return {"last_run": None if never else info["LastRunTime"], "last_result": describe_result(code),
                "state": info["State"], "last_result_code": code, "missed_runs": info["Missed"]}
