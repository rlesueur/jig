"""Long text read a part at a time, or searched, with every cut said out loud.

A tool that returns text longer than one result can hold gives a part of it, its total length and where the
next part starts (``next_offset``), so the model can read on with ``offset``, or use ``find`` to get only the
passages that mention a word, each with its offset.
"""

from __future__ import annotations

from typing import Any

from ..errors import ToolArgumentError

FIND_CONTEXT = 300

OFFSET_ARG = "Character position in the {what}'s text to start from (from next_offset of an earlier result)."
FIND_ARG = ("Return only the passages around each place this text appears (case-insensitive), with their offsets, "
            "instead of reading from offset.")


def text_page(text: str, *, tool: str, limit: int, offset: int = 0, find: str = "",
              what: str = "page") -> dict[str, Any]:
    """Part of ``text`` from ``offset``, at most ``limit`` characters, or with ``find`` the passages that mention it.

    Always gives ``total_chars``. A part that is not the whole text has ``truncated`` true and a ``note`` saying
    what is missing and, unless it reaches the end, the ``next_offset`` to read on from."""
    if offset < 0:
        raise ToolArgumentError(f"{tool}: offset must be 0 or more")
    if limit < 1:
        raise ToolArgumentError(f"{tool}: the limit must be at least 1 character")
    if find.strip():
        return {"total_chars": len(text), **find_passages(text, find.strip(), limit, what=what)}
    if offset and offset >= len(text):
        raise ToolArgumentError(f"{tool}: offset {offset} is past the end of the {what}'s text "
                                f"({len(text)} characters)")
    end = min(offset + limit, len(text))
    part: dict[str, Any] = {"total_chars": len(text), "text": text[offset:end], "offset": offset,
                            "truncated": end < len(text) or offset > 0}
    if part["truncated"]:
        part["note"] = (f"This is characters {offset} to {end} of {len(text)}; the rest of the {what} is not "
                        "shown here. "
                        + (f"Call {tool} again with offset={end} to read on, " if end < len(text) else "")
                        + "or use find to get the passages that mention what you need. Do not guess what the "
                        "rest says.")
        if end < len(text):
            part["next_offset"] = end
    return part


def find_passages(text: str, needle: str, limit: int, *, what: str = "page") -> dict[str, Any]:
    """The passages around each match of ``needle`` (case-insensitive), merged where they overlap, up to ``limit``
    characters in all. Each passage says where it starts, so the model can read on from there with offset."""
    lower, target = text.lower(), needle.lower()
    spans: list[list[int]] = []
    matches = 0
    start = lower.find(target)
    while start >= 0:
        matches += 1
        lo, hi = max(0, start - FIND_CONTEXT), min(len(text), start + len(target) + FIND_CONTEXT)
        if spans and lo <= spans[-1][1]:
            spans[-1][1] = hi
        else:
            spans.append([lo, hi])
        start = lower.find(target, start + len(target))
    passages, used, resume_at = [], 0, None
    for lo, hi in spans:
        room = limit - used
        if room <= 0:
            resume_at = lo
            break
        passages.append({"offset": lo, "text": text[lo:min(hi, lo + room)]})
        used += min(hi, lo + room) - lo
        if hi > lo + room:
            resume_at = lo + room
            break
    out: dict[str, Any] = {"find": needle, "matches": matches, "passages": passages}
    if resume_at is not None:
        out["note"] = (f"Not every passage fits in one result: this stops at character {resume_at} of {len(text)}. "
                       f"Read on with offset={resume_at}, or search for something more specific.")
    elif not matches:
        out["note"] = f"{needle!r} does not appear in the {what}'s text ({len(text)} characters)."
    return out
