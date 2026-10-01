"""Starter tools. Each declares its effect so modes and review are enforced by the gate."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from ..constants import Effect, TaskVariant, ToolCategory
from ..errors import ToolError
from .registry import ToolContext, ToolRegistry
from .web import ensure_public, extract_readable


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    tool = registry.tool

    @tool(
        description="Fetch a public web page over HTTP(S) and return its readable text, title and links. "
        "Header values may reference vault secrets as {{secret:NAME}}.",
        effect=Effect.READ,
        category=ToolCategory.WEB,
        outbound=True,
        args={
            "url": "Absolute http(s) URL of a public page.",
            "headers": "Optional request headers.",
            "max_chars": "Maximum characters of text to return.",
        },
    )
    async def web_fetch(ctx: ToolContext, url: str, headers: dict | None = None, max_chars: int = 0) -> dict[str, Any]:
        cfg = ctx.config.web_fetch
        limit = min(max_chars or cfg.max_chars, cfg.max_chars)
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
        return {
            "url": url,
            "final_url": current,
            "status": status,
            "title": title,
            "text": text[:limit],
            "truncated": len(text) > limit,
            "links": links[:25],
        }

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

    return registry


def http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"User-Agent": "Jig/0.1 (+local personal agent)"}, trust_env=False)
