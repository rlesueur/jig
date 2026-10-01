"""Optional model-server supervision and the bounded start-up readiness wait (``[model.launch]``).

Jig is model-agnostic, so starting the model server is optional. With a ``command``, Jig starts that
server (any server: ``llama-server ...``, ``ollama serve``, ...), writes its output to
``<data_dir>/logs/model-server.log`` and stops it again when Jig stops. Without one, Jig only waits
for the server you run yourself.

The wait is bounded by ``readiness_timeout_s``. If the endpoint is not serving the configured model in
time, start-up fails with :class:`ModelServerNotReady`, so whatever started Jig (a terminal, Task
Scheduler, launchd or systemd) sees a failure. Jig never switches to a different server or model.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .config import ModelLaunchConfig
from .errors import ModelServerUnavailable
from .model import ModelClient

log = logging.getLogger(__name__)


class ModelServerNotReady(ModelServerUnavailable):
    """The model endpoint did not become ready within the readiness timeout, or its launched server exited."""


class ModelServerSupervisor:
    def __init__(self, launch: ModelLaunchConfig, log_dir: Path):
        self.launch = launch
        self.log_path = log_dir / "model-server.log"
        self.process: subprocess.Popen[bytes] | None = None
        self.reused_running = False

    @property
    def configured(self) -> bool:
        return bool(self.launch.command)

    def command_line(self) -> list[str]:
        return [self.launch.command, *self.launch.args]

    async def ensure_ready(self, clients: list[ModelClient]) -> dict[str, Any]:
        """Start the server if configured (and not already answering), then wait for every endpoint."""
        if self.configured:
            try:
                await clients[0].health()
                self.reused_running = True
                log.info("model server at %s is already answering; not starting [model.launch] command",
                         clients[0].config.base_url)
            except ModelServerUnavailable:
                self._spawn()
        for client in clients:
            await wait_until_ready(client, timeout_s=self.launch.readiness_timeout_s,
                                   poll_interval_s=self.launch.poll_interval_s, process=self.process,
                                   process_log=self.log_path if self.process else None)
        return {"launched": self.process is not None, "pid": self.process.pid if self.process else None,
                "already_running": self.reused_running,
                "command": self.command_line() if self.configured else None}

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
        log.info("started model server (pid %d): %s; output in %s", self.process.pid,
                 subprocess.list2cmdline(self.command_line()), self.log_path)

    async def stop(self) -> None:
        """Stop only a server that Jig itself started."""
        p, self.process = self.process, None
        if p is None or p.poll() is not None:
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
