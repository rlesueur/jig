"""Configuration loaded from a TOML file with a small set of environment overrides.

Jig is model-agnostic: the endpoint, model name and sampling parameters all
come from config. Example profiles for common local servers live in
``profiles/``.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .errors import ConfigError

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "jig.toml"


@dataclass(frozen=True)
class EndpointConfig:
    """One OpenAI-compatible chat endpoint."""

    base_url: str
    # Empty means "discover from /v1/models"; discovery fails loudly unless exactly one model is served.
    name: str = ""
    # Name of an environment variable holding the API key, if the server needs one.
    api_key_env: str = ""
    connect_timeout_s: float = 5.0
    read_timeout_s: float = 600.0
    max_tokens: int = 8192
    # Sent verbatim in each request body. Keys the server does not support should be left out.
    sampling: dict[str, Any] = field(default_factory=dict)

    @property
    def api_key(self) -> str | None:
        if not self.api_key_env:
            return None
        value = os.environ.get(self.api_key_env)
        if not value:
            raise ConfigError(f"environment variable {self.api_key_env} (api_key_env) is not set")
        return value


@dataclass(frozen=True)
class ModelLaunchConfig:
    """``[model.launch]``: optionally start and supervise the model server, and wait for it to be ready.

    With ``command`` empty, Jig starts nothing and only waits (for a server you run yourself, such as
    the Ollama service). ``readiness_timeout_s = 0`` checks once and fails at once, as before.
    """

    command: str = ""
    args: list[str] = field(default_factory=list)
    # Relative to the config file's folder; empty means that folder.
    working_dir: str = ""
    env: dict[str, str] = field(default_factory=dict)
    readiness_timeout_s: float = 0.0
    poll_interval_s: float = 2.0
    # How long a launched server gets to exit after it is asked to stop, before it is killed.
    stop_timeout_s: float = 15.0
    # If a server Jig launched exits on its own, Jig restarts it up to this many times in a row
    # (a run of 10 minutes or more resets the count), waiting restart_delay_s first.
    max_restarts: int = 3
    restart_delay_s: float = 5.0


@dataclass(frozen=True)
class WebFetchConfig:
    timeout_s: float = 20.0
    max_bytes: int = 2_000_000
    max_chars: int = 12_000
    max_redirects: int = 5


@dataclass(frozen=True)
class RuntimeConfig:
    agent_id: str = "default"
    timezone: str = "Europe/London"
    max_steps: int = 12
    max_concurrent_tasks: int = 3
    heartbeat_s: float = 2.0
    # Below this context size Jig logs a warning at start-up (when the server reports its context).
    min_context_tokens: int = 32768


@dataclass(frozen=True)
class VisionConfig:
    # When true, Jig sends a real test image at start-up and refuses to start if the answer is wrong.
    # Needs a vision-capable model (for llama.cpp, a model plus its --mmproj projector).
    enabled: bool = False


@dataclass(frozen=True)
class SandboxConfig:
    # "directory": file tools are confined to a folder (no code execution, no browser).
    # "container": code, shell and the headless browser run in a hardened per-agent Docker container.
    backend: str = "directory"
    image: str = "jig-sandbox:0.1.0"
    cpus: float = 2.0
    memory: str = "2g"
    pids_limit: int = 512
    tmp_size: str = "512m"
    shm_size: str = "256m"
    # Host address the egress proxy listens on; the in-container relay reaches it via host.docker.internal.
    egress_bind: str = "127.0.0.1"
    egress_ports: list[int] = field(default_factory=lambda: [80, 443])
    command_timeout_s: float = 120.0
    # "compose": long-running sandbox services on an internal-only network (see docs/container.md).
    # The proxy then listens on Jig's own address on that network, on egress_port.
    exec_service: str = "sandbox-exec:7010"
    browser_service: str = "sandbox-browser:7011"
    egress_port: int = 3128


@dataclass(frozen=True)
class VaultConfig:
    # "auto": Windows DPAPI on Windows, the OS keyring elsewhere.
    # "keyfile": AES-256-GCM in the database, with a key derived from key_file (for containers).
    backend: str = "auto"
    key_file: str = ""


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8766


@dataclass(frozen=True)
class RemoteConfig:
    """``[remote]``: use Jig from your other devices through ``tailscale serve`` (see ``jig.remote``)."""

    # Tailscale logins (for example "you@example.com") allowed through tailscale serve. Empty means only
    # the login that turned remote access on. Never a wildcard.
    allowed_logins: list[str] = field(default_factory=list)
    # Container mode only: the host's tailnet name (machine.tailnet.ts.net) that `tailscale serve` on the
    # host forwards to the published port. On the host, `jig remote enable` records it instead.
    hostname: str = ""


@dataclass(frozen=True)
class Config:
    model: EndpointConfig
    sentinel: EndpointConfig
    runtime: RuntimeConfig
    web_fetch: WebFetchConfig
    server: ServerConfig
    data_dir: Path
    sandbox_dir: Path
    source: Path
    vision: VisionConfig = field(default_factory=VisionConfig)
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
    model_launch: ModelLaunchConfig = field(default_factory=ModelLaunchConfig)
    vault: VaultConfig = field(default_factory=VaultConfig)
    remote: RemoteConfig = field(default_factory=RemoteConfig)
    # "host" (default) or "container": set explicitly by the container image and deploy/jig.toml
    # (top-level ``deployment`` key or JIG_DEPLOYMENT), never guessed. Autostart is off in a container.
    deployment: str = "host"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jig.db"


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a table")
    return value


def _build(cls: type, values: dict[str, Any], section: str) -> Any:
    unknown = set(values) - set(cls.__dataclass_fields__)
    if unknown:
        raise ConfigError(f"Unknown keys in [{section}]: {sorted(unknown)}")
    try:
        return cls(**values)
    except TypeError as exc:
        raise ConfigError(f"[{section}]: {exc}") from exc


def load_config(path: str | os.PathLike[str] | None = None, **overrides: Any) -> Config:
    """Load configuration. ``overrides`` may set ``data_dir``, ``sandbox_dir`` and ``sandbox_backend``."""
    config_path = Path(path or os.environ.get("JIG_CONFIG") or DEFAULT_CONFIG_PATH)
    if not config_path.is_file():
        raise ConfigError(f"Config file not found: {config_path}")
    with config_path.open("rb") as fh:
        raw = tomllib.load(fh)
    base = config_path.resolve().parent

    model_raw = dict(_section(raw, "model"))
    launch_raw = model_raw.pop("launch", {})
    if not isinstance(launch_raw, dict):
        raise ConfigError("[model.launch] must be a table")
    model_launch = _build(ModelLaunchConfig, launch_raw, "model.launch")
    if model_launch.readiness_timeout_s < 0 or model_launch.poll_interval_s <= 0:
        raise ConfigError("[model.launch] readiness_timeout_s must be >= 0 and poll_interval_s > 0")
    if model_launch.max_restarts < 0 or model_launch.restart_delay_s < 0:
        raise ConfigError("[model.launch] max_restarts and restart_delay_s must be >= 0")
    deployment = os.environ.get("JIG_DEPLOYMENT") or raw.get("deployment", "host")
    if deployment not in ("host", "container"):
        raise ConfigError(f"deployment (or JIG_DEPLOYMENT) must be 'host' or 'container', not {deployment!r}")
    if model_launch.command:
        work = Path(model_launch.working_dir or ".")
        model_launch = replace(model_launch, working_dir=str((work if work.is_absolute() else base / work).resolve()))
    if v := os.environ.get("JIG_MODEL_BASE_URL"):
        model_raw["base_url"] = v
    if v := os.environ.get("JIG_MODEL_NAME"):
        model_raw["name"] = v
    if "base_url" not in model_raw:
        raise ConfigError("[model] base_url is required")
    model = _build(EndpointConfig, model_raw, "model")

    # The Sentinel inherits every unset key from [model], so by default it uses the same
    # endpoint and model, but it can point at a different (for example smaller) model.
    sentinel_raw = dict(_section(raw, "sentinel"))
    inherited = {k: getattr(model, k) for k in EndpointConfig.__dataclass_fields__}
    if "base_url" in sentinel_raw and "name" not in sentinel_raw:
        inherited["name"] = ""  # a different server: discover its model rather than assuming the same name
    sentinel = _build(EndpointConfig, {**inherited, **sentinel_raw}, "sentinel")

    tools_raw = _section(raw, "tools")
    web_fetch = _build(WebFetchConfig, tools_raw.get("web_fetch", {}), "tools.web_fetch")

    server_raw = dict(_section(raw, "server"))
    if v := os.environ.get("JIG_HOST"):
        server_raw["host"] = v
    if v := os.environ.get("JIG_PORT"):
        server_raw["port"] = int(v)

    paths = _section(raw, "paths")
    data_dir = Path(overrides.get("data_dir") or os.environ.get("JIG_DATA_DIR") or paths.get("data_dir", "data"))
    sandbox_dir = Path(
        overrides.get("sandbox_dir") or os.environ.get("JIG_SANDBOX_DIR") or paths.get("sandbox_dir", "sandbox")
    )
    if not data_dir.is_absolute():
        data_dir = base / data_dir
    if not sandbox_dir.is_absolute():
        sandbox_dir = base / sandbox_dir

    return Config(
        model=model,
        sentinel=sentinel,
        runtime=_build(RuntimeConfig, _section(raw, "runtime"), "runtime"),
        web_fetch=web_fetch,
        server=_build(ServerConfig, server_raw, "server"),
        data_dir=data_dir.resolve(),
        sandbox_dir=sandbox_dir.resolve(),
        source=config_path.resolve(),
        vision=_build(VisionConfig, _section(raw, "vision"), "vision"),
        sandbox=_sandbox_config(_section(raw, "sandbox"), overrides.get("sandbox_backend")),
        model_launch=model_launch,
        vault=_vault_config(_section(raw, "vault")),
        remote=_remote_config(_section(raw, "remote"), deployment),
        deployment=deployment,
    )


def _remote_config(values: dict[str, Any], deployment: str) -> RemoteConfig:
    values = dict(values)
    if v := os.environ.get("JIG_REMOTE_HOSTNAME"):
        values["hostname"] = v
    cfg = _build(RemoteConfig, values, "remote")
    logins = [str(x).strip().lower() for x in cfg.allowed_logins]
    if any(not x or "*" in x or "?" in x for x in logins):
        raise ConfigError("[remote] allowed_logins must list exact Tailscale logins; wildcards are not allowed")
    hostname = cfg.hostname.strip().lower().rstrip(".")
    if hostname and deployment != "container":
        raise ConfigError("[remote] hostname is only for container mode; on the host, 'jig remote enable' "
                          "finds and records the tailnet name itself")
    if hostname and (not hostname.endswith(".ts.net") or "*" in hostname):
        raise ConfigError(f"[remote] hostname must be the exact tailnet name ending in .ts.net, not {hostname!r}")
    return replace(cfg, allowed_logins=logins, hostname=hostname)


def _vault_config(values: dict[str, Any]) -> VaultConfig:
    values = dict(values)
    if v := os.environ.get("JIG_VAULT_BACKEND"):
        values["backend"] = v
    if v := os.environ.get("JIG_VAULT_KEY_FILE"):
        values["key_file"] = v
    cfg = _build(VaultConfig, values, "vault")
    if cfg.backend not in ("auto", "dpapi", "keyring", "keyfile"):
        raise ConfigError(f"[vault] backend must be 'auto', 'dpapi', 'keyring' or 'keyfile', not {cfg.backend!r}")
    if cfg.backend == "keyfile" and not cfg.key_file:
        raise ConfigError("[vault] backend = 'keyfile' needs key_file (or JIG_VAULT_KEY_FILE), the path of the key")
    return cfg


def _sandbox_config(values: dict[str, Any], backend_override: str | None) -> SandboxConfig:
    values = dict(values)
    if backend := backend_override or os.environ.get("JIG_SANDBOX_BACKEND"):
        values["backend"] = backend
    cfg = _build(SandboxConfig, values, "sandbox")
    if cfg.backend not in ("directory", "container", "compose"):
        raise ConfigError(f"[sandbox] backend must be 'directory', 'container' or 'compose', not {cfg.backend!r}")
    return cfg
