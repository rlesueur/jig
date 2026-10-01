"""Container sandbox backend: a hardened per-agent Docker container for code, shell and the browser.

Layout for one agent workspace (``key`` = agent id + a hash of the workspace path):

    jig-sbx-net-<key>   internal Docker network: no gateway, no route out
    jig-sbx-<key>       the sandbox: workspace mounted at /workspace, non-root, read-only root,
                        no capabilities, no-new-privileges, CPU/memory/pids limits, no external DNS
    jig-egress-<key>    relay on the internal network (alias jig-egress) and the default bridge;
                        forwards every connection to the runtime's egress proxy and nowhere else

If this backend is selected and Docker or the image is missing, start-up fails. There is no fallback.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from ..config import SandboxConfig
from ..errors import SandboxUnavailable, ToolError
from .docker import adocker, docker_path, require_daemon
from .egress import EgressProxy, Lease

SANDBOX_UID = "10001:10001"
RELAY_PORT = 3128
RELAY_ALIAS = "jig-egress"
PROXY_URL = f"http://{RELAY_ALIAS}:{RELAY_PORT}"
OUTPUT_LIMIT = 20_000
BROWSER_START_TIMEOUT_S = 60.0
BROWSER_CALL_TIMEOUT_S = 90.0


class ContainerSandbox:
    def __init__(self, config: SandboxConfig, workspace: Path, agent_id: str, egress: EgressProxy):
        self.config = config
        self.workspace = workspace
        self.egress = egress
        key = f"{agent_id}-{hashlib.sha1(str(workspace).lower().encode()).hexdigest()[:10]}".lower()
        self.name = f"jig-sbx-{key}"
        self.relay = f"jig-egress-{key}"
        self.network = f"jig-sbx-net-{key}"
        self.started = False
        self._browser: BrowserSession | None = None
        self._browser_lock = asyncio.Lock()

    # Lifecycle -------------------------------------------------------------
    async def start(self) -> dict[str, Any]:
        version = await asyncio.to_thread(require_daemon)
        inspect = await adocker("image", "inspect", self.config.image, check=False)
        if inspect.returncode != 0:
            raise SandboxUnavailable(f"sandbox image {self.config.image!r} is not built; run `jig sandbox build`")
        port = await self.egress.start()
        try:
            return await self._create(port, version)
        except BaseException:
            await self._remove()
            await self.egress.stop()
            raise

    async def _create(self, port: int, version: str) -> dict[str, Any]:
        await self._remove()
        label = ["--label", "jig.sandbox=1", "--label", f"jig.workspace={self.workspace}"]
        harden = ["--user", SANDBOX_UID, "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--read-only"]
        await adocker("network", "create", "--internal", *label, self.network)
        await adocker(
            "run", "-d", "--name", self.relay, *label, *harden, "--memory", "128m", "--cpus", "0.5",
            "--pids-limit", "64", "--add-host", "host.docker.internal:host-gateway",
            self.config.image, "python3", "-u", "/opt/jig/relay.py", str(RELAY_PORT), "host.docker.internal", str(port),
        )
        await adocker("network", "connect", "--alias", RELAY_ALIAS, self.network, self.relay)
        proxy_env = [x for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
                     for x in ("-e", f"{var}={PROXY_URL}")]
        await adocker(
            "run", "-d", "--name", self.name, *label, *harden, "--init", "--network", self.network,
            "--dns", "127.0.0.1",
            "--tmpfs", f"/tmp:rw,nosuid,nodev,size={self.config.tmp_size}",
            "--tmpfs", "/home/jig:rw,nosuid,nodev,size=256m,mode=1777",
            "--shm-size", self.config.shm_size,
            "--memory", self.config.memory, "--memory-swap", self.config.memory,
            "--cpus", str(self.config.cpus), "--pids-limit", str(self.config.pids_limit),
            "-e", "HOME=/home/jig", *proxy_env, "-e", "NO_PROXY=", "-e", f"JIG_BROWSER_PROXY={PROXY_URL}",
            "-v", f"{self.workspace}:/workspace:rw", "-w", "/workspace",
            self.config.image, "sleep", "infinity",
        )
        self.started = True
        await self._self_check()
        return {"backend": "container", "docker": version, "container": self.name, "image": self.config.image,
                "egress_proxy": f"{self.egress.bind}:{port}"}

    async def _self_check(self) -> None:
        """Prove the only path out works end to end: with no lease, the proxy must answer 403."""
        probe = ("import socket; s = socket.create_connection(('jig-egress', 3128), 10); "
                 "s.sendall(b'CONNECT example.com:443 HTTP/1.1\\r\\nHost: example.com:443\\r\\n\\r\\n'); "
                 "print(s.recv(200).decode('latin-1').splitlines()[0])")
        result = await self.exec(["python3", "-c", probe], timeout_s=30)
        line = result["stdout"].strip()
        if result["exit_code"] != 0 or " 403 " not in f"{line} ":
            raise SandboxUnavailable(
                f"the sandbox cannot reach Jig's egress proxy through the relay (got {line!r}, "
                f"stderr {result['stderr'][-300:]!r}). Check that host.docker.internal reaches "
                f"{self.egress.bind} on the host, or set [sandbox] egress_bind"
            )

    async def _remove(self) -> None:
        await adocker("rm", "-f", self.name, self.relay, check=False)
        await adocker("network", "rm", self.network, check=False)

    async def stop(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self.started:
            await self._remove()
            self.started = False
        await self.egress.stop()

    # Execution -------------------------------------------------------------
    def _require_started(self) -> None:
        if not self.started:
            raise SandboxUnavailable("the container sandbox is not running")

    @asynccontextmanager
    async def lease(self, *, tool: str, run_id: str | None = None,
                    task_id: str | None = None) -> AsyncIterator[Lease]:
        async with self.egress.lease(tool=tool, run_id=run_id, task_id=task_id) as lease:
            yield lease

    async def exec(self, argv: list[str], *, stdin: bytes | None = None, timeout_s: float | None = None) -> dict[str, Any]:
        """Run a command in the sandbox as the unprivileged user. ``timeout`` kills it inside the container too."""
        self._require_started()
        limit = timeout_s or self.config.command_timeout_s
        cmd = ["exec", "-i", "-w", "/workspace", self.name, "timeout", "-s", "KILL", str(int(limit)), *argv]
        proc = await adocker(*cmd, timeout=limit + 30, check=False, stdin=stdin or b"")
        out = proc.stdout.decode("utf-8", "replace")
        err = proc.stderr.decode("utf-8", "replace")
        return {
            "exit_code": proc.returncode,
            "timed_out": proc.returncode == 137,
            "stdout": out[-OUTPUT_LIMIT:],
            "stderr": err[-OUTPUT_LIMIT:],
            "truncated": len(out) > OUTPUT_LIMIT or len(err) > OUTPUT_LIMIT,
        }

    async def browser(self) -> BrowserSession:
        self._require_started()
        async with self._browser_lock:
            if self._browser is None or not self._browser.alive:
                session = BrowserSession(self.name)
                await session.start()
                self._browser = session
            return self._browser


class BrowserSession:
    """A long-lived ``browser_server.py`` in the container, driven over ``docker exec -i`` stdin/stdout."""

    def __init__(self, container: str):
        self.container = container
        self._proc: subprocess.Popen[bytes] | None = None
        self._stderr: deque[str] = deque(maxlen=40)
        self._lock = asyncio.Lock()

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    async def start(self) -> None:
        self._proc = subprocess.Popen(
            [docker_path(), "exec", "-i", self.container, "python3", "-u", "/opt/jig/browser_server.py"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        asyncio.get_running_loop().run_in_executor(None, self._drain_stderr)
        ready = await self._read(BROWSER_START_TIMEOUT_S)
        if not ready.get("ready"):
            raise ToolError(f"the sandbox browser did not start: {ready}")

    def _drain_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        for raw in self._proc.stderr:
            self._stderr.append(raw.decode("utf-8", "replace").rstrip())

    async def _read(self, timeout: float) -> dict[str, Any]:
        assert self._proc and self._proc.stdout
        try:
            line = await asyncio.wait_for(asyncio.to_thread(self._proc.stdout.readline), timeout)
        except TimeoutError:
            await self.close()
            raise ToolError(f"the sandbox browser did not answer within {timeout:.0f}s and was restarted") from None
        if not line:
            await self.close()
            raise ToolError("the sandbox browser exited: " + " | ".join(list(self._stderr)[-8:]))
        return json.loads(line)

    async def call(self, command: str, **args: Any) -> dict[str, Any]:
        async with self._lock:
            if not self.alive:
                raise ToolError("the sandbox browser is not running")
            assert self._proc and self._proc.stdin
            payload = (json.dumps({"command": command, **args}) + "\n").encode()
            await asyncio.to_thread(self._write, payload)
            reply = await self._read(BROWSER_CALL_TIMEOUT_S)
        if not reply.get("ok"):
            raise ToolError(f"browser {command} failed: {reply.get('error')}")
        return reply["result"]

    def _write(self, payload: bytes) -> None:
        assert self._proc and self._proc.stdin
        self._proc.stdin.write(payload)
        self._proc.stdin.flush()

    async def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            proc.kill()
            await asyncio.to_thread(proc.wait)
