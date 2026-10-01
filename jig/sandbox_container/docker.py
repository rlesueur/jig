"""Thin wrapper around the ``docker`` CLI. Every failure raises ``SandboxUnavailable`` with Docker's own message."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

from ..errors import SandboxUnavailable

IMAGE_DIR = Path(__file__).resolve().parent / "image"


def docker_path() -> str:
    path = shutil.which("docker")
    if not path:
        raise SandboxUnavailable("the docker CLI is not on PATH; install Docker (Docker Desktop on Windows/macOS)")
    return path


def docker(*args: str, timeout: float = 120.0, check: bool = True,
           stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    try:
        proc = subprocess.run([docker_path(), *args], input=stdin, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise SandboxUnavailable(f"docker {args[0]} timed out after {timeout:.0f}s") from exc
    if check and proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip() or proc.stdout.decode("utf-8", "replace").strip()
        raise SandboxUnavailable(f"docker {' '.join(args[:2])} failed (exit {proc.returncode}): {err[:800]}")
    return proc


async def adocker(*args: str, timeout: float = 120.0, check: bool = True,
                  stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    return await asyncio.to_thread(docker, *args, timeout=timeout, check=check, stdin=stdin)


def require_daemon() -> str:
    proc = docker("info", "--format", "{{.ServerVersion}}", timeout=30, check=False)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise SandboxUnavailable(
            "the Docker daemon is not running or not reachable, so the container sandbox cannot start "
            f"(Jig does not fall back to the directory sandbox). Start Docker Desktop or the Docker service. "
            f"Docker said: {err[:400]}"
        )
    return proc.stdout.decode().strip()


def build_image(tag: str) -> None:
    """Build the sandbox image, streaming Docker's output to the console."""
    require_daemon()
    result = subprocess.run([docker_path(), "build", "-t", tag, str(IMAGE_DIR)])
    if result.returncode != 0:
        raise SandboxUnavailable(f"docker build of {tag} failed (exit {result.returncode})")
