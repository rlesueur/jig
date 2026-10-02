"""Thin wrapper around the ``docker`` CLI. Every failure raises ``SandboxUnavailable`` with Docker's own message."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Any

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


def docker_status(image: str, *, timeout: float = 10.0) -> dict[str, Any]:
    """What is in place for the container sandbox: the CLI, a running daemon and the built image. Never raises."""
    out: dict[str, Any] = {"cli": shutil.which("docker") is not None, "daemon": False, "version": None,
                           "image": image, "image_built": False, "problem": None}
    if not out["cli"]:
        out["problem"] = "Docker is not installed (the docker command was not found)."
        return out
    try:
        proc = docker("info", "--format", "{{.ServerVersion}}", timeout=timeout, check=False)
    except SandboxUnavailable as exc:
        out["problem"] = f"Docker did not answer: {exc}"
        return out
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        out["problem"] = f"Docker is installed but not running. Docker said: {err[:300]}"
        return out
    out["daemon"], out["version"] = True, proc.stdout.decode().strip()
    try:
        out["image_built"] = docker("image", "inspect", image, timeout=timeout, check=False).returncode == 0
    except SandboxUnavailable as exc:
        out["problem"] = f"Docker did not answer: {exc}"
    return out


def build_image(tag: str) -> None:
    """Build the sandbox image, streaming Docker's output to the console."""
    require_daemon()
    result = subprocess.run([docker_path(), "build", "-t", tag, str(IMAGE_DIR)])
    if result.returncode != 0:
        raise SandboxUnavailable(f"docker build of {tag} failed (exit {result.returncode})")
