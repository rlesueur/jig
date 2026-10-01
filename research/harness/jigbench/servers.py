"""Start and stop our own llama-server processes on ports 8090-8097.

Port 8080 belongs to the user's main model server and is never started, stopped or restarted here.
Every server logs to research/logs/servers/. Before starting, free VRAM is checked so the user's
server keeps its headroom; if there is not enough, starting fails loudly rather than degrading.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .paths import LOGS
from .provenance import model_catalogue

BUILDS = {
    "prism": Path(r"C:\Users\you\tools\llama-prism-b10709\llama-server.exe"),
    "upstream": Path(r"C:\Users\you\tools\llama-upstream-b8664-cuda131\llama-server.exe"),
}
ALLOWED_PORTS = range(8090, 8098)
USER_SERVER_PORT = 8080


class ServerError(RuntimeError):
    pass


@dataclass
class ServerSpec:
    model: str                 # key in models.toml
    port: int
    ctx: int = 32768
    gpu_layers: int = 99
    n_cpu_moe: int = 0         # MoE experts kept on the CPU (llama.cpp --n-cpu-moe)
    parallel: int = 2
    build: str | None = None   # defaults to the model's server_build
    extra: list[str] = field(default_factory=list)
    vram_needed_mib: int | None = None  # override the estimate

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def free_vram_mib() -> int:
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True, timeout=30, check=True)
    return int(out.stdout.strip().splitlines()[0])


def _model_path(key: str) -> tuple[Path, dict[str, Any]]:
    cat = model_catalogue()
    if key not in cat:
        raise ServerError(f"unknown model {key!r}; known: {sorted(cat)}")
    m = cat[key]
    lock = m.get("lock")
    if not lock or not lock.get("verified"):
        raise ServerError(f"model {key!r} has no verified download in models.lock.json; run fetch_models.py")
    return Path(lock["path"]), m


class LlamaServer:
    def __init__(self, spec: ServerSpec):
        if spec.port == USER_SERVER_PORT or spec.port not in ALLOWED_PORTS:
            raise ServerError(f"port {spec.port} is not one of ours (allowed: 8090-8097)")
        self.spec = spec
        self.path, self.meta = _model_path(spec.model)
        self.build = spec.build or self.meta.get("server_build", "prism")
        self.exe = BUILDS[self.build]
        self.proc: subprocess.Popen[bytes] | None = None
        self.log_path = LOGS / "servers" / f"{spec.model}-{spec.port}.log"

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.spec.port}/v1"

    def _estimate_vram_mib(self) -> int:
        if self.spec.vram_needed_mib:
            return self.spec.vram_needed_mib
        size_mib = self.path.stat().st_size / 2**20
        on_gpu = 0.35 if self.spec.n_cpu_moe else 1.0
        kv = self.spec.ctx / 32768 * 2048 * max(1, self.spec.parallel) / 2
        return int(size_mib * on_gpu + kv + 1024)

    def _port_in_use(self) -> bool:
        try:
            httpx.get(f"http://127.0.0.1:{self.spec.port}/health", timeout=1)
            return True
        except httpx.HTTPError:
            return False

    def start(self, *, headroom_mib: int = 2048, timeout_s: float = 600) -> None:
        if self._port_in_use():
            raise ServerError(f"something is already listening on port {self.spec.port}")
        need = self._estimate_vram_mib()
        free = free_vram_mib()
        if free - need < headroom_mib:
            raise ServerError(f"not enough free VRAM for {self.spec.model}: need about {need} MiB plus "
                              f"{headroom_mib} MiB headroom, {free} MiB free")
        cmd = [str(self.exe), "-m", str(self.path), "--host", "127.0.0.1", "--port", str(self.spec.port),
               "-c", str(self.spec.ctx), "-ngl", str(self.spec.gpu_layers), "--parallel", str(self.spec.parallel),
               "--jinja", "--alias", self.spec.model, *self.spec.extra]
        if self.spec.n_cpu_moe:
            cmd += ["--n-cpu-moe", str(self.spec.n_cpu_moe)]
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log = self.log_path.open("ab")
        log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(cmd)}\n".encode())
        log.flush()
        self.proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise ServerError(f"llama-server for {self.spec.model} exited with {self.proc.returncode}; "
                                  f"see {self.log_path}")
            try:
                if httpx.get(f"http://127.0.0.1:{self.spec.port}/health", timeout=2).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(2)
        self.stop()
        raise ServerError(f"llama-server for {self.spec.model} did not become healthy in {timeout_s:.0f}s")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=30)
        self.proc = None

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    def describe(self) -> dict[str, Any]:
        return {"spec": self.spec.as_dict(), "exe": str(self.exe), "build": self.build, "model_path": str(self.path),
                "base_url": self.base_url, "log": str(self.log_path)}

    def __enter__(self) -> "LlamaServer":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()
