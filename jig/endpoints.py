"""Where a model endpoint is (on your own machine or network, or in the cloud) and the cloud providers Jig knows.

Classification looks only at the address written in ``base_url``; it never resolves DNS, so the answer cannot
change underneath a running Jig. An endpoint is **local** when its host is:

- a loopback, private-LAN (RFC 1918, IPv6 unique-local), link-local or tailnet (100.64.0.0/10) address;
- ``localhost``, a single-label name (``gpu-box``, a Compose service such as ``ollama``), or a name ending in
  ``.localhost``, ``.local``, ``.lan``, ``.home.arpa``, ``.internal`` (``host.docker.internal``) or ``.ts.net``.

Everything else, including any public IP address and any other DNS name, is **cloud**: whatever is sent there
leaves your machine and your network. Cloud endpoints must use HTTPS and need explicit consent (``jig.cloud``).
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

_NETWORKS: list[tuple[str, Any]] = [
    ("loopback", ipaddress.ip_network("127.0.0.0/8")),
    ("loopback", ipaddress.ip_network("::1/128")),
    ("private network", ipaddress.ip_network("10.0.0.0/8")),
    ("private network", ipaddress.ip_network("172.16.0.0/12")),
    ("private network", ipaddress.ip_network("192.168.0.0/16")),
    ("tailnet", ipaddress.ip_network("fd7a:115c:a1e0::/48")),  # Tailscale's IPv6 range, inside fc00::/7
    ("private network", ipaddress.ip_network("fc00::/7")),  # unique-local IPv6
    ("link-local", ipaddress.ip_network("169.254.0.0/16")),
    ("link-local", ipaddress.ip_network("fe80::/10")),
    # Shared address space (RFC 6598). Tailscale gives every device on a tailnet an address here.
    ("tailnet", ipaddress.ip_network("100.64.0.0/10")),
]
_LOCAL_SUFFIXES = {".localhost": "local name", ".local": "local name", ".lan": "local name",
                   ".home.arpa": "local name", ".internal": "local name", ".ts.net": "tailnet"}


@dataclass(frozen=True)
class Location:
    kind: str  # "local" or "cloud"
    host: str
    reason: str  # why, in plain words: "loopback", "private network", "tailnet", "public host name", ...

    @property
    def is_cloud(self) -> bool:
        return self.kind == "cloud"

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "host": self.host, "reason": self.reason}


def endpoint_host(base_url: str) -> str:
    host = urlsplit(base_url).hostname
    if not host:
        raise ValueError(f"{base_url!r} has no host name")
    return host.lower().rstrip(".")


def origin(base_url: str) -> str:
    """``scheme://host:port``: what consent is given for. A different provider or port needs its own consent."""
    parts = urlsplit(base_url)
    port = parts.port or {"http": 80, "https": 443}.get(parts.scheme, 0)
    return f"{parts.scheme}://{endpoint_host(base_url)}:{port}"


def classify(base_url: str) -> Location:
    host = endpoint_host(base_url)
    try:
        ip = ipaddress.ip_address(host.strip("[]").split("%")[0])
    except ValueError:
        ip = None
    if ip is not None:
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if ip.is_unspecified:
            return Location("local", host, "loopback")
        for reason, net in _NETWORKS:
            if ip.version == net.version and ip in net:
                return Location("local", host, reason)
        return Location("cloud", host, "public address")
    if host == "localhost":
        return Location("local", host, "loopback")
    if "." not in host:
        return Location("local", host, "local name")
    for suffix, reason in _LOCAL_SUFFIXES.items():
        if host.endswith(suffix):
            return Location("local", host, reason)
    return Location("cloud", host, "public host name")


# Cloud providers -------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Provider:
    """What Jig must do differently for one provider's OpenAI-compatible API. Every difference is cited."""

    id: str
    label: str
    base_url: str
    docs: str
    # The request field that limits output length.
    max_tokens_field: str = "max_tokens"
    # "json_schema": response_format with a JSON schema. "tool_call": a single forced tool call whose
    # arguments are the answer, for APIs that ignore response_format.
    structured_output: str = "json_schema"
    # Request keys (from [model.sampling]) the provider documents as supported. None: any key, because the
    # provider rejects unknown keys with an error rather than ignoring them.
    sampling_keys: frozenset[str] | None = None
    temperature_max: float | None = None
    headers: dict[str, str] = field(default_factory=dict)
    # Query parameters for GET /models (for example, a page size large enough to list every model).
    models_params: dict[str, str] = field(default_factory=dict)
    # Prefixes the provider may put before model ids in GET /models.
    model_id_prefixes: tuple[str, ...] = ()
    key_url: str = ""

    @property
    def host(self) -> str:
        return endpoint_host(self.base_url)


PROVIDERS: dict[str, Provider] = {
    # https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create
    # max_tokens "is now deprecated in favor of max_completion_tokens, and is not compatible with o-series
    # models". Unknown request fields are rejected (HTTP 400), so any sampling key is passed through.
    # https://developers.openai.com/api/docs/guides/reasoning: Chat Completions does not support function
    # calling with GPT-6 Astra or GPT-6.1 Sol; Jig's tool-calling check fails for those models.
    "openai": Provider(
        id="openai", label="OpenAI", base_url="https://api.openai.com/v1",
        docs="https://developers.openai.com/api/docs/guides/function-calling",
        max_tokens_field="max_completion_tokens",
        key_url="https://platform.openai.com/api-keys",
    ),
    # https://openrouter.ai/docs/api_reference/overview: OpenAI-shaped schema; max_tokens; tools are passed
    # through or transformed per upstream provider. https://openrouter.ai/docs/guides/routing/provider-selection:
    # without provider.require_parameters, providers "will ignore unknown parameters", so the profile sets it.
    "openrouter": Provider(
        id="openrouter", label="OpenRouter", base_url="https://openrouter.ai/api/v1",
        docs="https://openrouter.ai/docs/guides/features/tool-calling",
        key_url="https://openrouter.ai/settings/keys",
    ),
    # https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk: response_format is
    # "Ignored", tool "strict" is ignored, temperature above 1 "capped at 1", and "most unsupported fields are
    # silently ignored". So structured output uses a forced tool call (tool_choice is "Fully supported"), and
    # only the documented sampling keys are accepted. GET /v1/models pages 20 at a time by default (limit up
    # to 1000; https://platform.claude.com/docs/en/api/models/list).
    "anthropic": Provider(
        id="anthropic", label="Anthropic", base_url="https://api.anthropic.com/v1",
        docs="https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk",
        structured_output="tool_call",
        sampling_keys=frozenset({"temperature", "top_p", "stop", "parallel_tool_calls", "thinking"}),
        temperature_max=1.0,
        headers={"anthropic-version": "2023-06-01"},
        models_params={"limit": "1000"},
        key_url="https://platform.claude.com/settings/keys",
    ),
    # https://ai.google.dev/gemini-api/docs/openai: base URL .../v1beta/openai/; function calling, streaming,
    # structured output (response_format) and reasoning_effort are documented; Gemini-only options go in
    # extra_body; other parameters "will be silently ignored", so only the documented keys are accepted.
    # Gemini 3 needs its thought signatures sent back with tool calls
    # (https://ai.google.dev/gemini-api/docs/generate-content/thinking#signatures); Jig keeps every extra
    # field of a tool call, so they are.
    "gemini": Provider(
        id="gemini", label="Google Gemini", base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        docs="https://ai.google.dev/gemini-api/docs/openai",
        sampling_keys=frozenset({"temperature", "top_p", "stop", "reasoning_effort", "extra_body"}),
        model_id_prefixes=("models/",),
        key_url="https://aistudio.google.com/app/apikey",
    ),
}

# Keys Jig itself sets in every request; [model.sampling] must not set them.
RESERVED_REQUEST_KEYS = frozenset({"model", "messages", "stream", "tools", "tool_choice", "response_format",
                                   "max_tokens", "max_completion_tokens"})
