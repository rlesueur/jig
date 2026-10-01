"""Container sandbox: isolation, limits and gated egress, against real Docker. Nothing is mocked.

Needs Docker running and the image built (`jig sandbox build`). Some tests need internet access.
"""

from __future__ import annotations

import dataclasses
import json
import uuid

import pytest

from jig.config import load_config
from jig.constants import ApprovalStatus, Mode, TaskStatus
from jig.errors import SandboxUnavailable
from jig.runtime import Jig
from jig.sandbox_container.docker import docker

from .conftest import audit_kinds, wait_for
from .sandbox_helpers import pending_approval

PROXY_FETCH = """
import sys, urllib.request, urllib.error
try:
    r = urllib.request.urlopen(sys.argv[1], timeout=25)
    print("STATUS", r.status)
except urllib.error.HTTPError as e:
    print("STATUS", e.code, e.read().decode(errors="replace").strip())
except Exception as e:
    print("ERROR", type(e).__name__, e)
"""


@pytest.fixture
def container_config(tmp_path):
    return load_config(data_dir=tmp_path / "data", sandbox_dir=tmp_path / "sandbox", sandbox_backend="container")


@pytest.fixture
async def cjig(container_config, capabilities):
    runtime = Jig(container_config)
    await runtime.start(run_scheduler=False, check_capabilities=False)
    try:
        yield runtime
    finally:
        await runtime.stop()


async def sh(jig: Jig, script: str) -> dict:
    return await jig.container.exec(["sh", "-c", script], timeout_s=60)


async def fetch(jig: Jig, url: str) -> str:
    result = await jig.container.exec(["python3", "-c", PROXY_FETCH, url], timeout_s=60)
    return result["stdout"].strip()


def inspect(name: str) -> dict:
    return json.loads(docker("inspect", name).stdout)[0]


async def test_runs_unprivileged_with_a_read_only_root(cjig):
    result = await sh(cjig, "id -u; id -g; grep -E '^(CapEff|CapBnd|NoNewPrivs)' /proc/self/status; "
                            "touch /etc/jig-test 2>&1; touch /usr/jig-test 2>&1; touch /tmp/ok && echo tmp-writable")
    out = result["stdout"]
    assert out.splitlines()[:2] == ["10001", "10001"]
    assert "CapEff:\t0000000000000000" in out and "CapBnd:\t0000000000000000" in out
    assert "NoNewPrivs:\t1" in out
    assert out.count("Read-only file system") == 2
    assert "tmp-writable" in out


async def test_only_the_workspace_is_visible(cjig, tmp_path):
    marker = f"jig-host-secret-{uuid.uuid4().hex}"
    (tmp_path / f"{marker}.txt").write_text("outside the workspace", encoding="utf-8")
    (cjig.sandbox.root / "inside.txt").write_text("hello from the host", encoding="utf-8")

    result = await sh(cjig, f"cat /workspace/inside.txt; echo; find / -name '{marker}*' 2>/dev/null | head -1; "
                            "echo written > /workspace/from-container.txt")
    assert result["stdout"].strip() == "hello from the host"
    assert (cjig.sandbox.root / "from-container.txt").read_text(encoding="utf-8").strip() == "written"

    mounts = [m for m in inspect(cjig.container.name)["Mounts"] if m["Type"] == "bind"]
    assert len(mounts) == 1 and mounts[0]["Destination"] == "/workspace"


async def test_hardening_and_limits_are_applied(cjig):
    host = inspect(cjig.container.name)["HostConfig"]
    assert host["ReadonlyRootfs"] is True
    assert host["CapDrop"] == ["ALL"] and not host["CapAdd"]
    assert "no-new-privileges" in host["SecurityOpt"]
    assert host["Memory"] == 2 * 1024**3 and host["MemorySwap"] == host["Memory"]
    assert host["NanoCpus"] == 2_000_000_000
    assert host["PidsLimit"] == 512
    assert set(host["Tmpfs"]) == {"/tmp", "/home/jig"}
    assert host["Privileged"] is False
    network = json.loads(docker("network", "inspect", cjig.container.network).stdout)[0]
    assert network["Internal"] is True


async def test_direct_egress_is_blocked(cjig):
    script = """
import socket
for target in [("1.1.1.1", 443), ("8.8.8.8", 53)]:
    try:
        socket.create_connection(target, 5); print("CONNECTED", target)
    except OSError as e:
        print("BLOCKED", target, type(e).__name__)
try:
    socket.getaddrinfo("example.com", 443); print("RESOLVED")
except OSError as e:
    print("NO-DNS", type(e).__name__)
"""
    result = await cjig.container.exec(["python3", "-c", script], timeout_s=60)
    out = result["stdout"]
    assert "CONNECTED" not in out and out.count("BLOCKED") == 2, out
    assert "NO-DNS" in out, out


async def test_egress_is_closed_without_a_reviewed_action(cjig):
    out = await fetch(cjig, "https://example.com/")
    assert out.startswith("ERROR") or "403" in out, out
    blocks = cjig.audit.query(kind="egress.block")
    assert blocks and "no reviewed sandbox action" in json.loads(blocks[-1]["data_json"])["reason"]


@pytest.mark.network
async def test_gated_egress_allows_public_and_blocks_local(cjig):
    async with cjig.container.lease(tool="test"):
        allowed = await fetch(cjig, "https://example.com/")
        loopback = await fetch(cjig, "http://127.0.0.1:8766/health")
        private = await fetch(cjig, "http://192.168.1.1/")
        private_10 = await fetch(cjig, "http://10.0.0.1/")
        model = await fetch(cjig, "http://localhost:8080/v1/models")
        port = await fetch(cjig, "http://example.com:8080/")
    assert allowed == "STATUS 200", allowed
    for out in (loopback, private, private_10, model):
        assert "403" in out and "no-local-network" in out, out
    assert "403" in port and "port 8080 is not allowed" in port, port
    kinds = audit_kinds(cjig, kind="egress")
    assert "egress.allow" in kinds and "egress.block" in kinds


@pytest.mark.network
async def test_custom_rule_can_block_an_egress_host(cjig):
    cjig.rules.create(tool="egress", decision="block", arg="host", pattern="example.com")
    async with cjig.container.lease(tool="test"):
        out = await fetch(cjig, "https://example.com/")
    assert "403" in out, out
    reason = json.loads(cjig.audit.query(kind="egress.block")[-1]["data_json"])["reason"]
    assert "blocks egress to example.com" in reason, reason


async def test_run_command_needs_approval(cjig):
    assert cjig.registry.get("run_command").describe()["default_decision"] == "ask"
    cjig.scheduler.start()
    task = cjig.create_task(
        title="Container proof",
        description="Use the run_command tool to run exactly this shell command: echo jig-container-ok > proof.txt",
        mode=Mode.ACTION,
    )
    approval = await pending_approval(cjig, "run_command")
    assert any(r["decision"] == "ask" for r in approval["reasons"])
    assert cjig.store.get_task(task["id"])["status"] == TaskStatus.WAITING_APPROVAL
    assert not (cjig.sandbox.root / "proof.txt").exists(), "nothing may run before approval"

    cjig.approvals.respond(approval["id"], approve=True, note="go ahead")
    await wait_for(lambda: cjig.store.get_task(task["id"])["status"] in (TaskStatus.DONE, TaskStatus.FAILED),
                   what="task to finish")
    final = cjig.store.get_task(task["id"])
    assert final["status"] == TaskStatus.DONE, final["error"]
    assert (cjig.sandbox.root / "proof.txt").read_text(encoding="utf-8").strip() == "jig-container-ok"
    assert cjig.approvals.get(approval["id"])["status"] == ApprovalStatus.APPROVED
    assert "sentinel.verdict" in audit_kinds(cjig, task_id=task["id"])


async def test_missing_image_fails_loudly(container_config):
    broken = dataclasses.replace(container_config,
                                 sandbox=dataclasses.replace(container_config.sandbox, image="jig-sandbox:absent"))
    runtime = Jig(broken)
    try:
        with pytest.raises(SandboxUnavailable, match="jig sandbox build"):
            await runtime.container.start()
    finally:
        await runtime.container.stop()
        runtime.db.close()


async def test_unreachable_docker_daemon_fails_loudly(container_config, monkeypatch):
    # A real, unreachable daemon endpoint: the docker CLI genuinely fails to connect.
    monkeypatch.setenv("DOCKER_HOST", "tcp://127.0.0.1:1")
    runtime = Jig(container_config)
    try:
        with pytest.raises(SandboxUnavailable, match="Docker daemon is not running"):
            await runtime.container.start()
    finally:
        await runtime.container.stop()
        runtime.db.close()
