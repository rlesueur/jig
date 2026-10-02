"""Connectors: Jig working with the user's own accounts. See ``base`` for the framework and
docs/connectors-setup.md for setting each one up."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from ..errors import ConnectorError
from . import gmail  # noqa: F401  (registers the provider)
from .base import PROVIDERS, ConnectionStore, Connectors, SECRET_PREFIX, provider

__all__ = ["PROVIDERS", "ConnectionStore", "Connectors", "SECRET_PREFIX", "connect", "provider", "register_tools"]


def register_tools(registry: Any, connectors: Connectors) -> None:
    gmail.register_gmail_tools(registry, connectors)


async def connect(name: str, *, access: str | None, store: ConnectionStore, http: httpx.AsyncClient,
                  open_browser: Callable[[str], object], ready: Callable[[str], object] | None = None,
                  via: str) -> dict[str, Any]:
    """Run the provider's sign-in, check what was granted and store it. Returns the connection and any
    requested scopes the user did not grant (the tools that need them then say so)."""
    spec = provider(name)
    level_name = access or spec.default_access
    if level_name not in spec.access_levels:
        raise ConnectorError(f"{spec.label} access must be one of {list(spec.access_levels)}, not {level_name!r}")
    level = spec.access_levels[level_name]
    if spec.connect is None:
        raise ConnectorError(f"{spec.label} has no sign-in flow")
    grant, account = await spec.connect(http, store, level, open_browser, ready)
    missing = [s for s in level.scopes if s not in grant.scopes]
    if len(missing) == len(level.scopes):
        raise ConnectorError(f"{spec.label}: none of the requested permissions were granted, so nothing was "
                             "connected. Tick them on the consent page and try again.")
    row = store.save(name, grant, account=account, access=level_name, via=via)
    return {"connection": row, "not_granted": missing}
