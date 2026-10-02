"""Google OAuth for the Google connectors (Gmail now): the installed-app flow with PKCE and a loopback
redirect, as documented at https://developers.google.com/identity/protocols/oauth2/native-app.

The user's own Google Cloud project supplies a "Desktop app" OAuth client (client ID and client secret,
which Google says installed apps cannot keep confidential; Jig still keeps it in the vault).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx

from ..errors import ConnectorAuthError, ConnectorError, SecretNotFound
from ..vault import Vault
from .base import Grant, client_secret_name

FAMILY = "google"
AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
MANAGE_URL = "https://myaccount.google.com/connections"
RECONNECT_HINT = ("If your Google app's publishing status is still 'Testing', Google ends Jig's access after 7 days: "
                  "set it to 'In production' (docs/connectors-setup.md), then reconnect with 'jig connect {name}'.")


def client_from_json(path: Path) -> dict[str, str]:
    """Read the client ID and secret from the JSON file the Google Cloud console downloads."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ConnectorError(f"could not read the Google client file {path}: {exc}") from None
    return client_from_text(text, source=str(path))


def client_from_text(text: str, *, source: str = "that file") -> dict[str, str]:
    """The client ID and secret from the text of the downloaded client file."""
    try:
        data = json.loads(text.lstrip("\ufeff"))
    except ValueError:
        raise ConnectorError(f"{source} isn't the client file Google downloads (it isn't JSON). Download it again "
                             "from the Clients page (docs/connectors-setup.md).") from None
    section = data.get("installed") if isinstance(data, dict) else None
    if not isinstance(section, dict):
        kind = next(iter(data), "nothing") if isinstance(data, dict) else "not an object"
        raise ConnectorError(f"{source} is not a Desktop app client (it has {kind!r}, not 'installed'). Create a "
                             "client of type 'Desktop app' (docs/connectors-setup.md).")
    client = {"client_id": str(section.get("client_id", "")), "client_secret": str(section.get("client_secret", ""))}
    check_client(client)
    return client


def check_client(client: dict[str, str]) -> None:
    if not client.get("client_id", "").endswith(".apps.googleusercontent.com"):
        raise ConnectorError("that doesn't look like a Google OAuth client ID (it ends in .apps.googleusercontent.com)")
    if not client.get("client_secret") or any(c.isspace() for c in client["client_secret"]):
        raise ConnectorError("the Google client secret is empty or contains spaces")


def _grant_from(body: dict[str, Any], previous: Grant | None = None) -> Grant:
    scopes = body.get("scope")
    return Grant(
        access_token=body["access_token"],
        refresh_token=body.get("refresh_token") or (previous.refresh_token if previous else None),
        expires_at=time.time() + float(body.get("expires_in", 3600)),
        scopes=scopes.split() if isinstance(scopes, str) else (previous.scopes if previous else []),
        token_type=body.get("token_type") or "Bearer",
    )


def _token_error(r: httpx.Response) -> str:
    try:
        body = r.json()
        return f"{body.get('error')}: {body.get('error_description', '')}".strip(": ")
    except ValueError:
        return r.text[:200]


async def exchange_code(http: httpx.AsyncClient, client: dict[str, str], *, code: str, redirect_uri: str,
                        verifier: str) -> Grant:
    try:
        r = await http.post(TOKEN_URL, data={
            "code": code, "client_id": client["client_id"], "client_secret": client["client_secret"],
            "redirect_uri": redirect_uri, "grant_type": "authorization_code", "code_verifier": verifier,
        }, timeout=30)
    except httpx.HTTPError as exc:
        raise ConnectorError(f"could not reach Google's token endpoint: {type(exc).__name__}: {exc}") from None
    if r.status_code != 200:
        raise ConnectorError(f"Google refused the sign-in (HTTP {r.status_code}, {_token_error(r)}); nothing was "
                             "connected")
    grant = _grant_from(r.json())
    if not grant.refresh_token:
        raise ConnectorError("Google returned no refresh token, so Jig would lose access within the hour; nothing "
                             "was connected. Remove Jig at https://myaccount.google.com/connections and connect again.")
    return grant


def _client(vault: Vault) -> dict[str, str]:
    try:
        return json.loads(vault.reveal(client_secret_name(FAMILY)))
    except SecretNotFound:
        raise ConnectorAuthError("the Google app client is no longer in the vault, so Jig can't renew access; "
                                 "connect again with 'jig connect <provider> --client-json <file>'") from None


def make_refresh(name: str):
    async def refresh(http: httpx.AsyncClient, vault: Vault, grant: Grant) -> Grant:
        client = _client(vault)
        try:
            r = await http.post(TOKEN_URL, data={
                "client_id": client["client_id"], "client_secret": client["client_secret"],
                "refresh_token": grant.refresh_token, "grant_type": "refresh_token",
            }, timeout=30)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"could not reach Google to renew access: {type(exc).__name__}: {exc}") from None
        if r.status_code in (400, 401):
            raise ConnectorAuthError(f"Google no longer accepts Jig's access ({_token_error(r)}). "
                                     + RECONNECT_HINT.format(name=name))
        if r.status_code != 200:
            raise ConnectorError(f"Google's token endpoint returned HTTP {r.status_code}: {_token_error(r)}")
        return _grant_from(r.json(), grant)
    return refresh


async def revoke(http: httpx.AsyncClient, vault: Vault, grant: Grant) -> str:
    token = grant.refresh_token or grant.access_token
    try:
        r = await http.post(REVOKE_URL, data={"token": token}, timeout=30)
    except httpx.HTTPError as exc:
        raise ConnectorError(f"could not reach Google: {type(exc).__name__}") from None
    if r.status_code == 200:
        return ("revoked at Google. Google removes the app's access for your account, so any other Google "
                "connector using the same app client needs connecting again")
    if r.status_code == 400 and "invalid_token" in r.text:
        return "Google says it was already revoked or expired"
    raise ConnectorError(f"Google's revoke endpoint returned HTTP {r.status_code}: {_token_error(r)}")


def authorise_params() -> dict[str, str]:
    # offline: a refresh token; consent: Google issues a fresh one even if the app was allowed before.
    return {"access_type": "offline", "prompt": "consent", "include_granted_scopes": "false"}


async def sign_in(http: httpx.AsyncClient, store: Any, level: Any, open_browser, ready, *, api: str,
                  probe_url: str, account_of) -> tuple[Grant, str]:
    """The browser sign-in for one Google connector, then one real call to its API to find the account
    (and to prove the API is enabled in the user's project)."""
    from . import oauth

    client = store.client(FAMILY)
    result = await oauth.authorise(authorize_url=AUTHORIZE_URL, client_id=client["client_id"],
                                   scopes=list(level.scopes), extra=authorise_params(),
                                   open_browser=open_browser, label="Google", ready=ready)
    grant = await exchange_code(http, client, code=result.code, redirect_uri=result.redirect_uri,
                                verifier=result.verifier)
    try:
        r = await http.get(probe_url, headers={"Authorization": f"Bearer {grant.access_token}"}, timeout=30)
    except httpx.HTTPError as exc:
        r = None
        problem = f"could not reach the {api}: {type(exc).__name__}"
    else:
        problem = (f"signed in, but the {api} refused (HTTP {r.status_code}): {r.text[:300]}. Is the {api} "
                   "enabled in your Google Cloud project?") if r.status_code != 200 else ""
    if problem:
        try:
            undone = await revoke(http, None, grant)  # type: ignore[arg-type]
        except ConnectorError as exc:
            undone = f"revoking the new grant also failed ({exc}); remove it at {MANAGE_URL}"
        raise ConnectorError(f"{problem} Nothing was connected; the new grant was {undone}.")
    return grant, account_of(r.json())  # type: ignore[union-attr]
