"""Find what the set-up page needs: model apps already running on this computer, and the graphics card.

Model apps are found by asking each one's usual local port for its models (read-only GET requests; nothing
is loaded, changed or started): llama.cpp's llama-server (8080), LM Studio (1234) and Ollama (11434), plus
any address the person types in. The graphics card comes from ``nvidia-smi`` (NVIDIA), or on Windows from
the display adapter's memory size in the registry (other makes).
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from typing import Any
from urllib.parse import urlsplit

import httpx

KNOWN_PORTS = {8080: "llamacpp", 1234: "lmstudio", 11434: "ollama"}
LABELS = {"llamacpp": "llama.cpp", "lmstudio": "LM Studio", "ollama": "Ollama", "other": "Model server"}
TIMEOUT_S = 2.0


async def find_model_servers(extra_url: str = "", skip_port: int | None = None) -> dict[str, Any]:
    targets = [(f"http://127.0.0.1:{port}", app) for port, app in KNOWN_PORTS.items() if port != skip_port]
    if extra_url:
        root = _root(extra_url)
        if root and all(root != t[0] for t in targets):
            targets.append((root, ""))
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        results = await asyncio.gather(*(_probe(client, root, app) for root, app in targets))
    return {"servers": [r for r in results if r["found"]], "checked": results}


def _root(url: str) -> str:
    url = url.strip()
    if "://" not in url:
        url = "http://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


async def _get(client: httpx.AsyncClient, url: str) -> Any:
    try:
        r = await client.get(url)
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


async def _probe(client: httpx.AsyncClient, root: str, expected: str) -> dict[str, Any]:
    port = urlsplit(root).port
    out: dict[str, Any] = {"root": root, "port": port, "expected": LABELS.get(expected, ""), "found": False}
    models = await _get(client, f"{root}/v1/models")
    if not isinstance(models, dict) or not isinstance(models.get("data"), list):
        return out
    ids = [m.get("id") for m in models["data"] if isinstance(m, dict) and m.get("id")]
    entries: dict[str, dict[str, Any]] = {i: {"id": i, "loaded": None, "context": None, "vision": None} for i in ids}
    app = "other"
    if isinstance(version := await _get(client, f"{root}/api/version"), dict) and "version" in version:
        app = "ollama"
        running = await _get(client, f"{root}/api/ps")
        loaded = {m.get("name") or m.get("model") for m in (running or {}).get("models", []) if isinstance(m, dict)}
        for i, e in entries.items():
            e["loaded"] = i in loaded
            for m in (running or {}).get("models", []):
                if isinstance(m, dict) and (m.get("name") or m.get("model")) == i and m.get("context_length"):
                    e["context"] = m["context_length"]
    elif isinstance(lms := await _get(client, f"{root}/api/v0/models"), dict) and isinstance(lms.get("data"), list):
        app = "lmstudio"
        for m in lms["data"]:
            if isinstance(m, dict) and m.get("id") in entries:
                e = entries[m["id"]]
                e["loaded"] = m.get("state") == "loaded"
                e["context"] = m.get("loaded_context_length") or None
                e["vision"] = m.get("type") == "vlm"
                e["kind"] = m.get("type")
        # Embedding models can't chat, so they are never offered.
        entries = {i: e for i, e in entries.items() if e.get("kind") != "embeddings"}
    elif isinstance(props := await _get(client, f"{root}/props"), dict) and (
            "default_generation_settings" in props or "model_path" in props):
        app = "llamacpp"
        ctx = (props.get("default_generation_settings") or {}).get("n_ctx") or props.get("n_ctx")
        vision = (props.get("modalities") or {}).get("vision")
        for e in entries.values():
            e["loaded"], e["context"], e["vision"] = True, ctx, vision
    out.update(found=True, app=app, label=LABELS[app], base_url=f"{root}/v1", models=list(entries.values()))
    return out


def find_gpu() -> dict[str, Any]:
    """The graphics card with the most memory, in GB, or ``{"found": False}``."""
    if nvidia := shutil.which("nvidia-smi"):
        try:
            out = subprocess.run([nvidia, "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=10, **_no_window())
        except (OSError, subprocess.TimeoutExpired):
            out = None
        if out is not None and out.returncode == 0 and out.stdout.strip():
            cards = []
            for line in out.stdout.strip().splitlines():
                name, total, used = (p.strip() for p in line.split(","))
                # GB as cards are sold (binary: a "32 GB" card reports 32607 MiB).
                cards.append({"name": name, "total_gb": round(int(total) / 1024, 1),
                              "used_gb": round(int(used) / 1024, 1)})
            best = max(cards, key=lambda c: c["total_gb"])
            return {"found": True, "source": "nvidia-smi", **best, "cards": cards}
    if sys.platform == "win32":
        cards = _windows_adapters()
        if cards:
            best = max(cards, key=lambda c: c["total_gb"])
            return {"found": True, "source": "Windows display adapter settings", **best, "used_gb": None,
                    "cards": cards}
    return {"found": False, "source": "nvidia-smi" if sys.platform != "win32" else "nvidia-smi and Windows",
            "note": "No graphics card memory could be read on this computer."}


def _no_window() -> dict[str, Any]:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def _windows_adapters() -> list[dict[str, Any]]:
    import winreg

    key_path = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    cards = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path)
    except OSError:
        return []
    with root:
        for i in range(64):
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                break
            if not sub.isdigit():
                continue
            try:
                with winreg.OpenKey(root, sub) as k:
                    size, _ = winreg.QueryValueEx(k, "HardwareInformation.qwMemorySize")
                    name, _ = winreg.QueryValueEx(k, "DriverDesc")
            except OSError:
                continue
            if isinstance(size, bytes):
                size = int.from_bytes(size, "little")
            if size:
                cards.append({"name": str(name), "total_gb": round(int(size) / 2**30, 1)})
    return cards
