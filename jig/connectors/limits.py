"""``[connectors.<id>]`` limits shared by the connectors: the only places Jig may change, the only people it
may send to, and a marker everything it writes must start with. The gate checks them before review (each
tool's ``precheck``), and each tool checks them again just before it acts."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def limits_for(config: Any, name: str):
    return config.connectors.get(name) if config is not None else None


def target_problem(config: Any, name: str, candidates: Iterable[str | None], what: str) -> str | None:
    """None when no allow-list is set or one of the names/ids of the target is on it."""
    limits = limits_for(config, name)
    if limits is None or not limits.allowed_targets:
        return None
    names = [c for c in candidates if c]
    if any(c in limits.allowed_targets for c in names):
        return None
    shown = names[0] if names else "(unknown)"
    return f"[connectors.{name}] allowed_targets does not include the {what} {shown!r}; Jig may only change " \
           f"{limits.allowed_targets}"


def recipient_problem(config: Any, name: str, recipients: Iterable[str]) -> str | None:
    limits = limits_for(config, name)
    if limits is None or not limits.allowed_recipients:
        return None
    outside = [r for r in recipients if isinstance(r, str) and r.strip().lower() not in limits.allowed_recipients]
    if outside:
        return (f"[connectors.{name}] allowed_recipients does not include {outside}; Jig may only send to "
                f"{limits.allowed_recipients}")
    return None


def prefix_problem(config: Any, name: str, texts: Iterable[str | None], what: str) -> str | None:
    """Every given text (ignoring ones that are None) must start with the required prefix."""
    limits = limits_for(config, name)
    if limits is None or not limits.required_prefix:
        return None
    for text in texts:
        if text is not None and not str(text).lstrip().startswith(limits.required_prefix):
            return (f"[connectors.{name}] required_prefix: the {what} must start with {limits.required_prefix!r}, "
                    f"and {str(text)[:80]!r} doesn't")
    return None


def first_problem(*problems: str | None) -> str | None:
    return next((p for p in problems if p), None)
