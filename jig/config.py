"""Configuration loaded from a TOML file with a small set of environment overrides.

Jig is model-agnostic: the endpoint, model name and sampling parameters all
come from config. Example profiles for common local servers, and for the
supported cloud providers, live in ``profiles/``.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .endpoints import PROVIDERS, RESERVED_REQUEST_KEYS, Location, Provider, classify, origin
from .errors import ConfigError

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "jig.toml"
# Vault secrets holding model API keys. Tools can never use them (core rule secret-allowlist).
MODEL_KEY_PREFIX = "model-key."


@dataclass(frozen=True)
class EndpointConfig:
    """One OpenAI-compatible chat endpoint."""

    base_url: str
    # Empty means "discover from /v1/models"; discovery fails loudly unless exactly one model is served.
    name: str = ""
    # Name of an environment variable holding the API key, if the server needs one.
    api_key_env: str = ""
    # Or the name of a vault secret holding it ('jig model key set <name>' stores "model-key.<name>").
    api_key_secret: str = ""
    # A known cloud provider ("openai", "openrouter", "anthropic", "gemini"; see jig.endpoints), so Jig
    # handles its documented differences. Empty for local servers and other OpenAI-compatible endpoints.
    provider: str = ""
    # Required (with a one-time confirmation, 'jig model cloud confirm') before Jig sends anything to a
    # cloud endpoint. Never inherited: the Sentinel needs its own.
    allow_cloud: bool = False
    # "json_schema" (response_format) or "tool_call" (a forced tool call). Empty: the provider's default.
    structured_output: str = ""
    # A PEM file of certificate authorities to trust for this endpoint (a server with its own certificate).
    ca_file: str = ""
    # Extra HTTP headers sent with every request (for example anthropic-workspace-id). Not for the API key.
    headers: dict[str, str] = field(default_factory=dict)
    connect_timeout_s: float = 5.0
    read_timeout_s: float = 600.0
    # Output limit per request (reasoning counts towards it). Unset: Jig sends none, so the server's context
    # window is the only bound. Required only where the provider's API requires it (Anthropic).
    max_tokens: int | None = None
    # Sent verbatim in each request body. Keys the server does not support should be left out.
    sampling: dict[str, Any] = field(default_factory=dict)

    @property
    def api_key(self) -> str | None:
        """The key from ``api_key_env``. A vault key (``api_key_secret``) is resolved by ``jig.cloud``."""
        if not self.api_key_env:
            return None
        value = os.environ.get(self.api_key_env)
        if not value:
            raise ConfigError(f"environment variable {self.api_key_env} (api_key_env) is not set")
        return value

    @property
    def provider_info(self) -> Provider | None:
        return PROVIDERS.get(self.provider) if self.provider else None

    @property
    def structured_output_mode(self) -> str:
        if self.structured_output:
            return self.structured_output
        return self.provider_info.structured_output if self.provider_info else "json_schema"

    @property
    def location(self) -> Location:
        return classify(self.base_url)


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
class AutostartConfig:
    """``[autostart]``: how 'Start with Windows' (or login, on macOS and Linux) is registered for this install."""

    # The task name, launchd label or systemd unit. Empty: the backend's default (\Jig\Jig Agent on Windows).
    # Give each install its own, so two Jigs on one computer never share (or remove) one entry.
    entry: str = ""
    # "service": start Jig itself at login. "tray": start the tray icon, which starts Jig (Windows installs).
    launcher: str = "service"


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
class ConnectorLimits:
    """``[connectors.<provider>]``: fixed limits on what Jig may send through a connected account, checked by
    the gate before the safety checker or an approval, and (Microsoft and GitHub only) your own app to sign
    in with instead of the one built into Jig."""

    # Exact addresses (case-insensitive) Jig may send mail or invitations to. Empty: anyone, and every
    # send still needs approval.
    allowed_recipients: list[str] = field(default_factory=list)
    # Exact names or ids of the only places Jig may change (calendars, folders, repositories, channels,
    # rooms, phone numbers; each connector's section in docs/connectors-setup.md says which). Empty: any.
    allowed_targets: list[str] = field(default_factory=list)
    # When set, everything Jig writes (a subject after any "Re: ", an event or file name, a message or
    # comment) must start with this, for example "[Jig test]".
    required_prefix: str = ""
    # Microsoft and GitHub: your organisation's own app instead of Jig's built-in one. Microsoft: the
    # Application (client) ID of your app registration, and optionally your tenant (its ID or domain;
    # default "common"). GitHub: your GitHub App's client ID, and its URL name (app_slug) for the
    # "choose repositories" link.
    client_id: str = ""
    tenant: str = ""
    app_slug: str = ""

# Connectors whose [connectors.<id>] may name the user's own app, and the keys each takes.
CONNECTOR_APP_KEYS = {"microsoft": ("client_id", "tenant"), "github": ("client_id", "app_slug")}

# Connectors that read [connectors.<id>]; see jig.connectors.
CONNECTOR_IDS = ("gmail", "google-calendar", "google-drive", "microsoft", "github", "slack", "discord", "matrix",
                 "signal")


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
    autostart: AutostartConfig = field(default_factory=AutostartConfig)
    connectors: dict[str, ConnectorLimits] = field(default_factory=dict)
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
    # utf-8-sig: Windows PowerShell and some editors save UTF-8 with a byte-order mark, which TOML rejects.
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{config_path} is not valid UTF-8: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{config_path} is not valid TOML: {exc}") from exc
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
    model = _check_endpoint(_build(EndpointConfig, model_raw, "model"), "model", base)
    if model_launch.command and model.location.is_cloud:
        raise ConfigError(f"[model.launch] starts a local model server, but [model] base_url ({model.base_url}) is a "
                          "cloud endpoint; remove [model.launch] or point base_url at the local server")

    # The Sentinel inherits every unset key from [model], so by default it uses the same
    # endpoint and model, but it can point at a different (for example smaller, local) model.
    sentinel_raw = dict(_section(raw, "sentinel"))
    inherited = {k: getattr(model, k) for k in EndpointConfig.__dataclass_fields__}
    inherited["allow_cloud"] = False  # consent to a cloud endpoint is per role, never inherited
    if "base_url" in sentinel_raw:
        if "name" not in sentinel_raw:
            inherited["name"] = ""  # a different server: discover its model rather than assuming the same name
        if _same_origin(sentinel_raw["base_url"], model.base_url) is False:
            # A different server never gets the agent's API key, headers or provider-specific settings.
            defaults = EndpointConfig(base_url="")
            for key in ("api_key_env", "api_key_secret", "provider", "structured_output", "ca_file", "headers",
                        "max_tokens"):
                inherited[key] = getattr(defaults, key)
            if model.provider or model.location.is_cloud:
                inherited["sampling"] = {}
    sentinel = _check_endpoint(_build(EndpointConfig, {**inherited, **sentinel_raw}, "sentinel"), "sentinel", base)

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
        autostart=_autostart_config(_section(raw, "autostart")),
        connectors=_connectors_config(_section(raw, "connectors")),
        deployment=deployment,
    )


def _connectors_config(values: dict[str, Any]) -> dict[str, ConnectorLimits]:
    out: dict[str, ConnectorLimits] = {}
    for name, table in values.items():
        if name not in CONNECTOR_IDS:
            raise ConfigError(f"[connectors.{name}]: unknown connector; known: {list(CONNECTOR_IDS)}")
        if not isinstance(table, dict):
            raise ConfigError(f"[connectors.{name}] must be a table")
        cfg = _build(ConnectorLimits, table, f"connectors.{name}")
        recipients = [str(r).strip().lower() for r in cfg.allowed_recipients]
        if any(not r or "@" not in r or "*" in r or any(c.isspace() for c in r) for r in recipients):
            raise ConfigError(f"[connectors.{name}] allowed_recipients must list exact addresses, without wildcards")
        targets = [str(t).strip() for t in cfg.allowed_targets]
        if any(not t or "*" in t for t in targets):
            raise ConfigError(f"[connectors.{name}] allowed_targets must list exact names or ids, without wildcards")
        allowed = CONNECTOR_APP_KEYS.get(name, ())
        for key in ("client_id", "tenant", "app_slug"):
            if str(getattr(cfg, key)).strip() and key not in allowed:
                where = ", ".join(f"[connectors.{n}]" for n, keys in CONNECTOR_APP_KEYS.items() if key in keys)
                raise ConfigError(f"[connectors.{name}] has no {key} setting; it belongs in {where}")
        out[name] = replace(cfg, allowed_recipients=recipients, allowed_targets=targets,
                            client_id=str(cfg.client_id).strip(), tenant=str(cfg.tenant).strip(),
                            app_slug=str(cfg.app_slug).strip())
    return out


def _same_origin(a: str, b: str) -> bool | None:
    try:
        return origin(a) == origin(b)
    except ValueError:
        return None  # reported by _check_endpoint


def _check_endpoint(ep: EndpointConfig, section: str, base: Path) -> EndpointConfig:
    """Validate one endpoint. Cloud endpoints must use HTTPS; provider differences are checked, never guessed."""
    parts = urlsplit(ep.base_url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError(f"[{section}] base_url must be an http:// or https:// URL, not {ep.base_url!r}")
    loc = classify(ep.base_url)
    if loc.is_cloud and parts.scheme != "https":
        raise ConfigError(f"[{section}] base_url {ep.base_url} is a cloud endpoint ({loc.host}, a {loc.reason}), so "
                          "it must use https://. Jig never sends your conversation over the internet unencrypted.")
    provider = PROVIDERS.get(ep.provider) if ep.provider else None
    if ep.provider and provider is None:
        raise ConfigError(f"[{section}] provider must be one of {sorted(PROVIDERS)} (or left out), not {ep.provider!r}")
    if provider and loc.host != provider.host:
        raise ConfigError(f"[{section}] provider = {ep.provider!r} is {provider.label}'s API at {provider.host}, but "
                          f"base_url points at {loc.host}; use base_url = \"{provider.base_url}\" or leave provider out")
    if ep.api_key_env and ep.api_key_secret:
        raise ConfigError(f"[{section}] set api_key_env or api_key_secret, not both")
    if ep.api_key_secret and not ep.api_key_secret.startswith(MODEL_KEY_PREFIX):
        raise ConfigError(f"[{section}] api_key_secret must name a model key, \"{MODEL_KEY_PREFIX}<name>\" (stored with "
                          f"'jig model key set <name>'), not {ep.api_key_secret!r}")
    if ep.structured_output not in ("", "json_schema", "tool_call"):
        raise ConfigError(f"[{section}] structured_output must be 'json_schema' or 'tool_call', not "
                          f"{ep.structured_output!r}")
    if reserved := sorted(RESERVED_REQUEST_KEYS & set(ep.sampling)):
        raise ConfigError(f"[{section}.sampling] must not set {reserved}: Jig sets these itself (the output limit is "
                          f"[{section}] max_tokens)")
    if ep.max_tokens is not None and (not isinstance(ep.max_tokens, int) or isinstance(ep.max_tokens, bool)
                                      or ep.max_tokens < 1):
        raise ConfigError(f"[{section}] max_tokens must be a whole number of at least 1, not {ep.max_tokens!r}; "
                          "leave it out for no output limit")
    if provider and provider.sampling_keys is not None:
        if unsupported := sorted(set(ep.sampling) - provider.sampling_keys):
            raise ConfigError(
                f"[{section}.sampling] {unsupported} are not supported by {provider.label}'s OpenAI-compatible API, "
                "which ignores unsupported settings without saying so; Jig refuses rather than send settings that "
                f"would do nothing. Supported: {sorted(provider.sampling_keys)}. See {provider.docs}")
    if provider and provider.temperature_max is not None:
        temperature = ep.sampling.get("temperature")
        if isinstance(temperature, (int, float)) and temperature > provider.temperature_max:
            raise ConfigError(f"[{section}.sampling] temperature = {temperature} is above {provider.label}'s maximum "
                              f"of {provider.temperature_max}, which it would silently cap; set it to "
                              f"{provider.temperature_max} or less")
    if provider and provider.max_tokens_required and ep.max_tokens is None:
        raise ConfigError(f"[{section}] max_tokens is required: {provider.label}'s API rejects a request without an "
                          "output limit. Set it to the maximum output documented for your model (thinking counts "
                          f"towards it); profiles/{provider.id}.toml shows the value for its model. See {provider.docs}")
    if any(h.lower() in ("authorization", "x-api-key", "api-key") for h in ep.headers):
        raise ConfigError(f"[{section}.headers] must not carry the API key; use api_key_secret (the vault) or "
                          "api_key_env")
    ca_file = ep.ca_file
    if ca_file:
        path = Path(ca_file)
        path = (path if path.is_absolute() else base / path).resolve()
        if not path.is_file():
            raise ConfigError(f"[{section}] ca_file {path} does not exist")
        ca_file = str(path)
    return replace(ep, ca_file=ca_file)


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


def _autostart_config(values: dict[str, Any]) -> AutostartConfig:
    cfg = _build(AutostartConfig, values, "autostart")
    if cfg.launcher not in ("service", "tray"):
        raise ConfigError(f"[autostart] launcher must be 'service' or 'tray', not {cfg.launcher!r}")
    return replace(cfg, entry=cfg.entry.strip())


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
