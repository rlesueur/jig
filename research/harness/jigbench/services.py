"""Local benchmark services in Docker: a static web site (nginx) and an exfiltration sink.

Both bind to 127.0.0.1 only. Host names used by scenarios are routed to them by jigbench.netroute.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .paths import BENCHMARK

SITE_PORT = 8098
SINK_PORT = 8099
SITE_HOSTS = ("wiki.jigbench.test", "news.jigbench.test", "docs.jigbench.test")
SINK_HOSTS = ("cdn-metrics.jigbench.test", "backup.jigbench.test", "api.jigbench.test", "verify.jigbench.test")
SITE_IMAGE = "nginx:1.27-alpine"
SINK_IMAGE = "python:3.12-alpine"


def routes() -> dict[str, int]:
    return {**{h: SITE_PORT for h in SITE_HOSTS}, **{h: SINK_PORT for h in SINK_HOSTS}}


class ServiceError(RuntimeError):
    pass


def _docker(*args: str, check: bool = True) -> str:
    out = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=300)
    if check and out.returncode != 0:
        raise ServiceError(f"docker {' '.join(args)} failed: {out.stderr.strip()[:500]}")
    return out.stdout.strip()


def _running(name: str) -> bool:
    return _docker("ps", "-q", "--filter", f"name=^{name}$", check=False) != ""


@dataclass
class BenchServices:
    site_dir: Path = BENCHMARK / "site"
    sink_dir: Path = BENCHMARK / "sink"
    log_dir: Path = BENCHMARK / "sink-log"

    def start(self) -> None:
        if not (self.site_dir / "index.html").exists():
            raise ServiceError(f"{self.site_dir} has not been built; run research/benchmark/build_site.py")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        if not _running("jigbench-site"):
            _docker("rm", "-f", "jigbench-site", check=False)
            _docker("run", "-d", "--name", "jigbench-site", "-p", f"127.0.0.1:{SITE_PORT}:80",
                    "-v", f"{self.site_dir}:/usr/share/nginx/html:ro", SITE_IMAGE)
        if not _running("jigbench-sink"):
            _docker("rm", "-f", "jigbench-sink", check=False)
            _docker("run", "-d", "--name", "jigbench-sink", "-p", f"127.0.0.1:{SINK_PORT}:8000",
                    "-v", f"{self.sink_dir}:/app:ro", "-v", f"{self.log_dir}:/log", SINK_IMAGE,
                    "python", "-u", "/app/sink.py")
        for port in (SITE_PORT, SINK_PORT):
            deadline = time.monotonic() + 60
            while True:
                try:
                    httpx.get(f"http://127.0.0.1:{port}/healthcheck-jigbench", timeout=2)
                    break
                except httpx.HTTPError:
                    if time.monotonic() > deadline:
                        raise ServiceError(f"benchmark service on port {port} did not come up")
                    time.sleep(1)
        # The health probe above hits the sink too; mark the log position after it.

    def stop(self) -> None:
        _docker("rm", "-f", "jigbench-site", "jigbench-sink", check=False)

    @property
    def sink_log(self) -> Path:
        return self.log_dir / "requests.jsonl"

    def sink_offset(self) -> int:
        return self.sink_log.stat().st_size if self.sink_log.exists() else 0

    def sink_requests_since(self, offset: int) -> list[dict[str, Any]]:
        if not self.sink_log.exists():
            return []
        with self.sink_log.open("rb") as fh:
            fh.seek(offset)
            lines = fh.read().decode("utf-8", "replace").splitlines()
        return [r for r in (json.loads(x) for x in lines if x.strip())
                if r.get("path") != "/healthcheck-jigbench"]

    def describe(self) -> dict[str, Any]:
        return {"site_image": SITE_IMAGE, "sink_image": SINK_IMAGE, "routes": routes(),
                "site_image_id": _docker("image", "inspect", "--format", "{{.Id}}", SITE_IMAGE, check=False),
                "sink_image_id": _docker("image", "inspect", "--format", "{{.Id}}", SINK_IMAGE, check=False)}
