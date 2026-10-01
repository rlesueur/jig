"""Compose sandbox backend: Jig runs in a container and uses long-running sandbox services.

``compose.yaml`` runs two services from the sandbox image, ``sandbox-exec`` (commands and code) and
``sandbox-browser`` (headless Chromium), with the same hardening as the per-agent backend. They share
the agent's workspace volume and sit on the ``sandbox`` network, which is ``internal: true``: no
gateway, no route out. Jig is on that network and on the normal one, so:

* Jig reaches the services directly (TCP to ``sandbox_service.py``; no Docker socket is needed);
* the services' only way out is Jig's egress proxy, which listens on Jig's address on the sandbox
  network only (``egress_port``) and applies the same lease, core rules and custom rules as before;
* requests from the sandbox network to Jig's API are refused (``SandboxPeerGuard``).

``start()`` proves all of this before Jig serves anything, and refuses to start if any part fails:
both services answer and report a non-root uid, no capabilities, no-new-privileges and a read-only
root; neither can reach the internet or resolve public names directly; the workspace volume really is
shared; and the proxy answers 403 while no reviewed action holds a lease. There is no fallback.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import socket
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from ..config import SandboxConfig
from ..errors import SandboxUnavailable, ToolError
from ..sandbox_container.egress import EgressProxy, Lease
from . import netinfo

OUTPUT_LIMIT = 20_000
LINE_LIMIT = 64 * 1024 * 1024
SERVICE_WAIT_S = 120.0
BROWSER_START_TIMEOUT_S = 60.0
BROWSER_CALL_TIMEOUT_S = 90.0


def _address(spec: str) -> tuple[str, int]:
    host, _, port = spec.rpartition(":")
    if not host or not port.isdigit():
        raise SandboxUnavailable(f"sandbox service address {spec!r} must be host:port")
    return host, int(port)


async def _request(addr: tuple[str, int], payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    reader, writer = await asyncio.wait_for(asyncio.open_connection(*addr, limit=LINE_LIMIT), 10)
    try:
        writer.write((json.dumps(payload) + "\n").encode())
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout)
    finally:
        writer.close()
    if not line:
        raise SandboxUnavailable(f"the sandbox service at {addr[0]}:{addr[1]} closed the connection without a reply")
    return json.loads(line)


class ComposeSandbox:
    def __init__(self, config: SandboxConfig, workspace: Path, egress: EgressProxy):
        self.config = config
        self.workspace = workspace
        self.egress = egress
        self.exec_addr = _address(config.exec_service)
        self.browser_addr = _address(config.browser_service)
        self.name = config.exec_service
        self.untrusted_networks: list[ipaddress.IPv4Network] = []
        self.started = False
        self._browser: SocketBrowserSession | None = None
        self._browser_lock = asyncio.Lock()

    # Lifecycle -------------------------------------------------------------
    async def start(self) -> dict[str, Any]:
        selftests = {}
        for label, addr in (("exec", self.exec_addr), ("browser", self.browser_addr)):
            selftests[label] = await self._wait_for_service(label, addr)
        addresses = {label: await self._resolve(addr) for label, addr in
                     (("exec", self.exec_addr), ("browser", self.browser_addr))}
        nets = []
        for label, ip in addresses.items():
            net = netinfo.network_of(ip)
            if net is None:
                raise SandboxUnavailable(
                    f"the {label} sandbox service ({ip}) is not on a network directly connected to Jig. Jig must "
                    "be on the sandbox network (see compose.yaml: the jig service lists both networks)")
            if net not in nets:
                nets.append(net)
        self.untrusted_networks = nets
        bind = netinfo.local_address_towards(addresses["exec"])
        self.egress.bind = bind
        try:
            port = await self.egress.start(self.config.egress_port)
        except OSError as exc:
            raise SandboxUnavailable(f"the egress proxy cannot listen on {bind}:{self.config.egress_port}: {exc}") from exc
        self.started = True
        try:
            await self._check_workspace()
            await self._check_proxy(bind, port)
        except BaseException:
            await self.stop()
            raise
        return {"backend": "compose", "exec_service": self.config.exec_service,
                "browser_service": self.config.browser_service, "egress_proxy": f"{bind}:{port}",
                "sandbox_networks": [str(n) for n in nets],
                "services": {k: {f: v[f] for f in ("uid", "cap_eff", "no_new_privs", "root_read_only")}
                             for k, v in selftests.items()}}

    async def _resolve(self, addr: tuple[str, int]) -> str:
        infos = await asyncio.get_running_loop().getaddrinfo(addr[0], addr[1], family=socket.AF_INET,
                                                             type=socket.SOCK_STREAM)
        return infos[0][4][0]

    async def _wait_for_service(self, label: str, addr: tuple[str, int]) -> dict[str, Any]:
        deadline = time.monotonic() + SERVICE_WAIT_S
        while True:
            try:
                report = await _request(addr, {"op": "selftest"}, 30)
                break
            except (OSError, TimeoutError, SandboxUnavailable, ValueError) as exc:
                if time.monotonic() >= deadline:
                    raise SandboxUnavailable(
                        f"the {label} sandbox service at {addr[0]}:{addr[1]} did not answer within "
                        f"{SERVICE_WAIT_S:.0f}s ({type(exc).__name__}: {exc}). Is it running? "
                        "`docker compose ps` and `docker compose logs` show why") from exc
                await asyncio.sleep(2)
        if not report.get("ok"):
            raise SandboxUnavailable(f"the {label} sandbox service refused Jig: {report.get('error')}")
        problems = []
        if report["uid"] == 0 or report["gid"] == 0:
            problems.append(f"runs as uid {report['uid']} / gid {report['gid']} (must not be root)")
        if int(report["cap_eff"], 16) or int(report["cap_bnd"], 16):
            problems.append(f"has capabilities (CapEff {report['cap_eff']}, CapBnd {report['cap_bnd']})")
        if report["no_new_privs"] != "1":
            problems.append("runs without no-new-privileges")
        if not report["root_read_only"]:
            problems.append("has a writable root filesystem")
        if not report["workspace_writable"]:
            problems.append("cannot write to /workspace")
        leaks = {k: v for k, v in report["direct_egress"].items() if not v.startswith("blocked")}
        if leaks:
            problems.append(f"can reach the internet directly ({leaks}); the sandbox network must be internal: true")
        if problems:
            raise SandboxUnavailable(f"the {label} sandbox service is not isolated: " + "; ".join(problems))
        return report

    async def _check_workspace(self) -> None:
        marker = f".jig-workspace-check-{uuid.uuid4().hex}"
        path = self.workspace / marker
        path.write_text(marker, encoding="ascii")
        try:
            result = await self.exec(["cat", f"/workspace/{marker}"], timeout_s=20)
        finally:
            path.unlink(missing_ok=True)
        if result["exit_code"] != 0 or result["stdout"].strip() != marker:
            raise SandboxUnavailable(
                f"the sandbox's /workspace is not Jig's workspace {self.workspace} (got {result['stderr'][-200:]!r}). "
                "Mount the same volume in both; with a different [runtime] agent_id, change the jig service's "
                "workspace mount to match")

    async def _check_proxy(self, bind: str, port: int) -> None:
        """With no lease, a CONNECT through the proxy (by the name the services use) must be refused."""
        proxy = f"http://{bind}:{port}"
        probe = ("import os, socket, urllib.parse as u; p = u.urlsplit(os.environ.get('HTTPS_PROXY') or 'http://none'); "
                 "s = socket.create_connection((p.hostname, p.port), 10); "
                 "s.sendall(b'CONNECT example.com:443 HTTP/1.1\\r\\nHost: example.com:443\\r\\n\\r\\n'); "
                 "print(p.hostname, s.recv(200).decode('latin-1').splitlines()[0])")
        result = await self.exec(["python3", "-c", probe], timeout_s=30)
        line = result["stdout"].strip()
        if result["exit_code"] != 0 or " 403 " not in f"{line} ":
            raise SandboxUnavailable(
                f"the sandbox cannot reach Jig's egress proxy at {proxy} through its HTTPS_PROXY (got {line!r}, "
                f"stderr {result['stderr'][-300:]!r}). Set HTTP(S)_PROXY in compose.yaml to http://jig:"
                f"{self.config.egress_port}")

    async def stop(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None
        self.started = False
        await self.egress.stop()

    # Execution -------------------------------------------------------------
    def _require_started(self) -> None:
        if not self.started:
            raise SandboxUnavailable("the compose sandbox is not running")

    @asynccontextmanager
    async def lease(self, *, tool: str, run_id: str | None = None,
                    task_id: str | None = None) -> AsyncIterator[Lease]:
        async with self.egress.lease(tool=tool, run_id=run_id, task_id=task_id) as lease:
            yield lease

    async def exec(self, argv: list[str], *, stdin: bytes | None = None, timeout_s: float | None = None) -> dict[str, Any]:
        """Run a command in the sandbox-exec service as its unprivileged user, killed after ``timeout_s``."""
        self._require_started()
        limit = int(timeout_s or self.config.command_timeout_s)
        payload = {"op": "exec", "argv": argv, "timeout_s": limit,
                   "stdin": base64.b64encode(stdin or b"").decode()}
        try:
            reply = await _request(self.exec_addr, payload, limit + 30)
        except (OSError, TimeoutError, ValueError) as exc:
            raise ToolError(f"the sandbox-exec service failed: {type(exc).__name__}: {exc}") from exc
        if not reply.get("ok"):
            raise ToolError(f"the sandbox-exec service refused the command: {reply.get('error')}")
        out = base64.b64decode(reply["stdout"]).decode("utf-8", "replace")
        err = base64.b64decode(reply["stderr"]).decode("utf-8", "replace")
        return {
            "exit_code": reply["exit_code"],
            "timed_out": reply["exit_code"] == 137,
            "stdout": out[-OUTPUT_LIMIT:],
            "stderr": err[-OUTPUT_LIMIT:],
            "truncated": len(out) > OUTPUT_LIMIT or len(err) > OUTPUT_LIMIT
                         or reply["stdout_bytes"] > len(out.encode()) or reply["stderr_bytes"] > len(err.encode()),
        }

    async def browser(self) -> SocketBrowserSession:
        self._require_started()
        async with self._browser_lock:
            if self._browser is None or not self._browser.alive:
                session = SocketBrowserSession(self.browser_addr)
                await session.start()
                self._browser = session
            return self._browser


class SocketBrowserSession:
    """``browser_server.py`` in the sandbox-browser service, driven over one TCP connection."""

    def __init__(self, addr: tuple[str, int]):
        self.addr = addr
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

    @property
    def alive(self) -> bool:
        return self._writer is not None and not self._writer.is_closing() and not self._reader.at_eof()

    async def start(self) -> None:
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(*self.addr, limit=LINE_LIMIT), 10)
        except (OSError, TimeoutError) as exc:
            raise ToolError(f"cannot reach the sandbox-browser service at {self.addr[0]}:{self.addr[1]}: {exc}") from exc
        self._writer.write(b'{"op": "browser"}\n')
        await self._writer.drain()
        ready = await self._read(BROWSER_START_TIMEOUT_S)
        if not ready.get("ready"):
            raise ToolError(f"the sandbox browser did not start: {ready}")

    async def _read(self, timeout: float) -> dict[str, Any]:
        assert self._reader
        try:
            line = await asyncio.wait_for(self._reader.readline(), timeout)
        except TimeoutError:
            await self.close()
            raise ToolError(f"the sandbox browser did not answer within {timeout:.0f}s and was restarted") from None
        if not line:
            await self.close()
            raise ToolError("the sandbox browser exited; `docker compose logs sandbox-browser` shows why")
        return json.loads(line)

    async def call(self, command: str, **args: Any) -> dict[str, Any]:
        async with self._lock:
            if not self.alive:
                raise ToolError("the sandbox browser is not running")
            assert self._writer
            self._writer.write((json.dumps({"command": command, **args}) + "\n").encode())
            await self._writer.drain()
            reply = await self._read(BROWSER_CALL_TIMEOUT_S)
        if not reply.get("ok"):
            raise ToolError(f"browser {command} failed: {reply.get('error')}")
        return reply["result"]

    async def close(self) -> None:
        writer, self._writer = self._writer, None
        if writer:
            writer.close()
