"""The egress proxy: the only way out of the sandbox container.

The sandbox container sits on an internal Docker network with no route out
and no external DNS. Its one neighbour is a relay container that forwards
every TCP connection to this proxy, which runs inside the Jig runtime on the
host. For each connection (HTTP ``CONNECT`` for HTTPS, absolute-URI requests
for plain HTTP) the proxy:

1. refuses it unless a reviewed sandbox action is running (a *lease*). Leases
   are opened only by container tools after the gate (core rules, custom
   rules, the Sentinel and any approval) has passed the call;
2. applies the core rule ``no-local-network``: it resolves the host itself and
   refuses loopback, private, link-local and reserved addresses, then connects
   to the address it checked, so DNS rebinding cannot swap it afterwards;
3. allows only the configured ports (80 and 443 by default);
4. applies custom rules for the pseudo-tool ``egress`` (arguments ``host``
   and ``url``): ``block`` refuses; ``ask`` also refuses, because a connection
   cannot wait for a human.

Every decision is written to the audit log (``egress.allow`` / ``egress.block``).
"""

from __future__ import annotations

import asyncio
import ipaddress
import itertools
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from ..constants import Decision

_HEAD_LIMIT = 32_768
_HOP_HEADERS = {"proxy-connection", "proxy-authorization", "connection", "keep-alive", "te", "trailer", "upgrade"}


@dataclass
class Lease:
    id: int
    tool: str
    run_id: str | None
    task_id: str | None


class EgressBlocked(Exception):
    pass


async def resolve_public(host: str, port: int) -> str:
    """Resolve ``host`` and return one address, refusing anything that is not on the public internet."""
    name = host.strip("[]").lower()
    if name in {"localhost", "localhost.localdomain"} or name.endswith(".localhost"):
        raise EgressBlocked(f"core rule no-local-network: host {host!r} is local")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(name, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise EgressBlocked(f"could not resolve host {host!r}: {exc}") from exc
    addresses = [info[4][0].split("%")[0] for info in infos]
    for addr in addresses:
        ip = ipaddress.ip_address(addr)
        if not ip.is_global or ip.is_multicast:
            raise EgressBlocked(f"core rule no-local-network: host {host!r} resolves to non-public address {ip}")
    return addresses[0]


class EgressProxy:
    def __init__(self, *, bind: str, ports: list[int], rules: Any, audit: Any):
        self.bind = bind
        self.ports = set(ports)
        self.rules = rules
        self.audit = audit
        self.port: int | None = None
        self._server: asyncio.Server | None = None
        self._leases: dict[int, Lease] = {}
        self._ids = itertools.count(1)
        self._conns: set[asyncio.Task[Any]] = set()

    async def start(self, port: int = 0) -> int:
        self._server = await asyncio.start_server(self._handle, self.bind, port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            for t in list(self._conns):
                t.cancel()
            await asyncio.gather(*self._conns, return_exceptions=True)
            await self._server.wait_closed()
            self._server = None

    @asynccontextmanager
    async def lease(self, *, tool: str, run_id: str | None = None, task_id: str | None = None) -> AsyncIterator[Lease]:
        """Open egress for the duration of one reviewed sandbox action."""
        lease = Lease(next(self._ids), tool, run_id, task_id)
        self._leases[lease.id] = lease
        try:
            yield lease
        finally:
            del self._leases[lease.id]

    @property
    def active_leases(self) -> list[Lease]:
        return list(self._leases.values())

    async def check(self, host: str, port: int, url: str) -> tuple[str, Lease]:
        """Return the vetted address to connect to and the lease it runs under, or raise ``EgressBlocked``."""
        if not self._leases:
            raise EgressBlocked("no reviewed sandbox action is running; egress is only open while an approved "
                                "container tool runs")
        lease = self._leases[max(self._leases)]
        address = await resolve_public(host, port)
        if port not in self.ports:
            raise EgressBlocked(f"port {port} is not allowed (allowed: {sorted(self.ports)})")
        rule = self.rules.match("egress", {"host": host.lower(), "url": url})
        if rule and rule["decision"] == Decision.BLOCK:
            raise EgressBlocked(f"custom rule {rule['id']} blocks egress to {host}")
        if rule and rule["decision"] == Decision.ASK:
            raise EgressBlocked(f"custom rule {rule['id']} asks for approval for {host}; a connection cannot wait "
                                "for a human, so it is refused")
        return address, lease

    def _audit(self, allowed: bool, host: str, port: int, reason: str, lease: Lease | None) -> None:
        self.audit.record("egress.allow" if allowed else "egress.block",
                          f"{'allowed' if allowed else 'blocked'} {host}:{port}", actor="egress-proxy",
                          task_id=lease.task_id if lease else None, run_id=lease.run_id if lease else None,
                          host=host, port=port, reason=reason, tool=lease.tool if lease else None)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task:
            self._conns.add(task)
        try:
            await self._serve(reader, writer)
        except (ConnectionError, OSError, asyncio.IncompleteReadError, asyncio.TimeoutError,
                asyncio.LimitOverrunError, ValueError):
            pass
        finally:
            writer.close()
            if task:
                self._conns.discard(task)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 15)
        if len(head) > _HEAD_LIMIT:
            raise ValueError("request head too large")
        lines = head.decode("latin-1").split("\r\n")
        method, target, version = lines[0].split(" ", 2)
        if method.upper() == "CONNECT":
            host, _, port_s = target.rpartition(":")
            port, url = int(port_s), f"https://{target}/"
        else:
            parts = urlsplit(target)
            if parts.scheme != "http" or not parts.hostname:
                await self._refuse(writer, 400, "only CONNECT and absolute http:// requests are accepted")
                return
            host, port, url = parts.hostname, parts.port or 80, target
        lease: Lease | None = None
        try:
            address, lease = await self.check(host, port, url)
        except EgressBlocked as exc:
            lease = self._leases[max(self._leases)] if self._leases else None
            self._audit(False, host, port, str(exc), lease)
            await self._refuse(writer, 403, str(exc))
            return
        try:
            up_r, up_w = await asyncio.wait_for(asyncio.open_connection(address, port), 15)
        except (OSError, asyncio.TimeoutError) as exc:
            self._audit(False, host, port, f"connection failed: {exc!r}", lease)
            await self._refuse(writer, 502, f"could not connect to {host}:{port}")
            return
        self._audit(True, host, port, f"{method} via {address}", lease)
        if method.upper() == "CONNECT":
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
        else:
            parts = urlsplit(target)
            path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
            headers = [h for h in lines[1:] if h and h.split(":", 1)[0].strip().lower() not in _HOP_HEADERS]
            up_w.write(("\r\n".join([f"{method} {path} {version}", *headers, "Connection: close"]) + "\r\n\r\n")
                       .encode("latin-1"))
            await up_w.drain()
        try:
            await asyncio.gather(_pipe(reader, up_w), _pipe(up_r, writer))
        finally:
            up_w.close()

    @staticmethod
    async def _refuse(writer: asyncio.StreamWriter, status: int, reason: str) -> None:
        body = f"Jig egress proxy: {reason}\n".encode()
        phrase = {400: "Bad Request", 403: "Forbidden", 502: "Bad Gateway"}[status]
        writer.write(f"HTTP/1.1 {status} {phrase}\r\nContent-Type: text/plain; charset=utf-8\r\n"
                     f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
        await writer.drain()


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
    except (ConnectionError, OSError):
        pass
