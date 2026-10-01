"""The container deployment (compose.yaml), against a REAL running stack. Nothing is mocked.

Start the stack first, then point the tests at it:

    docker compose up -d
    $env:JIG_STACK_URL = "http://127.0.0.1:8766"; .\\.venv\\Scripts\\python -m pytest -q -m compose

Use the same COMPOSE_PROJECT_NAME (and other compose variables) as the stack. Without JIG_STACK_URL the
module is skipped. Some tests need internet access and the real model server.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
STACK_URL = os.environ.get("JIG_STACK_URL", "").rstrip("/")

pytestmark = [pytest.mark.compose,
              pytest.mark.skipif(not STACK_URL, reason="set JIG_STACK_URL to a running compose stack")]

ESCAPE = """
import socket, urllib.request, urllib.error
for target in [("1.1.1.1", 443), ("8.8.8.8", 53)]:
    try:
        socket.create_connection(target, 5); print("CONNECTED", target)
    except OSError as e:
        print("BLOCKED", target, e.errno)
try:
    socket.getaddrinfo("example.com", 443); print("RESOLVED")
except OSError:
    print("NO-DNS")
try:
    r = urllib.request.build_opener(urllib.request.ProxyHandler({})).open("http://jig:8766/health", timeout=5)
    print("API", r.status)
except urllib.error.HTTPError as e:
    print("API", e.code)
"""

PEER = """
import json, socket
s = socket.create_connection(("sandbox-exec", 7010), 5)
s.sendall(json.dumps({"op": "exec", "argv": ["id"]}).encode() + b"\\n")
print(s.makefile().readline().strip())
"""


def compose(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", "compose", *args], cwd=ROOT, input=stdin, capture_output=True, text=True,
                          timeout=120, check=True)


@pytest.fixture(scope="module")
def api() -> httpx.Client:
    token = compose("exec", "-T", "jig", "jig", "token", "show").stdout.strip()
    with httpx.Client(base_url=STACK_URL, headers={"Authorization": f"Bearer {token}"}, timeout=30) as client:
        yield client


def run_task(api: httpx.Client, description: str, mode: str, *, approve: bool, timeout: float = 600) -> dict:
    task = api.post("/tasks", json={"title": "compose stack test", "description": description, "mode": mode}).json()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if approve:
            for a in api.get("/approvals", params={"status": "pending"}).json():
                if a["task_id"] == task["id"]:
                    api.post(f"/approvals/{a['id']}", json={"approve": True, "note": "compose test"}).raise_for_status()
        current = api.get(f"/tasks/{task['id']}").json()
        if current["status"] in ("done", "failed", "blocked", "cancelled"):
            return current
        time.sleep(2)
    raise AssertionError(f"task {task['id']} did not finish within {timeout:.0f}s")


def audit(api: httpx.Client, **params) -> list[dict]:
    return api.get("/audit", params={"limit": 500, **params}).json()


def test_health_is_public_and_everything_else_needs_the_token(api):
    assert httpx.get(f"{STACK_URL}/health", timeout=10).json() == {"status": "ok"}
    assert httpx.get(f"{STACK_URL}/status", timeout=10).status_code == 401
    status = api.get("/status").json()
    assert status["vault_backend"] == "keyfile"
    sandbox = status["capabilities"]["sandbox"]
    assert sandbox["backend"] == "compose"
    for service in sandbox["services"].values():
        assert service["uid"] != 0 and service["cap_eff"] == "0000000000000000"
        assert service["no_new_privs"] == "1" and service["root_read_only"] is True
    assert status["capabilities"]["agent"]["tool_calling"] is True


@pytest.mark.parametrize("service", ["sandbox-exec", "sandbox-browser"])
def test_sandbox_services_cannot_reach_the_internet_or_jigs_api(service):
    out = compose("exec", "-T", service, "python3", "-", stdin=ESCAPE).stdout
    assert "CONNECTED" not in out and out.count("BLOCKED") == 2, out
    assert "NO-DNS" in out, out
    assert "API 403" in out, out


def test_sandbox_services_only_serve_jig():
    reply = json.loads(compose("exec", "-T", "sandbox-browser", "python3", "-", stdin=PEER).stdout)
    assert reply["ok"] is False and "is not Jig" in reply["error"]


def test_the_sandbox_networks_are_internal():
    project = json.loads(compose("config", "--format", "json").stdout)
    assert project["networks"]["sandbox"]["internal"] is True
    for name in ("sandbox-exec", "sandbox-browser"):
        svc = project["services"][name]
        assert list(svc["networks"]) == ["sandbox"], svc["networks"]
        assert svc["read_only"] is True and svc["cap_drop"] == ["ALL"]
    assert set(project["services"]["jig"]["networks"]) == {"default", "sandbox"}
    published = project["services"]["jig"]["ports"][0]
    assert published["host_ip"] == "127.0.0.1"


@pytest.mark.network
def test_approved_command_reaches_public_hosts_only_through_the_proxy(api):
    before = max([r["id"] for r in audit(api, newest_first=True, limit=1)] or [0])
    script = ("import urllib.request as u, urllib.error as e\n"
              "for url in ['https://example.com/', 'http://host.docker.internal:8080/v1/models']:\n"
              "    try: print('URL', url, u.urlopen(url, timeout=20).status)\n"
              "    except e.HTTPError as x: print('URL', url, x.code)\n"
              "    except OSError as x: print('URL', url, 'ERROR', x)\n")
    task = run_task(api, "Use the run_python tool to run exactly this Python code and report its output verbatim:\n"
                         + script, "action", approve=True)
    assert task["status"] == "done", task["error"]
    egress = [r for r in audit(api, kind="egress", after_id=before) if r["data"].get("tool") == "run_python"]
    allowed = {r["data"]["host"] for r in egress if r["kind"] == "egress.allow"}
    blocked = {r["data"]["host"]: r["data"]["reason"] for r in egress if r["kind"] == "egress.block"}
    assert "example.com" in allowed, egress
    assert "no-local-network" in blocked.get("host.docker.internal", ""), egress
    approvals = [r for r in audit(api, task_id=task["id"]) if r["kind"].startswith("approval")]
    assert approvals, "run_python must have needed an approval"
