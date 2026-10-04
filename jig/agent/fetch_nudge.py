"""A note on a tool result when guessed web addresses on one site keep coming back as HTTP 404.

The fetch itself is unchanged: a non-200 page is still an error, and nothing is invented in its place.
This only adds a sentence the model can read, after the same site has missed often enough, so it can
stop guessing and say how to look the page up. It does not call another search engine.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

# One or two misses can be a wrong address. Three on the same site is guessing.
GUESSED_URL_NUDGE_AT = 3

_ABSENT = (
    " Search is not installed, so you cannot look the right address up. Tell the user that, and that "
    "they can install search in Settings."
)
_OFF = " Search is turned off in Settings, so you cannot look the right address up. Tell the user that."
_READY = " Use web_search to look this up."
_HONEST = " Do not invent what the pages say, and do not fetch a search engine's results page."


def http_404_host(content: str) -> str | None:
    """The site a web_fetch error says returned HTTP 404, or None when this result is not that.

    ``content`` is the tool message as the model receives it (a JSON error, optionally with a note
    already added). Only an error whose text is exactly ``"<url> returned HTTP 404"`` counts.
    """
    try:
        data = json.loads(content)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    error = data.get("error")
    if not isinstance(error, str):
        return None
    marker = " returned HTTP "
    if marker not in error:
        return None
    url, _, status = error.rpartition(marker)
    if status.strip() != "404" or not url.startswith(("http://", "https://")) or not url.strip():
        return None
    host = (urlsplit(url.strip()).hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host or None


def guessed_url_note(host: str, search: str) -> str:
    """What to tell the model. ``search`` is ``ready``, ``off`` or ``absent`` (see ``search_hint``)."""
    if search == "ready":
        how = _READY
    elif search == "off":
        how = _OFF
    else:
        how = _ABSENT
    return (
        f"[Jig] These addresses on {host} are not pages (HTTP 404). Guessing web addresses is failing."
        f"{how}{_HONEST}"
    )


def search_hint(searxng: Any) -> str:
    """Whether search can be used, without starting it or asking a search engine anything.

    ``ready``: it is installed and switched on, or something is already listening on its port.
    ``off``: it is switched off in Settings. ``absent``: it is not installed and nothing is listening.
    A listening port is not opened and not scraped; web_search still says if that port is not SearXNG.
    """
    if searxng is None:
        return "absent"
    installed = bool(searxng.managed_installed())
    enabled = bool(searxng.enabled())
    if installed and enabled:
        return "ready"
    if not enabled:
        return "off"
    port = getattr(searxng, "discover_port", None)
    if isinstance(port, int) and _port_open(port):
        return "ready"
    return "absent"


def note_guessed_pages(messages: list[dict[str, Any]], counts: dict[str, int],
                       search_of: Callable[[], str]) -> None:
    """Count HTTP 404s by site for the tool results just added, and note the latest once a site has missed enough.

    ``counts`` is this run's tally (host to how many 404s) and is updated in place. Only messages after the
    latest assistant message are considered, so a later step is not counted twice. The note is appended after
    the tool's own JSON, on the latest miss for that site in this batch. Earlier results in the batch stay
    as the tool wrote them.
    """
    last = max((i for i, message in enumerate(messages) if message.get("role") == "assistant"), default=None)
    if last is None:
        return
    latest: dict[str, dict[str, Any]] = {}
    for message in messages[last + 1:]:
        if message.get("role") != "tool" or not isinstance(message.get("content"), str):
            continue
        host = http_404_host(message["content"])
        if host is None:
            continue
        counts[host] = counts.get(host, 0) + 1
        if counts[host] >= GUESSED_URL_NUDGE_AT:
            latest[host] = message
    if not latest:
        return
    search = search_of()
    for host, message in latest.items():
        message["content"] += "\n\n" + guessed_url_note(host, search)


def _port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.4)
        try:
            sock.connect(("127.0.0.1", port))
        except OSError:
            return False
    return True
