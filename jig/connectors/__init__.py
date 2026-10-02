"""Connectors: Jig working with the user's own accounts. See ``base`` for the framework and
docs/connectors-setup.md for setting each one up."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from ..errors import ConnectorError
from . import discord, github, gmail, google_calendar, google_drive, matrix, microsoft, signal, slack  # noqa: F401  (each registers its provider)
from .base import PROVIDERS, ConnectionStore, Connectors, SECRET_PREFIX, provider

__all__ = ["PROVIDERS", "ConnectionStore", "Connectors", "SECRET_PREFIX", "connect", "provider", "register_tools"]


def register_tools(registry: Any, connectors: Connectors) -> None:
    gmail.register_gmail_tools(registry, connectors)
    google_calendar.register_calendar_tools(registry, connectors)
    google_drive.register_drive_tools(registry, connectors)
    microsoft.register_microsoft_tools(registry, connectors)
    github.register_github_tools(registry, connectors)
    slack.register_slack_tools(registry, connectors)
    discord.register_discord_tools(registry, connectors)
    matrix.register_matrix_tools(registry, connectors)
    signal.register_signal_tools(registry, connectors)


async def connect(name: str, *, access: str | None, store: ConnectionStore, http: httpx.AsyncClient,
                  open_browser: Callable[[str], object] | None = None, ready: Callable[[str], object] | None = None,
                  values: dict[str, str] | None = None, via: str) -> dict[str, Any]:
    """Run the provider's sign-in (or check the token typed in for it), check what was granted and store it.
    Returns the connection and any requested scopes the user did not grant (the tools that need them then
    say so)."""
    spec = provider(name)
    level_name = access or spec.default_access
    if level_name not in spec.access_levels:
        raise ConnectorError(f"{spec.label} access must be one of {list(spec.access_levels)}, not {level_name!r}")
    level = spec.access_levels[level_name]
    if spec.connect is None:
        raise ConnectorError(f"{spec.label} has no sign-in flow")
    if spec.kind == "token":
        missing_inputs = [i.name for i in spec.inputs if not (values or {}).get(i.name)]
        if missing_inputs:
            raise ConnectorError(f"{spec.label}: {', '.join(missing_inputs)} must be given; nothing was connected")
        grant, account = await spec.connect(http, store, level, dict(values or {}))
    else:
        if open_browser is None:
            raise ConnectorError(f"{spec.label} signs in through the browser; nothing was connected")
        grant, account = await spec.connect(http, store, level, open_browser, ready)
    missing = [s for s in level.scopes if s not in grant.scopes]
    if len(missing) == len(level.scopes):
        raise ConnectorError(f"{spec.label}: none of the requested permissions were granted, so nothing was "
                             "connected. Tick them on the consent page and try again.")
    row = store.save(name, grant, account=account, access=level_name, via=via)
    return {"connection": row, "not_granted": missing}
