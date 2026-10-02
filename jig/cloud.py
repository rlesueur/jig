"""Using a cloud model safely: explicit consent, and model API keys from the vault.

Jig is built for local models. A cloud endpoint (``jig.endpoints.classify``) is used only when both are true:

1. the config says so for that role: ``[model] allow_cloud = true`` for the agent, ``[sentinel] allow_cloud =
   true`` for the safety checker (never inherited), and
2. the user has confirmed it once with ``jig model cloud confirm``, which records ``model.cloud_consent.given``
   in the audit log for that role and that exact origin (``https://host:port``). A new provider needs a new
   confirmation; ``jig model cloud revoke`` withdraws it.

Otherwise Jig refuses to start, before it contacts the endpoint, and says what would leave the machine.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .audit import AuditLog
from .config import MODEL_KEY_PREFIX, Config, EndpointConfig
from .endpoints import Location, origin
from .errors import ConfigError, SecretNotFound

CONSENT_KIND = "model.cloud_consent"
GIVEN, REVOKED = f"{CONSENT_KIND}.given", f"{CONSENT_KIND}.revoked"
ROLES = {"agent": "the agent", "sentinel": "the safety checker (Sentinel)"}

# What leaves the machine, in the words the CLI, the start-up refusal and the docs all use.
SENT_BY_AGENT = [
    "your conversation: what you type and Jig's replies",
    "Jig's instructions and the list of tools it can use",
    "memory snippets Jig puts in its prompts, and anything it looks up in memory",
    "tool results: web pages, files and command output the agent reads",
    "images you share, when vision is on",
]
SENT_BY_SENTINEL = [
    "each action Jig wants to take, with its arguments (file contents, web addresses, messages)",
    "the request or task it is for",
]
STAYS_LOCAL = ("Your memory database, audit log, rules, approvals and vault secrets stay on this machine; only what "
               "goes into a request is sent. The provider's own terms decide how long it keeps what it receives.")


class CloudConsentRequired(ConfigError):
    """A cloud endpoint is configured without the config acknowledgement or the recorded confirmation."""


@dataclass(frozen=True)
class CloudUse:
    role: str  # "agent" or "sentinel"
    endpoint: EndpointConfig
    location: Location

    @property
    def origin(self) -> str:
        return origin(self.endpoint.base_url)

    @property
    def provider_label(self) -> str:
        info = self.endpoint.provider_info
        return info.label if info else self.location.host

    @property
    def sends(self) -> list[str]:
        return SENT_BY_AGENT if self.role == "agent" else SENT_BY_SENTINEL


def cloud_uses(config: Config) -> list[CloudUse]:
    out = []
    for role, ep in (("agent", config.model), ("sentinel", config.sentinel)):
        loc = ep.location
        if loc.is_cloud:
            out.append(CloudUse(role, ep, loc))
    return out


def disclosure(uses: list[CloudUse]) -> str:
    lines = []
    for use in uses:
        lines.append(f"{ROLES[use.role][0].upper() + ROLES[use.role][1:]} would use a cloud model: "
                     f"{use.provider_label} at {use.location.host}.")
        lines.append(f"  Sent to {use.location.host} with every request:")
        lines += [f"    - {s}" for s in use.sends]
    lines.append(STAYS_LOCAL)
    return "\n".join(lines)


def consent_state(audit: AuditLog, role: str, endpoint_origin: str) -> dict[str, Any] | None:
    """The latest consent record for this role and origin (given or revoked), or None."""
    for row in audit.query(kind=CONSENT_KIND, newest_first=True, limit=5000):
        data = json.loads(row["data_json"])
        if data.get("role") == role and data.get("origin") == endpoint_origin:
            return {"kind": row["kind"], "ts": row["ts"], "id": row["id"], **data}
    return None


def has_consent(audit: AuditLog, use: CloudUse) -> bool:
    state = consent_state(audit, use.role, use.origin)
    return bool(state and state["kind"] == GIVEN)


def require_consent(config: Config, audit: AuditLog) -> list[CloudUse]:
    """Refuse (``CloudConsentRequired``) unless every cloud endpoint is acknowledged in the config and confirmed."""
    uses = cloud_uses(config)
    not_allowed = [u for u in uses if not u.endpoint.allow_cloud]
    unconfirmed = [u for u in uses if u.endpoint.allow_cloud and not has_consent(audit, u)]
    if not (not_allowed or unconfirmed):
        return uses
    steps = []
    for u in not_allowed:
        section = "model" if u.role == "agent" else "sentinel"
        if u.role == "sentinel" and u.endpoint.base_url == config.model.base_url:
            steps.append("The safety checker inherits [model], so it would use the same cloud endpoint. Recommended: "
                         "keep it on a local model by setting [sentinel] base_url (and name) to your local server. Or, "
                         "to send safety checks to the cloud too, add allow_cloud = true under [sentinel].")
        else:
            steps.append(f"Add allow_cloud = true under [{section}] in {config.source} to accept this.")
    if unconfirmed or not_allowed:
        steps.append("Then run 'jig model cloud confirm' once to confirm it; that is recorded in the audit log.")
    raise CloudConsentRequired(
        "Jig did not start: it is set up to use a cloud model, which needs your explicit consent first.\n\n"
        + disclosure(not_allowed + unconfirmed) + "\n\n" + "\n".join(f"- {s}" for s in steps)
        + "\n\nOr point base_url at a local model server to keep everything on this machine.")


def record_consent(audit: AuditLog, use: CloudUse, *, via: str) -> int:
    return audit.record(GIVEN, f"cloud model confirmed for {use.role}: {use.provider_label} at {use.location.host}",
                        actor="user", role=use.role, origin=use.origin, host=use.location.host,
                        provider=use.endpoint.provider or None, sends=use.sends, via=via)


def record_revocation(audit: AuditLog, role: str, endpoint_origin: str, *, via: str) -> int:
    return audit.record(REVOKED, f"cloud model consent withdrawn for {role} ({endpoint_origin})", actor="user",
                        role=role, origin=endpoint_origin, via=via)


# Model API keys -------------------------------------------------------------------------------------------

def key_secret_name(name: str) -> str:
    return name if name.startswith(MODEL_KEY_PREFIX) else f"{MODEL_KEY_PREFIX}{name}"


def resolve_api_key(endpoint: EndpointConfig, vault: Any, *, role: str) -> str | None:
    """The endpoint's API key: from the vault (``api_key_secret``) or the environment (``api_key_env``)."""
    if not endpoint.api_key_secret:
        return endpoint.api_key
    if vault is None:
        raise ConfigError(f"[{'model' if role == 'agent' else 'sentinel'}] api_key_secret needs the vault")
    try:
        return vault.reveal(endpoint.api_key_secret)
    except SecretNotFound as exc:
        short = endpoint.api_key_secret.removeprefix(MODEL_KEY_PREFIX)
        raise ConfigError(f"the API key for {ROLES[role]} ({endpoint.api_key_secret!r}) is not in the vault; store "
                          f"it with 'jig model key set {short}'") from exc


def connection_summary(config: Config, uses_consent: AuditLog | None = None) -> dict[str, Any]:
    """Where the agent and the safety checker run, for /status and the UI. Never includes a key."""
    out: dict[str, Any] = {}
    for role, ep in (("agent", config.model), ("sentinel", config.sentinel)):
        loc = ep.location
        info = ep.provider_info
        entry: dict[str, Any] = {**loc.as_dict(), "provider": ep.provider or None,
                                 "provider_label": info.label if info else None,
                                 "key_source": "vault" if ep.api_key_secret else "environment" if ep.api_key_env
                                 else None}
        if loc.is_cloud:
            entry["sends"] = SENT_BY_AGENT if role == "agent" else SENT_BY_SENTINEL
            if uses_consent is not None:
                state = consent_state(uses_consent, role, origin(ep.base_url))
                entry["confirmed_at"] = state["ts"] if state and state["kind"] == GIVEN else None
        out[role] = entry
    out["sentinel"]["same_endpoint_as_agent"] = config.sentinel.base_url.rstrip("/") == config.model.base_url.rstrip("/")
    return out
