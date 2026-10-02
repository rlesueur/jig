"""Bare web addresses: ``www.example.com`` or ``example.com/path`` is read as ``https://``.

Models and benchmark tasks often write a web address without its scheme. The gate completes such an
address to ``https://`` *before* any check runs, so core rules (``no-local-network``), custom rules, the
Sentinel, approvals, the tool itself and egress all see the same, complete URL. Only values that are
unambiguously a host name are completed: a domain in public syntax (two or more labels, an alphabetic or
punycode top-level label) or an IP literal, optionally with a port, path, query or fragment. Anything with
a scheme (``http:``, ``mailto:``, ``file:``, ``javascript:`` ...), user information, whitespace, a single
label such as ``localhost``, or a leading ``/`` is left exactly as written. Nothing is ever turned into
``http://``.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any
from urllib.parse import urlsplit

URL_ARG_KEYS = frozenset({"url", "uri", "endpoint"})

_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_HOST_PORT = re.compile(r"^[^:/?#@\[\]]+:\d{1,5}(?:[/?#]|$)")
_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_TLD = re.compile(r"^(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})$")


def _is_domain(host: str) -> bool:
    if len(host) > 253:
        return False
    labels = host.split(".")
    return len(labels) >= 2 and all(_LABEL.match(x) for x in labels) and bool(_TLD.match(labels[-1]))


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def complete_bare_web_address(value: str) -> str | None:
    """``https://`` + value if value is unambiguously a bare web address, otherwise None."""
    if not value or any(c.isspace() for c in value) or value.startswith(("/", "\\")) or "\\" in value:
        return None
    if _SCHEME.match(value) and not _HOST_PORT.match(value):
        return None  # it has a scheme of its own; never rewritten
    authority = re.split(r"[/?#]", value, maxsplit=1)[0]
    if not authority or "@" in authority:
        return None
    candidate = "https://" + value
    try:
        parts = urlsplit(candidate)
        host = parts.hostname
        _ = parts.port  # raises ValueError for an invalid port
    except ValueError:
        return None
    if not host:
        return None
    if authority.startswith("["):
        return candidate if _is_ip_literal(host) and ":" in host else None
    return candidate if _is_ip_literal(host) or _is_domain(host) else None


def complete_url_args(args: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Complete bare web addresses in the URL arguments of a call. Returns the (possibly new) arguments and
    one record per completed value: ``{"arg", "original", "normalised"}``."""
    changes: list[dict[str, str]] = []
    out = dict(args)
    for key in sorted(URL_ARG_KEYS & args.keys()):
        value = args[key]
        if isinstance(value, str) and (full := complete_bare_web_address(value)):
            out[key] = full
            changes.append({"arg": key, "original": value, "normalised": full})
    return (out, changes) if changes else (args, [])
