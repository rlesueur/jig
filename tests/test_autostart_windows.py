"""Real Task Scheduler tests: register a TEST task, run it, talk to the Jig it starts, then remove it.

Only test entries are used ('\\Jig\\Jig Agent Test', '\\Jig\\Jig Agent API Test'), each with a temporary
data directory, so the user's own autostart setting is never touched.
"""

from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
import pytest

from jig.autostart import LaunchSpec
from jig.autostart.base import current_account
from jig.autostart.windows import WindowsTaskScheduler, current_user_sid
from jig.config import load_config
from jig.db import Database

from .server_helpers import config_path, free_port, kill, start_jig, token, wait_health, wait_stopped

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Task Scheduler backend")

TEST_TASK = "\\Jig\\Jig Agent Test"
API_TEST_TASK = "\\Jig\\Jig Agent API Test"
E2E_PORT = 8767
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def jig_cli(*args: str, stdin: int | None = subprocess.DEVNULL) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "jig.cli", "--config", str(config_path()), *args],
                          capture_output=True, text=True, stdin=stdin, timeout=120)


def schtasks_query(name: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["schtasks", "/Query", "/TN", name, "/XML"], capture_output=True, text=True)


def jig_tasks_listed() -> list[str]:
    out = subprocess.run(["schtasks", "/Query", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line.startswith('"\\Jig\\')]


def remove(name: str, data_dir: Path) -> None:
    backend = WindowsTaskScheduler(LaunchSpec.from_config(load_config(data_dir=data_dir)), entry=name)
    backend.disable()


def audit_kinds(data_dir: Path, prefix: str) -> list[dict]:
    db = Database(data_dir / "jig.db")
    try:
        return db.query("SELECT kind, actor, data_json FROM audit WHERE kind LIKE ? ORDER BY id", (f"{prefix}%",))
    finally:
        db.close()


def test_scheduled_task_end_to_end(tmp_path):
    data_dir = tmp_path / "data"
    assert schtasks_query(TEST_TASK).returncode != 0, f"{TEST_TASK} is left over from an earlier run"
    with socket.socket() as s:  # the port must be free for this test
        s.bind(("127.0.0.1", E2E_PORT))
    common = ["--entry", TEST_TASK, "--data-dir", str(data_dir), "--port", str(E2E_PORT)]
    try:
        # Without --yes and without a terminal to confirm on, nothing is registered.
        refused = jig_cli("autostart", "enable", *common)
        assert refused.returncode == 1 and "--yes" in refused.stderr
        assert "Command line:" in refused.stdout and "Trigger:" in refused.stdout and "Runs as:" in refused.stdout
        assert schtasks_query(TEST_TASK).returncode != 0

        enabled = jig_cli("autostart", "enable", "--yes", *common)
        assert enabled.returncode == 0, enabled.stderr
        print(enabled.stdout)

        # The real registered definition, read back from Task Scheduler.
        q = schtasks_query(TEST_TASK)
        assert q.returncode == 0, q.stderr
        root = ET.fromstring(re.sub(r"^\s*<\?xml[^>]*\?>", "", q.stdout))
        sid = current_user_sid()
        text = lambda path: root.findtext(path, namespaces=NS)  # noqa: E731
        # Registered with the SID; Task Scheduler exports it as DOMAIN\user.
        me = {sid.lower(), current_account().lower()}
        assert text("t:Triggers/t:LogonTrigger/t:UserId").lower() in me
        assert text("t:Principals/t:Principal/t:UserId").lower() in me
        assert text("t:Principals/t:Principal/t:LogonType") == "InteractiveToken"
        # Defaults are omitted on export: no RunLevel means LeastPrivilege (never HighestAvailable).
        assert text("t:Principals/t:Principal/t:RunLevel") in (None, "LeastPrivilege")
        assert text("t:Triggers/t:LogonTrigger/t:Delay") == "PT30S"
        assert text("t:Settings/t:MultipleInstancesPolicy") == "IgnoreNew"
        assert text("t:Settings/t:ExecutionTimeLimit") == "PT0S"
        assert text("t:Settings/t:DisallowStartIfOnBatteries") == "false"
        assert text("t:Settings/t:StopIfGoingOnBatteries") == "false"
        assert text("t:Settings/t:StartWhenAvailable") == "true"
        assert text("t:Settings/t:RestartOnFailure/t:Count") == "3"
        assert text("t:Actions/t:Exec/t:Command").lower().endswith("pythonw.exe")
        args = text("t:Actions/t:Exec/t:Arguments")
        assert "jig.autostart.launch" in args and f"--port {E2E_PORT}" in args and str(data_dir) in args
        print("schtasks /Query /XML:", {"logon_user": text("t:Triggers/t:LogonTrigger/t:UserId"),
                                        "logon_type": text("t:Principals/t:Principal/t:LogonType"),
                                        "run_level": text("t:Principals/t:Principal/t:RunLevel"),
                                        "time_limit": text("t:Settings/t:ExecutionTimeLimit"), "args": args})

        # Run it for real and wait for the Jig it starts.
        run = subprocess.run(["schtasks", "/Run", "/TN", TEST_TASK], capture_output=True, text=True)
        assert run.returncode == 0, run.stderr
        wait_health(E2E_PORT, timeout=240)
        headers = token(data_dir)
        status = httpx.get(f"http://127.0.0.1:{E2E_PORT}/status", headers=headers, timeout=30).json()
        assert status["start_reason"] == "autostart"
        assert httpx.get(f"http://127.0.0.1:{E2E_PORT}/state", headers=headers).json()["start_reason"] == "autostart"
        starts = httpx.get(f"http://127.0.0.1:{E2E_PORT}/audit", params={"kind": "runtime.start"},
                           headers=headers).json()
        assert starts[-1]["data"]["start_reason"] == "autostart"
        assert httpx.get(f"http://127.0.0.1:{E2E_PORT}/autostart").status_code == 401  # no token

        st = jig_cli("autostart", "status", "--json", *common)
        assert st.returncode == 0, st.stderr
        st = json.loads(st.stdout)
        print("status while running:", {k: st[k] for k in ("registered", "state", "last_result", "running", "answering")})
        assert st["registered"] and st["running"] and st["answering"]
        assert st["state"] == "Running" and "currently running" in st["last_result"]
        assert st["instance"]["start_reason"] == "autostart"
        assert (data_dir / "logs" / "jig.log").stat().st_size > 0
        assert "starting Jig" in (data_dir / "logs" / "autostart.log").read_text(encoding="utf-8")

        stop = jig_cli("stop", "--data-dir", str(data_dir))
        assert stop.returncode == 0, stop.stderr
        wait_stopped(data_dir)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            st = json.loads(jig_cli("autostart", "status", "--json", *common).stdout)
            if st["state"] == "Ready":
                break
            time.sleep(1)
        print("status after 'jig stop':", {k: st[k] for k in ("state", "last_result", "running")})
        assert st["last_result"].startswith("0x0 ") and not st["running"]
        assert "Jig stopped cleanly" in (data_dir / "logs" / "autostart.log").read_text(encoding="utf-8")

        disabled = jig_cli("autostart", "disable", *common)
        assert disabled.returncode == 0, disabled.stderr
        print(disabled.stdout)
        assert f"Task Scheduler task {TEST_TASK}" in disabled.stdout
        assert schtasks_query(TEST_TASK).returncode != 0
        assert TEST_TASK not in "\n".join(jig_tasks_listed())

        kinds = [r["kind"] for r in audit_kinds(data_dir, "autostart.")]
        assert kinds == ["autostart.enabled", "autostart.disabled"]
    finally:
        if schtasks_query(TEST_TASK).returncode == 0:
            subprocess.run(["schtasks", "/End", "/TN", TEST_TASK], capture_output=True)
            remove(TEST_TASK, data_dir)


def test_api_enable_needs_confirm_and_auth(tmp_path):
    data_dir, port = tmp_path / "data", free_port()
    proc, log = start_jig(data_dir, port, env={"JIG_AUTOSTART_ENTRY": API_TEST_TASK})
    base = f"http://127.0.0.1:{port}"
    try:
        wait_health(port, proc=proc, log=log)
        headers = token(data_dir)
        assert httpx.get(f"{base}/autostart").status_code == 401
        assert httpx.post(f"{base}/autostart/enable", json={"confirm": True}).status_code == 401

        info = httpx.get(f"{base}/autostart", headers=headers, timeout=60).json()
        assert info["status"]["registered"] is False and info["plan"]["entry"] == API_TEST_TASK
        assert info["plan"]["trigger"].startswith("at logon of") and info["plan"]["command_line"]

        assert httpx.post(f"{base}/autostart/enable", headers=headers, json={}).status_code == 422
        r = httpx.post(f"{base}/autostart/enable", headers=headers, json={"confirm": False})
        assert r.status_code == 400 and "confirm" in r.json()["error"]
        assert schtasks_query(API_TEST_TASK).returncode != 0

        r = httpx.post(f"{base}/autostart/enable", headers=headers, json={"confirm": True}, timeout=60)
        assert r.status_code == 200, r.text
        assert schtasks_query(API_TEST_TASK).returncode == 0
        assert httpx.post(f"{base}/autostart/enable", headers=headers, json={"confirm": True},
                          timeout=60).status_code == 409

        r = httpx.post(f"{base}/autostart/disable", headers=headers, timeout=60)
        assert r.status_code == 200 and f"Task Scheduler task {API_TEST_TASK}" in r.json()["removed"]
        assert schtasks_query(API_TEST_TASK).returncode != 0

        audit = httpx.get(f"{base}/audit", params={"kind": "autostart"}, headers=headers).json()
        assert [(a["kind"], a["data"]["via"]) for a in audit] == [("autostart.enabled", "api"),
                                                                   ("autostart.disabled", "api")]
    finally:
        if schtasks_query(API_TEST_TASK).returncode == 0:
            remove(API_TEST_TASK, data_dir)
        kill(proc)


def test_no_jig_test_tasks_left():
    leftovers = [t for t in jig_tasks_listed() if "Test" in t]
    assert not leftovers, leftovers
