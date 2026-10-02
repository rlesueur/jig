"""Turning Jig off ("Turn Jig off" in Settings, ``POST /power/stop``, ``jig stop [--model]``).

Two scopes:

* ``jig``: Jig shuts down gracefully (tasks are checkpointed and re-queued, nothing is left "running").
  A model server that Jig launched is left running, and the next Jig start supervises it again.
* ``jig_and_model``: the same, and the model server is stopped too, which frees its GPU memory. Only a
  server that Jig launched and supervises; one started any other way is refused with how to stop it.

Stopping never touches autostart: if 'Start with Windows' is on, Jig starts again at the next logon.
In container mode it is refused, because compose's restart policy would bring Jig straight back.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any

from .autostart import AutostartError, backend_from_config, not_applicable_reason
from .config import Config
from .errors import JigError
from .model_server import ModelServerSupervisor

SCOPES = ("jig", "jig_and_model")

CONTAINER_STOP_GUIDANCE = (
    "Jig runs in a container here, so it can't turn itself off: compose's restart policy (unless-stopped) "
    "would start it again straight away. On the host, run 'docker compose stop jig' (or 'docker compose stop' "
    "for the whole stack, including a model server in the stack). Start it again with 'docker compose start'.")


class PowerRefused(JigError):
    """A turn-off request that Jig refuses, with the reason and what to do instead."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def gpu_usage(pid: int) -> dict[str, Any]:
    """GPU memory held by ``pid``, from nvidia-smi. Says plainly when that cannot be known."""
    exe = shutil.which("nvidia-smi")
    if exe is None:
        return {"available": False, "reason": "nvidia-smi was not found, so Jig can't tell how much GPU memory "
                                              "the model server holds"}
    try:
        apps = subprocess.run([exe, "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
                              capture_output=True, text=True, timeout=15)
        gpus = subprocess.run([exe, "--query-gpu=index,name,memory.used,memory.total", "--format=csv,noheader,nounits"],
                              capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "reason": f"nvidia-smi did not answer: {exc}"}
    if apps.returncode != 0 or gpus.returncode != 0:
        return {"available": False, "reason": f"nvidia-smi failed: {(apps.stderr or gpus.stderr).strip()}"}
    devices = []
    for line in gpus.stdout.strip().splitlines():
        index, name, used, total = (x.strip() for x in line.split(","))
        devices.append({"index": int(index), "name": name, "memory_used_mib": int(used), "memory_total_mib": int(total)})
    rows = [[x.strip() for x in line.split(",")] for line in apps.stdout.strip().splitlines() if line.strip()]
    mine = [r for r in rows if r[0] == str(pid)]
    if not mine:
        return {"available": True, "on_gpu": False, "vram_mib": None, "gpus": devices,
                "note": f"nvidia-smi does not list the model server (pid {pid}) as a GPU process"}
    values = [r[1] for r in mine]
    vram = sum(int(v) for v in values) if all(v.isdigit() for v in values) else None
    note = None if vram is not None else (
        "nvidia-smi lists the model server on the GPU, but this driver does not report memory per process (usual "
        "on Windows with WDDM drivers). Stopping it frees whatever it holds; compare the GPU totals before and after.")
    return {"available": True, "on_gpu": True, "vram_mib": vram, "gpus": devices, "note": note}


def autostart_summary(config: Config) -> dict[str, Any]:
    """Whether THIS install starts with Windows. An entry that starts another Jig folder counts as not registered."""
    if reason := not_applicable_reason(config):
        return {"applicable": False, "registered": False, "reason": reason}
    backend = backend_from_config(config)
    try:
        owner = backend.registered_owner()
        return {"applicable": True, "registered": owner is not None and bool(backend.owned(owner)),
                "entry": backend.entry, "other_install": owner is not None and not backend.owned(owner)}
    except AutostartError as exc:
        return {"applicable": True, "registered": None, "entry": backend.entry, "error": str(exc)}


def start_again(autostart: dict[str, Any], *, tray: bool = False) -> str:
    how = "use Jig from the Start menu or its tray icon" if tray else "run jig serve"
    if autostart.get("registered"):
        return (f"To start Jig again, {how}, or restart your computer: Start with Windows is on. To stop it "
                "starting by itself, turn off Start with Windows in Settings next time Jig is running.")
    if autostart.get("registered") is None and autostart.get("applicable"):
        return f"Jig couldn't check Start with Windows ({autostart.get('error')}). To start Jig again, {how}."
    return f"To start Jig again, {how}."


def power_state(config: Config, supervisor: ModelServerSupervisor) -> dict[str, Any]:
    if config.deployment == "container":
        return {"applicable": False, "deployment": "container", "reason": CONTAINER_STOP_GUIDANCE}
    autostart = autostart_summary(config)
    model: dict[str, Any] = {"base_url": config.model.base_url, "configured": supervisor.configured,
                             **supervisor.info()}
    if supervisor.managed:
        model["gpu"] = gpu_usage(supervisor.process.pid)  # type: ignore[union-attr]
        model["frees_gpu_memory"] = model["gpu"].get("on_gpu") if model["gpu"]["available"] else None
    else:
        model["refusal"] = supervisor._not_managed_reason(config.model.base_url)
        model["frees_gpu_memory"] = False
    return {"applicable": True, "deployment": config.deployment, "scopes": list(SCOPES),
            "can_stop_model": supervisor.managed, "model_server": model, "autostart": autostart,
            "start_again": start_again(autostart)}


def check_stop(config: Config, supervisor: ModelServerSupervisor, scope: str, confirm: Any) -> None:
    """Raise PowerRefused (with the reason and what to do instead) unless this request may go ahead."""
    if config.deployment == "container":
        raise PowerRefused(CONTAINER_STOP_GUIDANCE)
    if scope not in SCOPES:
        raise PowerRefused(f"scope must be one of {', '.join(SCOPES)}, not {scope!r}", 400)
    if confirm is not True:
        raise PowerRefused('Jig was not turned off: send "confirm": true once the user has confirmed', 400)
    if scope == "jig_and_model":
        supervisor.require_managed(config.model.base_url)
