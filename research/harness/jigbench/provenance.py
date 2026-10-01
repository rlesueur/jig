"""Capture everything needed to reproduce a run: models, quantisation, server build, hardware, commits, seeds."""

from __future__ import annotations

import json
import platform
import subprocess
import tomllib
from datetime import datetime, timezone
from typing import Any

import httpx
import psutil

from . import __version__
from .paths import MODELS_LOCK, MODELS_TOML, REPO, RESEARCH


def _run(cmd: list[str], cwd: Any = None) -> str:
    out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {out.stderr.strip()[:300]}")
    return out.stdout.strip()


def git_info() -> dict[str, Any]:
    commit = _run(["git", "rev-parse", "HEAD"], cwd=REPO)
    dirty_jig = bool(_run(["git", "status", "--porcelain", "--", "jig"], cwd=REPO))
    dirty_research = bool(_run(["git", "status", "--porcelain", "--", "research"], cwd=REPO))
    return {"commit": commit, "jig_core_dirty": dirty_jig, "research_dirty": dirty_research}


def hardware_info() -> dict[str, Any]:
    gpus = []
    q = _run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"])
    for line in q.splitlines():
        name, mem, driver = [p.strip() for p in line.split(",")]
        gpus.append({"name": name, "memory_mib": int(mem), "driver": driver})
    return {
        "os": platform.platform(),
        "cpu": platform.processor(),
        "cpu_cores_logical": psutil.cpu_count(logical=True),
        "ram_gib": round(psutil.virtual_memory().total / 2**30, 1),
        "gpus": gpus,
        "python": platform.python_version(),
    }


def model_catalogue() -> dict[str, Any]:
    models = tomllib.loads(MODELS_TOML.read_text(encoding="utf-8"))["models"]
    lock = json.loads(MODELS_LOCK.read_text(encoding="utf-8")) if MODELS_LOCK.exists() else {}
    return {k: {**v, "lock": lock.get(k)} for k, v in models.items()}


def endpoint_info(base_url: str) -> dict[str, Any]:
    """Ask a running llama.cpp-compatible server what it is serving (model path, ftype, build, context)."""
    root = base_url.rstrip("/").removesuffix("/v1")
    info: dict[str, Any] = {"base_url": base_url}
    r = httpx.get(f"{base_url.rstrip('/')}/models", timeout=10)
    r.raise_for_status()
    data = r.json().get("data") or []
    if data:
        info["model_id"] = data[0].get("id")
        info["meta"] = data[0].get("meta")
    p = httpx.get(f"{root}/props", timeout=10)
    if p.status_code == 200:
        props = p.json()
        info["model_path"] = props.get("model_path")
        info["model_ftype"] = props.get("model_ftype")
        info["build_info"] = props.get("build_info")
        info["n_ctx"] = (props.get("default_generation_settings") or {}).get("n_ctx")
        info["total_slots"] = props.get("total_slots")
    return info


def capture(config: dict[str, Any], endpoints: dict[str, str]) -> dict[str, Any]:
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "jigbench_version": __version__,
        "git": git_info(),
        "hardware": hardware_info(),
        "endpoints": {name: endpoint_info(url) for name, url in endpoints.items()},
        "config": config,
        "research_dir": str(RESEARCH),
    }
