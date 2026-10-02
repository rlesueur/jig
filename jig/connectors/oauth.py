"""OAuth 2.0 for installed apps: authorisation code with PKCE (RFC 7636) and a loopback redirect (RFC 8252).

Jig listens on ``127.0.0.1`` on a random port for exactly one answer, checks the ``state`` value (so a
page that guesses the port can't inject a code), and stops listening. It never listens on another
interface. The code is exchanged for tokens by the caller, with the PKCE verifier.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import html
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit

from ..errors import ConnectorError

DEFAULT_TIMEOUT_S = 300.0


def pkce_pair() -> tuple[str, str]:
    """A code verifier (64 unreserved characters, within RFC 7636's 43 to 128) and its S256 challenge."""
    verifier = secrets.token_urlsafe(48)
    return verifier, pkce_challenge(verifier)


def pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


@dataclass
class AuthorisationResult:
    code: str
    redirect_uri: str
    verifier: str


_PAGE = """<!doctype html><html lang="en-GB"><meta charset="utf-8"><title>Jig</title>
<body style="font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;line-height:1.5">
<h1>{title}</h1><p>{text}</p></body></html>"""


class LoopbackReceiver:
    """A one-shot HTTP listener on 127.0.0.1 for the OAuth redirect."""

    def __init__(self, *, redirect_host: str = "127.0.0.1", path: str = "/", label: str = "the provider"):
        # redirect_host is what goes in redirect_uri: Google documents 127.0.0.1; Microsoft registers
        # "localhost". Either way Jig binds 127.0.0.1 only.
        self.redirect_host = redirect_host
        self.path = path
        self.label = label
        self.state = secrets.token_urlsafe(32)
        self._server: asyncio.base_events.Server | None = None
        self._result: asyncio.Future[str] | None = None
        self.port = 0

    @property
    def redirect_uri(self) -> str:
        return f"http://{self.redirect_host}:{self.port}{self.path}"

    async def start(self) -> None:
        self._result = asyncio.get_running_loop().create_future()
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def wait(self, timeout: float = DEFAULT_TIMEOUT_S) -> str:
        assert self._result is not None
        try:
            return await asyncio.wait_for(asyncio.shield(self._result), timeout)
        except TimeoutError:
            raise ConnectorError(f"no answer from {self.label} within {timeout:.0f}s; nothing was connected. "
                                 "Run the command again and finish signing in in the browser.") from None
        finally:
            await self.close()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), 10)
            while (await asyncio.wait_for(reader.readline(), 10)) not in (b"\r\n", b"\n", b""):
                pass
            parts = line.decode("latin-1").split()
            if len(parts) < 2 or parts[0] != "GET":
                await self._reply(writer, 405, "Not allowed", "Only the sign-in redirect is accepted here.")
                return
            target = urlsplit(parts[1])
            if target.path != self.path:
                await self._reply(writer, 404, "Not found", "This address only accepts the sign-in redirect.")
                return
            query = {k: v[0] for k, v in parse_qs(target.query).items()}
            if self._result is None or self._result.done():
                await self._reply(writer, 409, "Already finished", "This sign-in has already been handled.")
                return
            if not hmac.compare_digest(query.get("state", ""), self.state):
                # Not ours (a guess at the port, or an old tab). Keep waiting for the real answer.
                await self._reply(writer, 400, "Not recognised", "This answer doesn't match the sign-in Jig started.")
                return
            if "error" in query:
                why = query.get("error_description") or query["error"]
                self._result.set_exception(ConnectorError(f"{self.label} did not grant access: {why}"))
                await self._reply(writer, 200, "Not connected", f"{self.label} said: {why}. You can close this tab.")
                return
            code = query.get("code")
            if not code:
                await self._reply(writer, 400, "No code", "The answer had no authorisation code.")
                return
            self._result.set_result(code)
            await self._reply(writer, 200, "Jig is connected",
                              "You can close this tab and go back to Jig.")
        except (TimeoutError, ConnectionError, UnicodeDecodeError, ValueError, asyncio.LimitOverrunError,
                asyncio.IncompleteReadError):
            pass  # a malformed or abandoned request; keep waiting for the real redirect
        finally:
            writer.close()

    @staticmethod
    async def _reply(writer: asyncio.StreamWriter, status: int, title: str, text: str) -> None:
        body = _PAGE.format(title=html.escape(title), text=html.escape(text)).encode("utf-8")
        head = (f"HTTP/1.1 {status} {title}\r\nContent-Type: text/html; charset=utf-8\r\n"
                f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\nReferrer-Policy: no-referrer\r\n"
                "Connection: close\r\n\r\n").encode("latin-1")
        writer.write(head + body)
        await writer.drain()


async def authorise(*, authorize_url: str, client_id: str, scopes: list[str], extra: dict[str, str],
                    open_browser: Callable[[str], object], label: str, redirect_host: str = "127.0.0.1",
                    timeout: float = DEFAULT_TIMEOUT_S, ready: Callable[[str], object] | None = None
                    ) -> AuthorisationResult:
    """Run the browser part of the flow and return the code. ``ready`` gets the URL before the browser opens
    (the API returns it to the web UI)."""
    receiver = LoopbackReceiver(redirect_host=redirect_host, label=label)
    await receiver.start()
    verifier, challenge = pkce_pair()
    url = authorize_url + "?" + urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": receiver.redirect_uri,
        "scope": " ".join(scopes), "state": receiver.state, "code_challenge": challenge,
        "code_challenge_method": "S256", **extra,
    })
    try:
        if ready:
            ready(url)
        open_browser(url)
    except BaseException:
        await receiver.close()
        raise
    code = await receiver.wait(timeout)
    return AuthorisationResult(code=code, redirect_uri=receiver.redirect_uri, verifier=verifier)
