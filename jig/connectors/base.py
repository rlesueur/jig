"""Connected accounts: the shared framework every connector uses.

A *provider* (``gmail`` and so on) describes how to authorise and which access levels it offers. A
*connection* is the user's grant for one provider: its metadata (account, access level, scopes, status)
is in the ``connections`` table; its tokens are only in the vault, as ``connector.<provider>.grant``.

``Connectors`` is what tools get as ``ctx.connectors``. It sends an authorised request for a tool,
refreshing the access token when needed, and backs off when the provider asks it to. It never returns a
token: every token value it handles is added to the gate's redaction set, so none can appear in a tool
result, an error or the audit log. A core rule stops any tool from naming a ``connector.*`` secret.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..audit import AuditLog
from ..db import Database, dumps, now_iso
from ..errors import ConnectorAuthError, ConnectorError, ConnectorNotConnected, SecretNotFound
from ..vault import Vault

SECRET_PREFIX = "connector."
# Refresh this long before the provider's stated expiry, so a token never expires mid-request.
REFRESH_MARGIN_S = 120.0
# Waiting when a provider says "slow down" (HTTP 429, or 503 with Retry-After): at most this many
# retries and this long in total; then the call fails and says so.
MAX_RETRIES = 3
MAX_TOTAL_WAIT_S = 30.0
IDEMPOTENT = frozenset({"GET", "HEAD"})

STATUS_CONNECTED = "connected"
STATUS_NEEDS_RECONNECT = "needs_reconnect"


def grant_secret(provider: str) -> str:
    return f"{SECRET_PREFIX}{provider}.grant"


def client_secret_name(family: str) -> str:
    return f"{SECRET_PREFIX}{family}.client"


@dataclass(frozen=True)
class AccessLevel:
    name: str
    scopes: tuple[str, ...]
    description: str


@dataclass
class Grant:
    """OAuth tokens (or a personal token) for one connection. Lives only in the vault."""

    access_token: str
    refresh_token: str | None = None
    expires_at: float | None = None  # epoch seconds
    scopes: list[str] = field(default_factory=list)
    token_type: str = "Bearer"  # the Authorization scheme ("Bot" for Discord)
    # Non-secret details of the connection the connector needs (a Matrix homeserver, a Signal number).
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({"access_token": self.access_token, "refresh_token": self.refresh_token,
                           "expires_at": self.expires_at, "scopes": self.scopes, "token_type": self.token_type,
                           "extra": self.extra})

    @classmethod
    def from_json(cls, raw: str) -> Grant:
        d = json.loads(raw)
        return cls(access_token=d["access_token"], refresh_token=d.get("refresh_token"),
                   expires_at=d.get("expires_at"), scopes=list(d.get("scopes") or []),
                   token_type=d.get("token_type") or "Bearer", extra=dict(d.get("extra") or {}))

    def values(self) -> list[str]:
        return [v for v in (self.access_token, self.refresh_token) if v]

    def expiring(self) -> bool:
        return self.expires_at is not None and time.time() > self.expires_at - REFRESH_MARGIN_S


# refresh(http, vault, grant) -> a new Grant; raises ConnectorAuthError when the provider refuses.
RefreshFn = Callable[[httpx.AsyncClient, Vault, Grant], Awaitable[Grant]]
# revoke(http, vault, grant) -> a sentence saying what happened at the provider.
RevokeFn = Callable[[httpx.AsyncClient, Vault, Grant], Awaitable[str]]


@dataclass(frozen=True)
class Input:
    """Something ``jig connect`` asks for when a provider has no browser sign-in (a personal token)."""

    name: str
    prompt: str
    secret: bool = False  # asked without echo (or read from stdin with --stdin); never printed or logged
    optional: bool = False  # may be left blank


# show_code({"user_code", "verification_uri", "expires_in"}): a device sign-in's code is ready to show.
ShowCodeFn = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class ProviderSpec:
    id: str  # "gmail"
    label: str  # "Gmail"
    family: str  # whose app client it uses ("google"); its client is the vault secret connector.<family>.client
    access_levels: dict[str, AccessLevel]
    default_access: str
    # Hosts this connector may send authorised requests to. Anything else is refused, so a token can
    # never be sent to a host named by the model or by content it has read.
    api_hosts: frozenset[str]
    refresh: RefreshFn | None = None
    revoke: RevokeFn | None = None
    # "oauth": connect(http, store, level, open_browser, ready) -> (Grant, account), a browser sign-in.
    # "device": connect(http, store, level, show_code) -> (Grant, account), a code the user types in at
    #           the provider's site.
    # "token": connect(http, store, level, values) -> (Grant, account), with ``inputs`` typed in.
    kind: str = "oauth"
    connect: Callable[..., Awaitable[tuple[Grant, str]]] | None = None
    inputs: tuple[Input, ...] = ()
    # A second way in for a provider whose main sign-in isn't a token: connect(http, store, level, values)
    # with ``inputs`` (GitHub's fine-grained personal access token, for advanced users).
    token_connect: Callable[..., Awaitable[tuple[Grant, str]]] | None = None
    needs_client: bool = True  # an app client (connector.<family>.client) must be stored first
    # For a provider with an app Jig ships (or one the user brings): client_status(store) ->
    # {"configured": bool, "source": "built-in" | "config" | "vault" | None, "problem": str | None}.
    client_status: Callable[[Any], dict[str, Any]] | None = None
    # Extra hosts that depend on the connection (a Matrix homeserver), from the stored grant.
    grant_hosts: Callable[[Grant], frozenset[str]] | None = None
    # Where the user removes the app's access on the provider's side.
    manage_url: str = ""
    docs: str = "docs/connectors-setup.md"

    def hosts(self, grant: Grant) -> frozenset[str]:
        return self.api_hosts | (self.grant_hosts(grant) if self.grant_hosts else frozenset())

    @property
    def methods(self) -> tuple[str, ...]:
        """The ways to connect, main one first."""
        return (self.kind, "token") if self.token_connect else (self.kind,)


PROVIDERS: dict[str, ProviderSpec] = {}


def register_provider(spec: ProviderSpec) -> ProviderSpec:
    if spec.id in PROVIDERS:
        raise ValueError(f"connector {spec.id!r} is already registered")
    if spec.default_access not in spec.access_levels:
        raise ValueError(f"connector {spec.id!r}: default access {spec.default_access!r} is not one of its levels")
    PROVIDERS[spec.id] = spec
    return spec


def provider(name: str) -> ProviderSpec:
    try:
        return PROVIDERS[name]
    except KeyError:
        raise ConnectorError(f"unknown connector {name!r}; known: {', '.join(sorted(PROVIDERS))}") from None


class ConnectionStore:
    """Connection metadata in SQLite and tokens in the vault. Safe to use from the CLI while Jig runs."""

    def __init__(self, db: Database, vault: Vault, audit: AuditLog, settings: dict[str, Any] | None = None):
        self.db = db
        self.vault = vault
        self.audit = audit
        # [connectors.<id>] from jig.toml (jig.config.ConnectorLimits), for the app a connector signs in with.
        self.settings = settings or {}

    def setting(self, name: str, key: str) -> str:
        cfg = self.settings.get(name)
        return str(getattr(cfg, key, "") or "").strip() if cfg is not None else ""

    def client_state(self, spec: ProviderSpec) -> dict[str, Any]:
        if spec.client_status is not None:
            return spec.client_status(self)
        if not spec.needs_client:
            return {"configured": True, "source": None, "problem": None}
        if self.has_client(spec.family):
            return {"configured": True, "source": "vault", "problem": None}
        return {"configured": False, "source": None,
                "problem": f"no {spec.family} app client stored yet"}

    def get(self, name: str) -> dict[str, Any] | None:
        row = self.db.one("SELECT * FROM connections WHERE provider = ?", (name,))
        if row is None:
            return None
        row["scopes"] = json.loads(row.pop("scopes_json"))
        return row

    def status(self) -> list[dict[str, Any]]:
        out = []
        for spec in sorted(PROVIDERS.values(), key=lambda s: s.label):
            row = self.get(spec.id)
            client = self.client_state(spec)
            out.append({
                "provider": spec.id, "label": spec.label, "family": spec.family,
                "connected": bool(row and row["status"] == STATUS_CONNECTED),
                "status": row["status"] if row else "not_connected",
                "account": row["account"] if row else None,
                "access": row["access"] if row else None,
                "scopes": row["scopes"] if row else [],
                "last_error": row["last_error"] if row else None,
                "connected_at": row["connected_at"] if row else None,
                "kind": spec.kind,
                "methods": list(spec.methods),
                "inputs": [{"name": i.name, "prompt": i.prompt, "secret": i.secret, "optional": i.optional}
                           for i in spec.inputs],
                "client_configured": client["configured"],
                "client_source": client["source"],
                "client_problem": client["problem"],
                "install_url": client.get("install_url"),
                "access_levels": {k: {"scopes": list(v.scopes), "description": v.description}
                                  for k, v in spec.access_levels.items()},
                "default_access": spec.default_access,
                "manage_url": spec.manage_url,
            })
        return out

    def has_client(self, family: str) -> bool:
        return self.db.one("SELECT 1 AS x FROM secrets WHERE name = ?", (client_secret_name(family),)) is not None

    def client(self, family: str) -> dict[str, Any]:
        try:
            return json.loads(self.vault.reveal(client_secret_name(family)))
        except SecretNotFound:
            raise ConnectorNotConnected(
                f"no {family} app client is stored; see docs/connectors-setup.md and run 'jig connect' with "
                "--client-json, or without it to be asked for the client ID and secret") from None

    def set_client(self, family: str, client: dict[str, Any], *, via: str) -> None:
        self.vault.set(client_secret_name(family), json.dumps(client), allowed_tools=[])
        self.audit.record("connector.client_set", f"{family} app client stored in the vault", actor="user",
                          family=family, client_id=client.get("client_id"), via=via)

    def delete_client(self, family: str, *, via: str) -> bool:
        try:
            self.vault.delete(client_secret_name(family))
        except SecretNotFound:
            return False
        self.audit.record("connector.client_deleted", f"{family} app client deleted from the vault", actor="user",
                          family=family, via=via)
        return True

    def grant(self, name: str) -> Grant:
        try:
            return Grant.from_json(self.vault.reveal(grant_secret(name)))
        except SecretNotFound:
            raise ConnectorNotConnected(f"{provider(name).label} is not connected. Connect it with "
                                        f"'jig connect {name}' (see docs/connectors-setup.md).") from None

    def save(self, name: str, grant: Grant, *, account: str | None, access: str, via: str) -> dict[str, Any]:
        spec = provider(name)
        self.vault.set(grant_secret(name), grant.to_json(), allowed_tools=[])
        ts = now_iso()
        self.db.execute(
            "INSERT INTO connections(provider, account, access, scopes_json, status, last_error, connected_at, "
            "updated_at) VALUES (?, ?, ?, ?, ?, NULL, ?, ?) ON CONFLICT(provider) DO UPDATE SET "
            "account=excluded.account, access=excluded.access, scopes_json=excluded.scopes_json, "
            "status=excluded.status, last_error=NULL, connected_at=excluded.connected_at, updated_at=excluded.updated_at",
            (name, account, access, dumps(grant.scopes), STATUS_CONNECTED, ts, ts))
        self.audit.record("connector.connected", f"{spec.label} connected as {account}", actor="user",
                          provider=name, account=account, access=access, scopes=grant.scopes, via=via)
        return self.get(name)  # type: ignore[return-value]

    def update_grant(self, name: str, grant: Grant) -> None:
        self.vault.set(grant_secret(name), grant.to_json(), allowed_tools=[])
        self.db.execute("UPDATE connections SET scopes_json = ?, status = ?, last_error = NULL, updated_at = ? "
                        "WHERE provider = ?", (dumps(grant.scopes), STATUS_CONNECTED, now_iso(), name))

    def mark_needs_reconnect(self, name: str, why: str) -> None:
        self.db.execute("UPDATE connections SET status = ?, last_error = ?, updated_at = ? WHERE provider = ?",
                        (STATUS_NEEDS_RECONNECT, why, now_iso(), name))
        self.audit.record("connector.needs_reconnect", f"{provider(name).label}: {why}", actor="runtime",
                          provider=name, error=why)

    def remove(self, name: str, *, via: str, revoked: str) -> bool:
        had = self.get(name) is not None
        try:
            self.vault.delete(grant_secret(name))
            had = True
        except SecretNotFound:
            pass
        self.db.execute("DELETE FROM connections WHERE provider = ?", (name,))
        if had:
            self.audit.record("connector.disconnected", f"{provider(name).label} disconnected", actor="user",
                              provider=name, via=via, at_provider=revoked)
        return had


def _provider_message(r: httpx.Response) -> str:
    """The provider's own error text, kept short. Never includes request headers (where the token is)."""
    try:
        body = r.json()
    except ValueError:
        return r.text[:300].strip()
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err.get("status") or err)[:300]
    if isinstance(body, dict) and (body.get("error_description") or err):
        return str(body.get("error_description") or err)[:300]
    if isinstance(body, dict) and body.get("message"):
        return str(body["message"])[:300]
    return json.dumps(body)[:300]


def _retry_after(r: httpx.Response, attempt: int) -> float:
    value = r.headers.get("retry-after", "")
    try:
        return max(0.0, float(value))
    except ValueError:
        return float(2 ** attempt)


class Connectors:
    """Authorised requests for connector tools. Tokens stay inside; their values are redacted everywhere."""

    def __init__(self, store: ConnectionStore, http: httpx.AsyncClient, redactions: dict[str, str]):
        self.store = store
        self.http = http
        # The gate's always-redact map (shared, by reference): every token value seen is added to it.
        self.redactions = redactions
        self._locks: dict[str, asyncio.Lock] = {}

    def connected(self, name: str) -> bool:
        row = self.store.get(name)
        return bool(row and row["status"] == STATUS_CONNECTED)

    def has_any_scope(self, name: str, scopes: frozenset[str]) -> bool:
        row = self.store.get(name)
        return bool(row and row["status"] == STATUS_CONNECTED and scopes & set(row["scopes"]))

    def details(self, name: str) -> dict[str, Any]:
        """The connection's non-secret details (``Grant.extra``), without handing its tokens to tool code."""
        return dict(self.store.grant(name).extra)

    def require_scope(self, name: str, scopes: frozenset[str], what: str) -> None:
        row = self.store.get(name)
        label = provider(name).label
        if row is None:
            raise ConnectorNotConnected(f"{label} is not connected. Connect it with 'jig connect {name}'.")
        if row["status"] != STATUS_CONNECTED:
            raise ConnectorAuthError(f"{label} needs reconnecting ({row['last_error']}). Run 'jig connect {name}'.")
        if not scopes & set(row["scopes"]):
            raise ConnectorNotConnected(
                f"{label} is connected with '{row['access']}' access, which can't {what}. To allow it, reconnect "
                f"with a higher access level: 'jig connect {name} --access <level>' (see docs/connectors-setup.md).")

    def _remember(self, name: str, grant: Grant) -> None:
        # Every value ever used is kept: an old token in a late error must still be redacted.
        for value in grant.values():
            self.redactions[f"{SECRET_PREFIX}{name}.token-{hashlib.sha256(value.encode()).hexdigest()[:8]}"] = value

    async def _current(self, name: str, *, force_refresh: bool = False) -> Grant:
        spec = provider(name)
        async with self._locks.setdefault(name, asyncio.Lock()):
            grant = self.store.grant(name)
            self._remember(name, grant)
            if self.store.has_client(spec.family) and (secret := self.store.client(spec.family).get("client_secret")):
                self.redactions[f"{client_secret_name(spec.family)}.secret"] = secret
            if (force_refresh or grant.expiring()) and spec.refresh and grant.refresh_token:
                try:
                    grant = await spec.refresh(self.http, self.store.vault, grant)
                except ConnectorAuthError as exc:
                    self.store.mark_needs_reconnect(name, str(exc))
                    raise
                self._remember(name, grant)
                self.store.update_grant(name, grant)
                self.store.audit.record("connector.refreshed", f"{spec.label} access renewed", actor="runtime",
                                        provider=name)
            elif grant.expiring() and not grant.refresh_token:
                why = "the access has expired and there is no refresh token"
                self.store.mark_needs_reconnect(name, why)
                raise ConnectorAuthError(f"{spec.label}: {why}. Reconnect with 'jig connect {name}'.")
            return grant

    async def request(self, name: str, method: str, url: str, *, params: dict[str, Any] | None = None,
                      json_body: Any = None, content: bytes | None = None,
                      headers: dict[str, str] | None = None, timeout: float = 30.0) -> httpx.Response:
        """One authorised request. 401 refreshes once; 429 (and, for reads, 5xx) waits as the provider asks,
        within limits. Any other failure raises ConnectorError with the provider's own message."""
        spec = provider(name)
        parsed = httpx.URL(url)
        host = parsed.host
        grant = await self._current(name)
        allowed = spec.hosts(grant)
        if parsed.scheme != "https" or host not in allowed:
            raise ConnectorError(f"{spec.label} requests may only go to https://{sorted(allowed)}, not {url[:80]!r}")
        refreshed = False
        waited = 0.0
        attempt = 0
        while True:
            h = {**(headers or {}), "Authorization": f"{grant.token_type} {grant.access_token}"}
            try:
                r = await self.http.request(method, url, params=params, json=json_body, content=content, headers=h,
                                            timeout=timeout)
            except httpx.HTTPError as exc:
                raise ConnectorError(f"{spec.label}: could not reach {host}: {type(exc).__name__}: {exc}") from None
            if r.status_code == 401 and not refreshed and spec.refresh and grant.refresh_token:
                refreshed = True
                grant = await self._current(name, force_refresh=True)
                continue
            retryable = r.status_code == 429 or (method.upper() in IDEMPOTENT and r.status_code in (500, 502, 503, 504))
            if retryable and attempt < MAX_RETRIES:
                delay = _retry_after(r, attempt)
                if waited + delay <= MAX_TOTAL_WAIT_S:
                    self.store.audit.record("connector.backoff", f"{spec.label} asked Jig to wait {delay:.0f}s "
                                            f"(HTTP {r.status_code})", actor="runtime", provider=name,
                                            status=r.status_code, delay_s=delay)
                    await asyncio.sleep(delay)
                    waited += delay
                    attempt += 1
                    continue
            if r.status_code == 401:
                why = f"{spec.label} rejected Jig's access (HTTP 401: {_provider_message(r)})"
                self.store.mark_needs_reconnect(name, why)
                raise ConnectorAuthError(f"{why}. Reconnect with 'jig connect {name}'.")
            if r.status_code == 403:
                raise ConnectorError(f"{spec.label} refused this (HTTP 403): {_provider_message(r)}", status=403)
            if r.status_code == 429 or (retryable and r.status_code >= 500):
                raise ConnectorError(f"{spec.label} is rate-limiting or unavailable (HTTP {r.status_code}) after "
                                     f"waiting {waited:.0f}s; try again later. {_provider_message(r)}",
                                     status=r.status_code)
            if r.status_code >= 400:
                raise ConnectorError(f"{spec.label} returned HTTP {r.status_code}: {_provider_message(r)}",
                                     status=r.status_code)
            return r

    async def disconnect(self, name: str, *, via: str) -> dict[str, Any]:
        """Revoke at the provider where it supports that, then delete the tokens. A failed revocation is
        reported, and the local tokens are still deleted (the user asked for Jig to stop using them)."""
        spec = provider(name)
        try:
            grant = self.store.grant(name)
        except ConnectorNotConnected:
            grant = None
        if grant is None:
            removed = self.store.remove(name, via=via, revoked="nothing to revoke")
            return {"provider": name, "removed": removed, "at_provider": "nothing was stored", "manage_url": spec.manage_url}
        self._remember(name, grant)
        if spec.revoke:
            try:
                at_provider = await spec.revoke(self.http, self.store.vault, grant)
            except ConnectorError as exc:
                at_provider = f"revoking at {spec.label} FAILED ({exc}); remove the access yourself at {spec.manage_url}"
        else:
            at_provider = f"{spec.label} has no way for Jig to revoke it; remove the access at {spec.manage_url}"
        self.store.remove(name, via=via, revoked=at_provider)
        return {"provider": name, "removed": True, "at_provider": at_provider, "manage_url": spec.manage_url}
