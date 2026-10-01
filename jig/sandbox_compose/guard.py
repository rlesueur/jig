"""Refuse API requests that come from the sandbox network.

In container mode Jig listens on 0.0.0.0 and is also on the sandbox network, so code in the sandbox
could open a connection to Jig's API. Authentication would still stop it, but the core rule says
outbound tools may never reach Jig's own API at all, so this ASGI middleware refuses every HTTP request
and WebSocket handshake whose peer is on a sandbox network, before authentication, and audits it.
Until the compose backend has found those networks, it refuses everything (fail closed).
"""

from __future__ import annotations

import ipaddress
import logging
from typing import Any

log = logging.getLogger(__name__)


class SandboxPeerGuard:
    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        jig = getattr(scope["app"].state, "jig", None)
        backend = getattr(jig, "container", None)
        nets = getattr(backend, "untrusted_networks", None) if getattr(backend, "started", False) else None
        client = (scope.get("client") or ("", 0))[0]
        reason = None
        if not nets:
            reason = "the sandbox networks are not known yet"
        else:
            try:
                ip = ipaddress.ip_address(client)
            except ValueError:
                reason = f"unparseable peer address {client!r}"
            else:
                if any(ip in net for net in nets):
                    reason = f"{client} is on the sandbox network"
        if reason is None:
            await self.app(scope, receive, send)
            return
        log.warning("refused %s %s from %s: %s", scope["type"], scope.get("path"), client, reason)
        if jig is not None:
            jig.audit.record("api.sandbox_peer_refused", f"refused {scope.get('path')} from {client}",
                             actor="sandbox-guard", client=client, path=scope.get("path"), reason=reason)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = b'{"detail": "requests from the sandbox network are refused"}'
        await send({"type": "http.response.start", "status": 403,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
