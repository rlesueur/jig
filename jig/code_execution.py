"""Whether Jig can run code, and if not, exactly what is needed: for GET /sandbox, the UI and ``jig sandbox status``.

Code, shell commands and the headless browser exist only with the container (or compose) sandbox backend.
The default backend is ``directory``, which confines the file tools to a folder and runs no code. Jig never
switches backend by itself: the container backend refuses to start without Docker, so making it the default
would stop Jig starting at all on a machine without Docker.
"""

from __future__ import annotations

from typing import Any

from .config import Config

CODE_TOOLS = ("run_command", "run_python")
BROWSER_TOOLS = ("browser_open", "browser_read", "browser_screenshot", "browser_click", "browser_type",
                 "browser_fill", "browser_submit", "browser_login")


def code_execution_status(config: Config, tool_names: set[str]) -> dict[str, Any]:
    backend = config.sandbox.backend
    available = all(t in tool_names for t in CODE_TOOLS)
    out: dict[str, Any] = {"backend": backend, "available": available,
                           "tools": [t for t in (*CODE_TOOLS, *BROWSER_TOOLS) if t in tool_names],
                           "config_file": str(config.source)}
    if available:
        out["summary"] = ("On: code, shell commands and the headless browser run in an isolated container, "
                          "and each run is checked and asks you first.")
        out["steps"] = []
        return out
    from .sandbox_container.docker import docker_status

    docker = docker_status(config.sandbox.image)
    out["docker"] = docker
    steps: list[str] = []
    if not docker["cli"]:
        steps.append("Install Docker Desktop (Windows or macOS) or Docker Engine (Linux), and start it.")
    elif not docker["daemon"]:
        steps.append("Start Docker Desktop (or the Docker service on Linux).")
    if not docker["image_built"]:
        steps.append("Build Jig's sandbox image once: jig sandbox build")
    steps.append(f'Set [sandbox] backend = "container" in {config.source} (or set JIG_SANDBOX_BACKEND=container), '
                 "then restart Jig.")
    out["steps"] = steps
    out["ready_to_switch"] = docker["daemon"] and docker["image_built"]
    out["summary"] = ("Off: Jig is using the folder-only sandbox, so it can't run code, shell commands or a "
                      "web browser. " + ("Docker and the sandbox image are ready, so only the setting is left."
                                         if out["ready_to_switch"] else "This needs Docker."))
    return out
