"""Starter tools. Each declares its effect so modes and review are enforced by the gate."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urljoin
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from ..constants import Effect, Mode, TaskVariant, ToolCategory
from ..errors import ToolArgumentError, ToolError
from ..recurrence import Recurrence
from .registry import ToolContext, ToolRegistry
from .web import ensure_public, extract_readable


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    tool = registry.tool

    @tool(
        description="Fetch a public web page over HTTP(S) and return its readable text, title and links. "
        "A long page comes back one part at a time: the result then says how long the whole text is and where the "
        "next part starts, so read on with offset, or use find to get just the passages that mention a word. "
        "Header values may reference vault secrets as {{secret:NAME}}.",
        effect=Effect.READ,
        category=ToolCategory.WEB,
        outbound=True,
        args={
            "url": "Absolute http(s) URL of a public page.",
            "headers": "Optional request headers.",
            "max_chars": "Maximum characters of text to return; 0 for the most allowed. Never more than the "
                         "configured limit.",
            "offset": "Character position in the page's text to start from (from next_offset of an earlier result).",
            "find": "Return only the passages around each place this text appears (case-insensitive), with "
                    "their offsets, instead of reading from offset.",
        },
    )
    async def web_fetch(ctx: ToolContext, url: str, headers: dict | None = None, max_chars: int = 0,
                        offset: int = 0, find: str = "") -> dict[str, Any]:
        cfg = ctx.config.web_fetch
        limit = min(max_chars or cfg.max_chars, cfg.max_chars)
        if offset < 0:
            raise ToolArgumentError("web_fetch: offset must be 0 or more")
        current = url
        for _hop in range(cfg.max_redirects + 1):
            await ensure_public(current)
            body = bytearray()
            async with ctx.http.stream(
                "GET", current, headers=headers or {}, timeout=cfg.timeout_s, follow_redirects=False
            ) as r:
                if r.is_redirect:
                    location = r.headers.get("location")
                    if not location:
                        raise ToolError(f"redirect from {current} has no Location header")
                    current = urljoin(current, location)
                    continue
                async for chunk in r.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > cfg.max_bytes:
                        raise ToolError(f"response from {current} exceeds {cfg.max_bytes} bytes")
                status = r.status_code
                ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                encoding = r.encoding or "utf-8"
            break
        else:
            raise ToolError(f"too many redirects (>{cfg.max_redirects}) starting from {url}")
        if status >= 400:
            raise ToolError(f"{current} returned HTTP {status}")
        text_raw = body.decode(encoding, errors="replace")
        if ctype in ("text/html", "application/xhtml+xml") or (not ctype and "<html" in text_raw[:2000].lower()):
            title, text, links = extract_readable(text_raw, current)
        elif ctype.startswith("text/") or ctype in ("application/json", "application/xml"):
            title, text, links = "", text_raw, []
        else:
            raise ToolError(f"unsupported content type {ctype!r} at {current}")
        page = {"url": url, "final_url": current, "status": status, "title": title, "total_chars": len(text)}
        if find.strip():
            return {**page, **_find_passages(text, find.strip(), limit)}
        if offset and offset >= len(text):
            raise ToolArgumentError(f"web_fetch: offset {offset} is past the end of the page's text "
                                    f"({len(text)} characters)")
        end = min(offset + limit, len(text))
        part = {"text": text[offset:end], "offset": offset, "truncated": end < len(text) or offset > 0}
        if part["truncated"]:
            part["note"] = (f"This is characters {offset} to {end} of {len(text)}; the rest of the page is not "
                            "shown here. "
                            + (f"Call web_fetch again with offset={end} to read on, " if end < len(text) else "")
                            + "or use find to get the passages that mention what you need. Do not guess what the "
                            "rest says.")
            if end < len(text):
                part["next_offset"] = end
        return {**page, **part, "links": links[:25]}

    @tool(
        description="List files and folders in the agent's sandboxed workspace.",
        effect=Effect.READ,
        category=ToolCategory.FILES,
        variant=TaskVariant.BROWSING,
        args={"path": "Folder relative to the workspace root.", "recursive": "List sub-folders too."},
    )
    async def list_files(ctx: ToolContext, path: str = ".", recursive: bool = False) -> dict[str, Any]:
        folder = ctx.sandbox.resolve(path)
        if not folder.is_dir():
            raise ToolError(f"{path!r} is not a folder in the workspace")
        entries = folder.rglob("*") if recursive else folder.iterdir()
        items = []
        for p in sorted(entries):
            if len(items) >= 500:
                break
            items.append({
                "path": ctx.sandbox.relative(p),
                "type": "dir" if p.is_dir() else "file",
                "bytes": p.stat().st_size if p.is_file() else None,
            })
        return {"path": ctx.sandbox.relative(folder), "entries": items}

    @tool(
        description="Read a UTF-8 text file from the agent's sandboxed workspace.",
        effect=Effect.READ,
        category=ToolCategory.FILES,
        variant=TaskVariant.BROWSING,
        args={"path": "File path relative to the workspace root.", "max_chars": "Maximum characters to return."},
    )
    async def read_file(ctx: ToolContext, path: str, max_chars: int = 20000) -> dict[str, Any]:
        target = ctx.sandbox.resolve(path)
        if not target.is_file():
            raise ToolError(f"{path!r} does not exist in the workspace")
        text = target.read_text(encoding="utf-8", errors="replace")
        return {"path": ctx.sandbox.relative(target), "content": text[:max_chars], "truncated": len(text) > max_chars}

    @tool(
        description="Write a UTF-8 text file in the agent's sandboxed workspace. This is an action: "
        "it is reviewed by the Sentinel and may need the user's approval.",
        effect=Effect.SIDE_EFFECT,
        category=ToolCategory.FILES,
        args={
            "path": "File path relative to the workspace root.",
            "content": "Full text content to write.",
            "overwrite": "Replace the file if it already exists.",
        },
    )
    async def write_file(ctx: ToolContext, path: str, content: str, overwrite: bool = False) -> dict[str, Any]:
        target = ctx.sandbox.resolve(path)
        if target.exists() and not overwrite:
            raise ToolError(f"{path!r} already exists; set overwrite to true to replace it")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"path": ctx.sandbox.relative(target), "bytes": target.stat().st_size}

    @tool(
        description="Save a private note for yourself (allowed in read-only research mode).",
        effect=Effect.PRIVATE_WRITE,
        category=ToolCategory.NOTES,
        args={"title": "Short title.", "body": "Note text."},
    )
    async def note_write(ctx: ToolContext, title: str, body: str) -> dict[str, Any]:
        return ctx.store.add_note(title=title, body=body, task_id=ctx.task_id)

    @tool(
        description="List your most recent private notes.",
        effect=Effect.READ,
        category=ToolCategory.NOTES,
        variant=TaskVariant.BROWSING,
        args={"limit": "How many notes to return."},
    )
    async def note_list(ctx: ToolContext, limit: int = 20) -> dict[str, Any]:
        return {"notes": ctx.store.list_notes(limit=limit)}

    @tool(
        description="Search long-term memory for facts about the user and past work.",
        effect=Effect.READ,
        category=ToolCategory.MEMORY,
        variant=TaskVariant.BROWSING,
        args={"query": "Words to search for.", "limit": "Maximum results."},
    )
    async def memory_search(ctx: ToolContext, query: str, limit: int = 8) -> dict[str, Any]:
        return {"results": ctx.memory.search(query, limit=limit)}

    @tool(
        description="Remember a durable fact or preference in long-term memory.",
        effect=Effect.PRIVATE_WRITE,
        category=ToolCategory.MEMORY,
        args={"content": "The fact to remember, as one sentence.", "tags": "Optional tags."},
    )
    async def memory_add(ctx: ToolContext, content: str, tags: list | None = None) -> dict[str, Any]:
        source = f"task:{ctx.task_id}" if ctx.task_id else f"run:{ctx.run_id}"
        return ctx.memory.add(content, tags=[str(t) for t in tags or []], source=source)

    @tool(
        description="Permanently forget a memory by id (use when the user asks you to forget something).",
        effect=Effect.SIDE_EFFECT,
        category=ToolCategory.MEMORY,
        args={"memory_id": "Id of the memory to delete."},
    )
    async def memory_forget(ctx: ToolContext, memory_id: int) -> dict[str, Any]:
        ctx.memory.forget(memory_id)
        return {"forgotten": memory_id}

    @tool(
        description="Get the current date and time in a timezone (default Europe/London).",
        effect=Effect.READ,
        category=ToolCategory.TIME,
        args={"timezone": "IANA timezone name, e.g. Europe/London."},
    )
    async def current_time(ctx: ToolContext, timezone: str = "") -> dict[str, Any]:
        tz_name = timezone or ctx.config.runtime.timezone
        try:
            tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ToolError(f"unknown timezone {tz_name!r}") from exc
        now = datetime.now(tz)
        return {
            "timezone": tz_name,
            "iso": now.isoformat(timespec="seconds"),
            "human": now.strftime("%A %d %B %Y, %H:%M"),
            "utc_offset": now.strftime("%z"),
            "dst": bool(now.dst()),
        }

    async def describe_schedule(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        rec, _tz = _schedule_recurrence(ctx, args)
        runs, t = [], datetime.now(UTC)
        for _ in range(3):
            t = rec.next_after(t)
            runs.append(_local_words(t, rec.timezone or ctx.config.runtime.timezone))
        repeats = rec.describe() + (f" ({rec.timezone})" if rec.timezone else "")
        return {"repeats": repeats, "next_runs": "; ".join(runs)}

    @tool(
        description="Propose a schedule: a job Jig then runs by itself at set times, in the background, such as "
        "'every weekday at 08:00, summarise the news about X'. The user must approve it before it is saved. "
        "repeat is daily, weekdays (Monday to Friday), weekly (with days), interval (with every_minutes) or "
        "cron (with a five-field cron expression). Times are local, 24-hour HH:MM.",
        effect=Effect.SIDE_EFFECT,
        category=ToolCategory.TIME,
        human_only=True,
        resolve=describe_schedule,
        args={
            "name": "Short name for the schedule, such as 'Morning news summary'.",
            "prompt": "What to do each time, as complete, self-contained instructions.",
            "repeat": "How often it runs.",
            "at": "For daily, weekdays and weekly: the local time, HH:MM (24-hour), such as 08:00.",
            "days": "For weekly: the days, such as [\"mon\", \"thu\"].",
            "every_minutes": "For interval: minutes between runs (at least 1).",
            "cron": "For cron: the expression, such as '0 8 * * 1-5'.",
            "mode": "research (just looks things up and takes notes; the usual choice) or action (may act, asking "
                    "the user when needed).",
            "timezone": "IANA timezone for the times; leave empty for the user's timezone.",
        },
    )
    async def schedule_create(ctx: ToolContext, name: str, prompt: str,
                              repeat: Literal["daily", "weekdays", "weekly", "interval", "cron"], at: str = "",
                              days: list | None = None, every_minutes: int = 0, cron: str = "",
                              mode: Literal["research", "action"] = "research", timezone: str = "") -> dict[str, Any]:
        rec, tz = _schedule_recurrence(ctx, {"repeat": repeat, "at": at, "days": days,
                                             "every_minutes": every_minutes, "cron": cron, "timezone": timezone})
        source = f"task:{ctx.task_id}" if ctx.task_id else f"run:{ctx.run_id}"
        s = ctx.store.create_schedule(name=name.strip(), prompt=prompt.strip(), mode=Mode(mode), repeat=rec.repeat,
                                      timezone=tz, created_by=f"agent ({source})")
        return {"schedule_id": s["id"], "name": s["name"], "repeats": s["repeat_text"], "timezone": s["timezone"],
                "first_run": _local_words(datetime.fromisoformat(s["next_run_at"]), tz), "mode": s["mode"]}

    @tool(
        description="List the schedules: what Jig does by itself and when it next runs.",
        effect=Effect.READ,
        category=ToolCategory.TIME,
    )
    async def schedule_list(ctx: ToolContext) -> dict[str, Any]:
        default_tz = ctx.config.runtime.timezone
        return {"schedules": [{
            "id": s["id"], "name": s["name"], "prompt": s["prompt"], "mode": s["mode"], "enabled": s["enabled"],
            "repeats": s["repeat_text"], "timezone": s["timezone"],
            "next_run": _local_words(datetime.fromisoformat(s["next_run_at"]), s["timezone"] or default_tz),
            "last_status": s["last_task"]["status"] if s["last_task"] else None,
        } for s in ctx.store.list_schedules()]}

    return registry


def _schedule_recurrence(ctx: ToolContext, args: dict[str, Any]) -> tuple[Recurrence, str]:
    """The schedule_create arguments as a validated repeat, and the timezone its times are in."""
    kind = args.get("repeat")
    tz = args.get("timezone") or ctx.config.runtime.timezone
    if kind == "interval":
        minutes = args.get("every_minutes") or 0
        if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes < 1:
            raise ToolArgumentError("schedule_create: an interval repeat needs every_minutes of at least 1")
        repeat: dict[str, Any] = {"kind": "interval", "interval_s": minutes * 60}
    elif kind == "cron":
        repeat = {"kind": "cron", "cron": args.get("cron") or ""}
    elif kind == "weekly":
        repeat = {"kind": "weekly", "at": args.get("at") or "", "days": args.get("days") or []}
    else:
        repeat = {"kind": kind, "at": args.get("at") or ""}
    try:
        rec = Recurrence(repeat, tz)
    except ValueError as exc:
        raise ToolArgumentError(f"schedule_create: {exc}") from None
    return rec, tz


_FIND_CONTEXT = 300


def _find_passages(text: str, needle: str, limit: int) -> dict[str, Any]:
    """The passages around each match of ``needle`` (case-insensitive), merged where they overlap, up to ``limit``
    characters in all. Each passage says where it starts, so the model can read on from there with offset."""
    lower, target = text.lower(), needle.lower()
    spans: list[list[int]] = []
    matches = 0
    start = lower.find(target)
    while start >= 0:
        matches += 1
        lo, hi = max(0, start - _FIND_CONTEXT), min(len(text), start + len(target) + _FIND_CONTEXT)
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
        out["note"] = f"{needle!r} does not appear in the page's text ({len(text)} characters)."
    return out


def _local_words(t: datetime, tz: str) -> str:
    return t.astimezone(ZoneInfo(tz)).strftime("%a %d %b %Y, %H:%M")


def http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"User-Agent": "Jig/0.1 (+local personal agent)"}, trust_env=False)
