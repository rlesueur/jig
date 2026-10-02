"""Find what the set-up page needs: model apps on this computer, and the graphics card.

Model apps are found by asking each one's usual local port for its models (read-only requests; nothing is
loaded, changed or started): llama.cpp's llama-server (8080), LM Studio (1234) and Ollama (11434), plus any
address the person types in. Ollama and LM Studio are also looked for where their installers put them, so an
app that is installed but not running can be named, with how to start it. The graphics card comes from
``nvidia-smi`` (NVIDIA), or on Windows from the display adapter's memory size in the registry (other makes).

After a model has been chosen, ``serving_context`` asks its app how much context it was actually loaded
with, and ``load_in_lmstudio`` loads an LM Studio model with the context Jig needs (only when asked to).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

KNOWN_PORTS = {8080: "llamacpp", 1234: "lmstudio", 11434: "ollama"}
LABELS = {"llamacpp": "llama.cpp", "lmstudio": "LM Studio", "ollama": "Ollama", "other": "Model server"}
TIMEOUT_S = 2.0
# Loading a model can take a while on a slow disk; Jig waits this long for LM Studio to finish.
LOAD_TIMEOUT_S = 600.0

NO_MODELS = {
    "ollama": "Ollama is running but hasn't got a model yet. Download one (below, under \u201cI don\u2019t have a "
              "model app yet\u201d, is the one that suits your graphics card), then choose Look again.",
    "lmstudio": "LM Studio is running but hasn't got a model that can chat yet. Download one in LM Studio (below, "
                "under \u201cI don\u2019t have a model app yet\u201d, is the one that suits your graphics card), then "
                "choose Look again.",
}
_WHERE = {"win32": "the Start menu", "darwin": "your Applications folder"}.get(sys.platform, "your apps")
NOT_RUNNING = {
    "ollama": f"Ollama is installed but isn't running. Choose Start it for me, or start Ollama from {_WHERE} and "
              "choose Look again.",
    "lmstudio": "LM Studio is installed, but its server is off, so Jig can't reach it. Choose Start it for me: Jig "
                "starts the server in the background, without opening LM Studio. (Or open LM Studio, turn on the "
                "server in its Developer tab, and choose Look again.)",
}


async def find_model_servers(extra_url: str = "", skip_port: int | None = None) -> dict[str, Any]:
    targets = [(f"http://127.0.0.1:{port}", app) for port, app in KNOWN_PORTS.items() if port != skip_port]
    if extra_url:
        root = _root(extra_url)
        if root and all(root != t[0] for t in targets):
            targets.append((root, ""))
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        results = await asyncio.gather(*(_probe(client, root, app) for root, app in targets))
    servers = [r for r in results if r["found"]]
    running = {s["app"] for s in servers}
    installed = [{**app, "running": app["app"] in running,
                  "advice": None if app["app"] in running else NOT_RUNNING[app["app"]]}
                 for app in await asyncio.to_thread(find_installed_apps)]
    return {"servers": servers, "checked": results, "installed": installed}


def _app_paths() -> dict[str, list[Path]]:
    """Where each app's installer puts the program (and, for LM Studio, its command-line tool)."""
    home = Path.home()
    if sys.platform == "win32":
        programs = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local") / "Programs"
        return {"ollama": [programs / "Ollama" / "ollama app.exe", programs / "Ollama" / "ollama.exe"],
                "lmstudio": [programs / "LM Studio" / "LM Studio.exe"]}
    if sys.platform == "darwin":
        return {"ollama": [Path("/Applications/Ollama.app"), home / "Applications" / "Ollama.app"],
                "lmstudio": [Path("/Applications/LM Studio.app"), home / "Applications" / "LM Studio.app"]}
    return {"ollama": [Path("/usr/local/bin/ollama"), Path("/usr/bin/ollama")],
            "lmstudio": [home / ".lmstudio" / "bin" / "lms"]}


def find_installed_apps() -> list[dict[str, Any]]:
    """Ollama and LM Studio, if installed (the program itself must be there: an empty folder left by an
    uninstall doesn't count)."""
    out = []
    for app, paths in _app_paths().items():
        found = next((p for p in paths if p.exists() and (p.is_file() or p.suffix == ".app")), None)
        if found is None and app == "ollama" and (which := shutil.which("ollama")):
            found = Path(which)
        if found is not None:
            out.append({"app": app, "label": LABELS[app], "path": str(found), "can_start": _starter(app) is not None})
    return out


def _lms() -> Path | None:
    """LM Studio's command-line tool: the copy in the install, else the one it puts in ~/.lmstudio/bin."""
    candidates = [Path.home() / ".lmstudio" / "bin" / ("lms.exe" if sys.platform == "win32" else "lms")]
    if sys.platform == "win32":
        programs = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Programs"
        candidates.insert(0, programs / "LM Studio" / "resources" / "app" / ".webpack" / "lms.exe")
    elif sys.platform == "darwin":
        candidates.insert(0, Path("/Applications/LM Studio.app/Contents/Resources/app/.webpack/lms"))
    return next((p for p in candidates if p.is_file()), None)


def _starter(app: str) -> list[str] | None:
    """The command that starts ``app``'s server the way its own installer sets it up."""
    if app == "lmstudio":
        lms = _lms()
        return [str(lms), "server", "start"] if lms else None
    if app == "ollama":
        # --hide starts the app in the notification area without opening its chat window (as at sign-in).
        if sys.platform == "win32":
            tray = _app_paths()["ollama"][0]
            return [str(tray), "--hide"] if tray.is_file() else None
        if sys.platform == "darwin":
            return (["open", "-g", "-a", "Ollama", "--args", "--hide"] if any(p.exists() for p in _app_paths()["ollama"])
                    else None)
        return None  # on Linux Ollama is a system service: 'sudo systemctl start ollama'
    return None


def start_app(app: str) -> str:
    """Start an installed app's server in the background, as asked from the set-up page. Returns what was run.
    Raises RuntimeError (said plainly) if it can't be started or the start command fails."""
    cmd = _starter(app)
    if cmd is None:
        raise RuntimeError(f"Jig can't start {LABELS.get(app, app)} on this computer. Start it yourself, then choose "
                           "Look again.")
    if app == "lmstudio":
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
                              **_no_window())
        if done.returncode != 0:
            raise RuntimeError(f"LM Studio's server didn't start: {(done.stderr or done.stdout).strip()[:300]}")
    else:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         close_fds=True, **_no_window())
    return subprocess.list2cmdline(cmd)


async def serving_context(base_url: str) -> dict[str, Any] | None:
    """How much context each model at ``base_url`` was loaded with, as its app reports it: ``{"app": ...,
    "models": {id: tokens}}``. Ask after the model has answered (Ollama and LM Studio load a model on its first
    request). None if the server isn't one of the apps Jig knows how to ask."""
    root = _root(base_url)
    if not root:
        return None
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        if isinstance(version := await _get(client, f"{root}/api/version"), dict) and "version" in version:
            running = await _get(client, f"{root}/api/ps")
            models = {}
            for m in (running or {}).get("models", []):
                if isinstance(m, dict) and m.get("context_length"):
                    for name in {m.get("name"), m.get("model")} - {None}:
                        models[name] = m["context_length"]
                        models[name.removesuffix(":latest")] = m["context_length"]
            return {"app": "ollama", "models": models}
        if isinstance(lms := await _get(client, f"{root}/api/v0/models"), dict) and isinstance(lms.get("data"), list):
            return {"app": "lmstudio", "models": {m["id"]: m["loaded_context_length"] for m in lms["data"]
                                                  if isinstance(m, dict) and m.get("loaded_context_length")}}
        if isinstance(props := await _get(client, f"{root}/props"), dict) and (
                "default_generation_settings" in props or "model_path" in props):
            ctx = (props.get("default_generation_settings") or {}).get("n_ctx") or props.get("n_ctx")
            return {"app": "llamacpp", "context": ctx}
    return None


async def check_context(config: Any, client: Any) -> dict[str, Any]:
    """The context the agent model was really given, from its app (after it has answered a probe, so it is
    loaded), or from /v1/models. Raises ModelCapabilityError (check "context") when it is too small for Jig to
    use tools at all: below ``recommend.MIN_WORKING_CONTEXT``, or below ``[runtime] min_context_tokens`` if that
    is set lower. Unknown is reported as None, not guessed."""
    from .errors import ModelCapabilityError
    from .recommend import MIN_WORKING_CONTEXT

    tokens, app, reload_at = client.server_info.get("context_tokens"), None, None
    floor = min(MIN_WORKING_CONTEXT, config.runtime.min_context_tokens)
    if not config.model.location.is_cloud:
        found = await serving_context(config.model.base_url)
        if found is not None:
            app = found["app"]
            tokens = found.get("context") or found.get("models", {}).get(client.model_name) or tokens
        if app == "lmstudio":
            reload_at = lmstudio_default_context()
            if tokens is not None and tokens < floor:
                # LM Studio loads a model by itself, at its Default Context Length, when it's asked for one it
                # has unloaded; load it again with the context Jig was set up with.
                tokens = (await load_in_lmstudio(config.model.base_url, client.model_name,
                                                 config.runtime.min_context_tokens, replace=True))["context"]
    if tokens is not None and tokens < floor:
        exc = ModelCapabilityError(
            f"{client.label} {client.model_name} has a {tokens}-token context ({LABELS.get(app or '', 'its server')} "
            f"reports it); Jig needs at least {floor} to use tools, and {config.runtime.min_context_tokens} is "
            "advisable")
        exc.check, exc.context, exc.app, exc.floor = "context", tokens, app, floor
        raise exc
    return {"context_tokens": tokens, "context_app": app, "reload_context": reload_at}


def lmstudio_default_context() -> int | str | None:
    """LM Studio's Default Context Length (Settings > Model Defaults), which it uses whenever it loads a model by
    itself: a number, "max" (each model's maximum), or None if this computer's LM Studio settings can't be read."""
    pointer = Path.home() / ".lmstudio-home-pointer"
    try:
        home = Path(pointer.read_text(encoding="utf-8").strip()) if pointer.is_file() else Path.home() / ".lmstudio"
        setting = json.loads((home / "settings.json").read_text(encoding="utf-8")).get("defaultContextLength")
    except (OSError, ValueError):
        return None
    if not isinstance(setting, dict):
        return None
    if setting.get("type") == "max":
        return "max"
    return setting.get("value") if isinstance(setting.get("value"), int) else None


async def load_in_lmstudio(base_url: str, model: str, context: int, replace: bool = False) -> dict[str, Any]:
    """Load ``model`` in LM Studio with ``context`` tokens. If it is already loaded nothing changes, unless
    ``replace``: then each loaded copy is unloaded first. Returns ``{"loaded": bool, "context": tokens}``. Raises
    RuntimeError with LM Studio's own reason."""
    root = _root(base_url)
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        state = await _get(client, f"{root}/api/v0/models/{quote(model, safe='')}")
        if isinstance(state, dict) and state.get("state") == "loaded":
            if not replace:
                return {"loaded": False, "context": state.get("loaded_context_length")}
            await _unload_in_lmstudio(client, root, model)
        try:
            r = await client.post(f"{root}/api/v1/models/load", json={"model": model, "context_length": context},
                                  timeout=LOAD_TIMEOUT_S)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"LM Studio didn't load {model}: {exc!r}") from exc
    if r.status_code != 200:
        raise RuntimeError(f"LM Studio didn't load {model} (HTTP {r.status_code}): {r.text[:300]}")
    return {"loaded": True, "context": ((r.json().get("load_config") or {}).get("context_length") or context)}


async def _unload_in_lmstudio(client: httpx.AsyncClient, root: str, model: str) -> None:
    listing = await _get(client, f"{root}/api/v1/models")
    for entry in (listing or {}).get("models", []) if isinstance(listing, dict) else []:
        if isinstance(entry, dict) and entry.get("key") == model:
            for instance in entry.get("loaded_instances") or []:
                r = await client.post(f"{root}/api/v1/models/unload", json={"instance_id": instance["id"]},
                                      timeout=LOAD_TIMEOUT_S)
                if r.status_code != 200:
                    raise RuntimeError(f"LM Studio didn't unload {model} (HTTP {r.status_code}): {r.text[:300]}")


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
    # Ollama with nothing downloaded yet answers {"object": "list", "data": null}.
    if isinstance(models, dict) and models.get("data") is None and models.get("object") == "list":
        models["data"] = []
    if not isinstance(models, dict) or not isinstance(models.get("data"), list):
        return out
    ids = [m.get("id") for m in models["data"] if isinstance(m, dict) and m.get("id")]
    entries: dict[str, dict[str, Any]] = {i: {"id": i, "loaded": None, "context": None, "vision": None, "tools": None}
                                          for i in ids}
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
        shown = await asyncio.gather(*(_post(client, f"{root}/api/show", {"model": i}) for i in entries))
        for e, info in zip(list(entries.values()), shown):
            caps = info.get("capabilities") if isinstance(info, dict) else None
            if isinstance(caps, list):
                e["tools"], e["vision"] = "tools" in caps, "vision" in caps
                e["kind"] = "embeddings" if "embedding" in caps and "completion" not in caps else "llm"
    elif isinstance(lms := await _get(client, f"{root}/api/v0/models"), dict) and isinstance(lms.get("data"), list):
        app = "lmstudio"
        for m in lms["data"]:
            if isinstance(m, dict) and m.get("id") in entries:
                e = entries[m["id"]]
                e["loaded"] = m.get("state") == "loaded"
                e["context"] = m.get("loaded_context_length") or None
                e["vision"] = m.get("type") == "vlm"
                e["kind"] = m.get("type")
                if isinstance(m.get("capabilities"), list):
                    e["tools"] = "tool_use" in m["capabilities"]
    if app in ("ollama", "lmstudio"):
        # Embedding models can't chat, so they are never offered.
        entries = {i: e for i, e in entries.items() if e.pop("kind", None) != "embeddings"}
    elif isinstance(props := await _get(client, f"{root}/props"), dict) and (
            "default_generation_settings" in props or "model_path" in props):
        app = "llamacpp"
        ctx = (props.get("default_generation_settings") or {}).get("n_ctx") or props.get("n_ctx")
        vision = (props.get("modalities") or {}).get("vision")
        for e in entries.values():
            e["loaded"], e["context"], e["vision"] = True, ctx, vision
    out.update(found=True, app=app, label=LABELS[app], base_url=f"{root}/v1", models=list(entries.values()),
               advice=None if entries else NO_MODELS.get(app))
    return out


async def _post(client: httpx.AsyncClient, url: str, body: dict[str, Any]) -> Any:
    try:
        r = await client.post(url, json=body)
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


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
