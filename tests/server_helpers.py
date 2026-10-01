"""Helpers for tests that run real ``jig serve`` processes on their own port and data directory."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from jig.config import load_config
from jig.instance import running_instance

# Never the model server's port or the developers' own Jig.
RESERVED_PORTS = {8080, 8766}


def free_port() -> int:
    while True:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if port not in RESERVED_PORTS:
            return port


def config_path() -> Path:
    return load_config().source


def start_jig(data_dir: Path, port: int, *, env: dict[str, str] | None = None, config: Path | None = None,
              extra: list[str] | None = None) -> tuple[subprocess.Popen[bytes], Path]:
    data_dir.mkdir(parents=True, exist_ok=True)
    out = data_dir / "serve-console.log"
    cmd = [sys.executable, "-m", "jig.cli", "--config", str(config or config_path()), "serve", "--port", str(port),
           *(extra or [])]
    with out.open("wb") as fh:
        proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                env={**os.environ, "JIG_DATA_DIR": str(data_dir), **(env or {})})
    return proc, out


def wait_health(port: int, *, timeout: float = 240.0, proc: subprocess.Popen[bytes] | None = None,
                log: Path | None = None) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            raise AssertionError(f"jig serve exited with {proc.returncode}:\n{log.read_text(errors='replace') if log else ''}")
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise AssertionError(f"Jig on port {port} did not answer within {timeout:.0f}s")


def token(data_dir: Path) -> dict[str, str]:
    return {"Authorization": f"Bearer {(data_dir / 'api-token').read_text(encoding='ascii').strip()}"}


def wait_stopped(data_dir: Path, *, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if running_instance(data_dir) is None:
            return
        time.sleep(0.5)
    raise AssertionError(f"Jig for {data_dir} was still running after {timeout:.0f}s")


def kill(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.kill()
        proc.wait(30)
