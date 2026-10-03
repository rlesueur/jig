"""Network guard and readable-text extraction for ``web_fetch``."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from ..errors import PolicyBlocked


async def public_address_problem(url: str) -> str | None:
    """Return why ``url`` must not be fetched, or ``None`` if it targets the public internet.

    Blocks loopback, private, link-local and reserved addresses so the agent
    cannot reach the model server, Jig's own API (and so approve itself) or
    the local network.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return f"only http and https URLs are allowed, not {parts.scheme or 'none'!r}"
    host = parts.hostname
    if not host:
        return "URL has no host"
    if host.lower() in {"localhost", "localhost.localdomain"} or host.lower().endswith(".localhost"):
        return f"host {host!r} is local"
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return f"could not resolve host {host!r}: {exc}"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global or ip.is_multicast:
            return f"host {host!r} resolves to non-public address {ip}"
    return None


async def ensure_public(url: str) -> None:
    if problem := await public_address_problem(url):
        raise PolicyBlocked(f"core rule no-local-network: {problem}")


def page_status_error(url: str, status: int) -> str | None:
    """Why ``status`` is not a page of content, or ``None`` when it is HTTP 200.

    Redirects are followed before this is asked. 200 is the page. 202 is not:
    some search engines answer a blocked fetch with 202 and a challenge page,
    which is not a set of results. Other codes (403, 404, and the rest) are
    ordinary failures. The caller raises this text; nothing is invented in its place.
    """
    if status == 200:
        return None
    return f"{url} returned HTTP {status}"


_SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "head", "nav", "footer", "aside", "form"}
_BLOCK = {"p", "div", "section", "article", "main", "br", "li", "ul", "ol", "tr", "table",
          "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "header", "dd", "dt"}


class _Readable(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title = ""
        self._in_title = False
        self._skip_depth = 0
        self.chunks: list[str] = []
        self.links: list[dict[str, str]] = []
        self._link: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        if tag in _SKIP:
            self._skip_depth += 1
        if self._skip_depth:
            return
        if tag in _BLOCK:
            self.chunks.append("\n")
        if tag in {"h1", "h2", "h3"}:
            self.chunks.append("#" * int(tag[1]) + " ")
        if tag == "li":
            self.chunks.append("- ")
        if tag == "a":
            href = dict(attrs).get("href")
            if href and not href.startswith(("#", "javascript:", "mailto:")):
                self._link = {"href": urljoin(self.base_url, href), "text": ""}

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in _SKIP and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "a" and self._link is not None:
            self._link["text"] = " ".join(self._link["text"].split())
            self.links.append(self._link)
            self._link = None
        if tag in _BLOCK:
            self.chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._skip_depth:
            return
        self.chunks.append(data)
        if self._link is not None:
            self._link["text"] += data


def extract_readable(html: str, base_url: str) -> tuple[str, str, list[dict[str, str]]]:
    parser = _Readable(base_url)
    parser.feed(html)
    parser.close()
    text = "".join(parser.chunks)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    links = [link for link in parser.links if link["text"]]
    return " ".join(parser.title.split()), text, links
