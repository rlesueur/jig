"""Optional model-server supervision and the bounded start-up readiness wait (``[model.launch]``).

Jig is model-agnostic, so starting the model server is optional. With a ``command``, Jig starts that
server (any server: ``llama-server ...``, ``ollama serve``, ...), writes its output to
``<data_dir>/logs/model-server.log`` and stops it again when Jig stops. Without one, Jig only waits
for the server you run yourself.

The wait is bounded by ``readiness_timeout_s``. If the endpoint is not serving the configured model in
time, start-up fails with :class:`ModelServerNotReady`, so whatever started Jig (a terminal, Task
Scheduler, launchd or systemd) sees a failure. Jig never switches to a different server or model.

With a ``command``, the configured endpoint decides what happens at start-up:

* it already serves the configured model (for example a server started by hand): Jig uses it and
  launches nothing ("model server already running; not launching"); it does not supervise it;
* it answers HTTP 503 (llama.cpp while it loads a model): Jig launches nothing and waits for it;
* something else holds the port: Jig fails at once, since a second server could not bind it;
* nothing listens: Jig launches the command, and if that server later exits on its own, restarts it
  up to ``max_restarts`` times in a row before giving up (logged and audited each time).

Ownership. Jig only ever stops a server it launched. It records that server (pid, process start time and
command line) in ``<data_dir>/model-server.json``. "Turn Jig off" (``jig stop``, ``POST /power/stop``
with ``scope = "jig"``) leaves it running and keeps the record, and the next Jig start takes it back
under supervision ("adopts" it) if the same process, matched on pid *and* start time, still serves the
endpoint. Anything else at the endpoint is never stopped.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from .config import ModelLaunchConfig
from .errors import JigError, ModelServerUnavailable
from .model import ModelClient
from .procinfo import AdoptedProcess, process_start_time

log = logging.getLogger(__name__)

RECORD_FILENAME = "model-server.json"


class ModelServerNotReady(ModelServerUnavailable):
    """The model endpoint did not become ready within the readiness timeout, or its launched server exited."""


class ModelServerNotManaged(JigError):
    """Asked to stop (or start) a model server that Jig does not manage. Jig never stops a process it
    did not start."""


# A launched server that ran at least this long before exiting counts as a fresh failure, not a repeated one.
HEALTHY_RUN_S = 600.0

AuditFn = Callable[..., Any]


def _endpoint_address(base_url: str) -> tuple[str, int]:
    parts = urlsplit(base_url)
    return parts.hostname or "127.0.0.1", parts.port or (443 if parts.scheme == "https" else 80)


def _port_in_use(host: str, port: int, timeout_s: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


class ModelServerSupervisor:
    def __init__(self, launch: ModelLaunchConfig, log_dir: Path, *, audit: AuditFn | None = None,
                 state_dir: Path | None = None):
        self.launch = launch
        self.log_path = log_dir / "model-server.log"
        # Where the ownership record lives (the data directory). Without it, nothing is recorded or adopted.
        self.record_path = state_dir / RECORD_FILENAME if state_dir is not None else None
        self.process: subprocess.Popen[bytes] | AdoptedProcess | None = None
        self.reused_running = False
        self.adopted = False
        self.stopped_by_user = False
        self.restarts = 0
        self._audit = audit
        self._clients: list[ModelClient] = []
        self._started_at = 0.0
        self._watcher: asyncio.Task[None] | None = None
        self._stopping = False

    @property
    def configured(self) -> bool:
        return bool(self.launch.command)

    @property
    def managed(self) -> bool:
        """True while a server that Jig launched (or adopted) is running under its supervision."""
        return self.process is not None and self.process.poll() is None

    def command_line(self) -> list[str]:
        return [self.launch.command, *self.launch.args]

    def _record(self, kind: str, summary: str, **data: Any) -> None:
        if self._audit is not None:
            self._audit(kind, summary, **data)

    async def ensure_ready(self, clients: list[ModelClient]) -> dict[str, Any]:
        """Start the server if configured (see the module docstring for an endpoint that is already in
        use), then wait for every endpoint."""
        self._clients = clients
        if self.configured:
            await self._launch_unless_running(clients[0])
        for client in clients:
            await wait_until_ready(client, timeout_s=self.launch.readiness_timeout_s,
                                   poll_interval_s=self.launch.poll_interval_s, process=self.process,
                                   process_log=self.log_path if self.process else None)
        if self.process is not None:
            self._watcher = asyncio.create_task(self._watch(), name="jig-model-server-watch")
        return self.info()

    def info(self) -> dict[str, Any]:
        return {"launched": self.process is not None, "pid": self.process.pid if self.process else None,
                "already_running": self.reused_running, "adopted": self.adopted, "managed": self.managed,
                "stopped_by_user": self.stopped_by_user, "restarts": self.restarts,
                "command": self.command_line() if self.configured else None}

    # Ownership record ---------------------------------------------------------------------------
    def _write_record(self) -> None:
        if self.record_path is None or self.process is None:
            return
        self.record_path.parent.mkdir(parents=True, exist_ok=True)
        self.record_path.write_text(json.dumps({
            "pid": self.process.pid, "start_time": process_start_time(self.process.pid),
            "command": self.command_line(), "base_url": self._clients[0].config.base_url if self._clients else None,
            "launched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, indent=2), encoding="utf-8")

    def _clear_record(self) -> None:
        if self.record_path is not None:
            self.record_path.unlink(missing_ok=True)

    def _adoptable(self) -> AdoptedProcess | None:
        """The server an earlier Jig launched and left running, if this is provably that same process."""
        if self.record_path is None:
            return None
        try:
            record = json.loads(self.record_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return None
        pid, start = record.get("pid"), record.get("start_time")
        if record.get("command") != self.command_line() or not isinstance(pid, int) or start is None:
            log.info("not adopting the model server in %s: it was launched with a different command", self.record_path)
            return None
        if process_start_time(pid) != start:
            self._clear_record()  # that process has exited; the pid may now belong to something else
            return None
        try:
            return AdoptedProcess(pid)
        except OSError as exc:
            log.warning("not adopting model server pid %d: %s", pid, exc)
            return None

    async def _launch_unless_running(self, client: ModelClient) -> None:
        base_url = client.config.base_url
        try:
            await client.health()
        except ModelServerUnavailable as exc:
            not_serving = str(exc)
        else:
            if (adopted := self._adoptable()) is not None:
                self.process, self.adopted, self._started_at = adopted, True, time.monotonic()
                log.info("model server at %s is the one an earlier Jig launched (pid %d); supervising it again",
                         base_url, adopted.pid)
                return
            self.reused_running = True
            log.info("model server already running at %s (serving %s); not launching %s", base_url,
                     client.model_name, subprocess.list2cmdline(self.command_line()))
            return
        host, port = _endpoint_address(base_url)
        if not await asyncio.to_thread(_port_in_use, host, port, client.config.connect_timeout_s):
            self._spawn()
            return
        # Something holds the port, so a second server could not bind it. Only a server that is still
        # loading (llama.cpp answers 503 until the model is loaded) is waited for; anything else is an error.
        try:
            async with httpx.AsyncClient(timeout=client.config.connect_timeout_s) as http:
                status = (await http.get(base_url.rstrip("/") + "/models")).status_code
        except httpx.HTTPError as probe_exc:
            status, not_serving = None, f"{not_serving}; probe: {probe_exc!r}"
        if status == 503:
            self.reused_running = True
            log.info("model server already running at %s but still loading (HTTP 503); not launching, waiting "
                     "up to %.0fs for it", base_url, self.launch.readiness_timeout_s)
            return
        raise ModelServerNotReady(
            f"{host}:{port} is already in use by something that is not a working model server for this config "
            f"({not_serving}). Jig did not launch [model.launch] command, because it could not bind that port. "
            "Stop whatever is using the port, or point [model] base_url at the right server.")

    async def _watch(self) -> None:
        """Restart a launched server that exits on its own, up to max_restarts times in a row."""
        failures = 0
        while not self._stopping:
            p = self.process
            if p is None:
                return
            while p.poll() is None:
                await asyncio.sleep(self.launch.poll_interval_s)
            if self._stopping:
                return
            ran = time.monotonic() - self._started_at
            failures = 1 if ran >= HEALTHY_RUN_S else failures + 1
            if failures > self.launch.max_restarts:
                msg = (f"the model server Jig launched (pid {p.pid}) exited with code {p.returncode} after "
                       f"{ran:.0f}s; it has failed {failures} times in a row, more than max_restarts = "
                       f"{self.launch.max_restarts}, so Jig is not restarting it again. See {self.log_path}")
                log.error(msg)
                self._record("model_server.gave_up", msg, pid=p.pid, exit_code=p.returncode, failures=failures)
                self.process = None
                self._clear_record()
                return
            msg = (f"the model server Jig launched (pid {p.pid}) exited with code {p.returncode} after {ran:.0f}s; "
                   f"restarting it in {self.launch.restart_delay_s:.0f}s (restart {failures} of "
                   f"{self.launch.max_restarts}). See {self.log_path}")
            log.error(msg)
            self._record("model_server.exited", msg, pid=p.pid, exit_code=p.returncode, restart=failures)
            await asyncio.sleep(self.launch.restart_delay_s)
            if self._stopping:
                return
            try:
                self._spawn()
                self.restarts += 1
                for client in self._clients:
                    await wait_until_ready(client, timeout_s=self.launch.readiness_timeout_s,
                                           poll_interval_s=self.launch.poll_interval_s, process=self.process,
                                           process_log=self.log_path)
            except ModelServerNotReady as exc:
                log.error("restarting the model server failed: %s", exc)
                self._record("model_server.restart_failed", str(exc), restart=failures)
                if self.process is not None and self.process.poll() is None:
                    self.process.kill()
                    await asyncio.to_thread(self.process.wait)
                if self.process is None:
                    return
                continue
            log.info("model server restarted (pid %d) and ready", self.process.pid)
            self._record("model_server.restarted", f"model server restarted (pid {self.process.pid}) and ready",
                         pid=self.process.pid, restart=failures)

    def _spawn(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        out = self.log_path.open("ab")
        out.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} starting: {self.command_line()}\n".encode())
        out.flush()
        try:
            self.process = subprocess.Popen(self.command_line(), cwd=self.launch.working_dir or None,
                                            env={**os.environ, **self.launch.env}, stdout=out,
                                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                            creationflags=flags, start_new_session=sys.platform != "win32")
        except OSError as exc:
            raise ModelServerNotReady(f"could not start the model server {self.command_line()}: {exc}") from exc
        finally:
            out.close()
        self._started_at = time.monotonic()
        self.adopted = False
        self._write_record()
        log.info("started model server (pid %d): %s; output in %s", self.process.pid,
                 subprocess.list2cmdline(self.command_line()), self.log_path)

    async def _stop_watching(self) -> None:
        self._stopping = True
        if self._watcher is not None:
            self._watcher.cancel()
            await asyncio.gather(self._watcher, return_exceptions=True)
            self._watcher = None

    async def detach(self) -> int | None:
        """Stop supervising without stopping the server ("Turn Jig off" without the model). The ownership
        record stays, so the next Jig start adopts it again. Returns its pid, or None if none was running."""
        await self._stop_watching()
        p, self.process = self.process, None
        if p is None or p.poll() is not None:
            self._clear_record()
            return None
        log.info("leaving the model server (pid %d) running; the next Jig start supervises it again", p.pid)
        return p.pid

    def _not_managed_reason(self, base_url: str) -> str:
        if not self.configured:
            return (f"You started your model app yourself (at {base_url}), so Jig leaves it running. Close it from "
                    "its own window or tray icon if you want to free the graphics memory.")
        if self.stopped_by_user:
            return "The model server Jig started is already stopped. Start it again with 'jig model start'."
        return (f"Jig didn't start the model server at {base_url}: it was already running when Jig started "
                "(started by hand, or by a service such as Ollama), so Jig won't stop it. Stop it where it runs: "
                "press Ctrl+C in its window, or stop the service (for Ollama, quit it from the notification-area "
                "icon, or run 'ollama stop <model>' to unload the model and free the GPU).")

    def require_managed(self, base_url: str) -> None:
        if not self.managed:
            raise ModelServerNotManaged(self._not_managed_reason(base_url))

    async def stop_by_user(self, base_url: str) -> int:
        """Stop the server Jig launched while Jig keeps running ('jig model stop'). Refuses any other."""
        self.require_managed(base_url)
        pid = self.process.pid  # type: ignore[union-attr]
        await self.stop()
        self.stopped_by_user = True
        return pid

    async def start_by_user(self) -> dict[str, Any]:
        """Launch the configured server again after 'jig model stop' (or a give-up), and wait for it."""
        if not self.configured:
            raise ModelServerNotManaged("Jig can't start a model server: [model.launch] has no command in the config.")
        if self.managed:
            raise ModelServerNotManaged(f"the model server Jig started is already running (pid {self.process.pid})")
        if not self._clients:
            raise ModelServerNotManaged("Jig has not finished starting yet; try again in a moment")
        self._stopping, self.stopped_by_user, self.reused_running, self.restarts = False, False, False, 0
        return await self.ensure_ready(self._clients)

    async def stop(self) -> None:
        """Stop only a server that Jig itself started (or adopted)."""
        await self._stop_watching()
        p, self.process = self.process, None
        if p is None:
            return
        if p.poll() is not None:
            self._clear_record()
            return
        log.info("stopping model server (pid %d)", p.pid)
        p.terminate()
        try:
            await asyncio.to_thread(p.wait, self.launch.stop_timeout_s)
        except subprocess.TimeoutExpired:
            log.warning("model server (pid %d) did not exit within %.0fs; killing it", p.pid,
                        self.launch.stop_timeout_s)
            p.kill()
            await asyncio.to_thread(p.wait)
        self._clear_record()


async def wait_until_ready(client: ModelClient, *, timeout_s: float, poll_interval_s: float = 2.0,
                           process: subprocess.Popen[bytes] | None = None,
                           process_log: Path | None = None) -> dict[str, Any]:
    """Poll the endpoint until it serves the configured model. Raises ModelServerNotReady at the deadline."""
    start = time.monotonic()
    deadline = start + timeout_s
    attempt = 0
    while True:
        attempt += 1
        try:
            info = await client.health()
        except ModelServerUnavailable as exc:
            last = str(exc)
        else:
            if attempt > 1:
                log.info("%s is ready after %.1fs", client.label, time.monotonic() - start)
            return info
        if process is not None and (code := process.poll()) is not None:
            raise ModelServerNotReady(f"the model server started by Jig exited with code {code} before "
                                      f"{client.config.base_url} became ready; see {process_log}. Last error: {last}")
        now = time.monotonic()
        if now >= deadline:
            hint = (" Increase [model.launch] readiness_timeout_s if it needs longer to load."
                    if timeout_s else " Set [model.launch] readiness_timeout_s to wait for a server that is "
                    "still starting.")
            raise ModelServerNotReady(f"{client.label} at {client.config.base_url} was not ready within "
                                      f"{timeout_s:.0f}s ({attempt} attempts). Last error: {last}.{hint}")
        log.info("waiting for %s at %s (%.0fs of %.0fs): %s", client.label, client.config.base_url,
                 now - start, timeout_s, last)
        await asyncio.sleep(min(poll_interval_s, max(0.0, deadline - now)))
