"""Headless browser tools (Playwright + Chromium in the container sandbox).

Registered only with ``[sandbox] backend = "container"``. Reading tools are
``read`` effects, so they work in research mode. Interacting tools are
side effects and go through the Sentinel and approvals; submitting a form or
signing in is human-only, so it always needs the user's approval. Inside the
browser, form submissions and non-GET requests are blocked unless one of those
approved tools is running. All traffic goes through the egress proxy, which is
open only while a tool that opens the network is running.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from ..constants import Effect, TaskVariant, ToolCategory
from ..errors import ToolError
from ..vault import SECRET_REF
from .checkout import classify
from .registry import ToolContext, ToolRegistry
from .web import ensure_public

SCREENSHOT_DIR = "screenshots"


async def inspect_target(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Before review: what this submit or click would send, and whether it is a checkout or booking
    (see checkout.py). Read-only and offline: it only looks at the page already open."""
    session = await ctx.container.browser()
    try:
        info = await session.call("inspect_submit", selector=args.get("selector") or args.get("submit_selector"))
    except ToolError as exc:
        if "unknown command" in str(exc):
            raise ToolError("the sandbox image is older than this Jig and can't check for checkouts; rebuild it "
                            "with 'jig sandbox build'") from None
        raise
    return classify(info)


async def inspect_login(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Before review: the site the sign-in would go to, and that all three selectors match something on the
    page, so an attempt that can't work fails here instead of asking the user to approve it."""
    session = await ctx.container.browser()
    for key in ("username_selector", "password_selector"):
        try:
            await session.call("inspect_submit", selector=args[key])
        except ToolError as exc:
            raise ToolError(f"{key} {args[key]!r} doesn't match a field on this page ({exc}); use a selector from "
                            "browser_read with format 'snapshot'") from None
    try:
        info = await session.call("inspect_submit", selector=args["submit_selector"])
    except ToolError as exc:
        raise ToolError(f"submit_selector {args['submit_selector']!r} doesn't match a button on this page ({exc}); "
                        "use a selector from browser_read with format 'snapshot'") from None
    return {"signs_in_to": str(info.get("host") or ""), **classify(info)}


async def _call(ctx: ToolContext, tool: str, command: str, *, network: bool, **args: Any) -> dict[str, Any]:
    session = await ctx.container.browser()
    if not network:
        return await session.call(command, **args)
    async with ctx.container.lease(tool=tool, run_id=ctx.run_id, task_id=ctx.task_id):
        return await session.call(command, **args)


def register_browser_tools(registry: ToolRegistry) -> None:
    tool = registry.tool
    web = {"category": ToolCategory.WEB, "variant": TaskVariant.BROWSING}

    @tool(
        description="Open a public web page in the sandboxed headless browser (runs JavaScript) and return its "
        "title, readable text and links. Use web_fetch for simple pages.",
        effect=Effect.READ, outbound=True, **web,
        args={"url": "Absolute http(s) URL of a public page.", "max_chars": "Maximum characters of text to return."},
    )
    async def browser_open(ctx: ToolContext, url: str, max_chars: int = 6000) -> dict[str, Any]:
        await ensure_public(url)
        return await _call(ctx, "browser_open", "open", network=True, url=url, max_chars=max_chars)

    @tool(
        description="Read the page currently open in the browser, as readable text or as an accessibility "
        "snapshot (roles and names of buttons, links, fields) with 'controls': each visible field and button "
        "with a selector that matches only it. Use those selectors for clicking, typing, filling and signing in.",
        effect=Effect.READ, **web,
        args={"format": "text or snapshot.", "max_chars": "Maximum characters to return."},
    )
    async def browser_read(ctx: ToolContext, format: Literal["text", "snapshot"] = "text",
                           max_chars: int = 12000) -> dict[str, Any]:
        return await _call(ctx, "browser_read", "read", network=False, format=format, max_chars=max_chars)

    @tool(
        description="Take a screenshot of the current page and save it in the workspace. With a question, the "
        "image is shown to the model and the answer returned (needs vision to be enabled); without one, use "
        "browser_read for the page's text.",
        effect=Effect.READ, **web,
        args={"question": "Optional question about what the page looks like.",
              "full_page": "Capture the whole page rather than the visible part."},
    )
    async def browser_screenshot(ctx: ToolContext, question: str = "", full_page: bool = False) -> dict[str, Any]:
        if question:
            await ctx.vision.require()
        path = f"{SCREENSHOT_DIR}/shot-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.png"
        result = await _call(ctx, "browser_screenshot", "screenshot", network=False, path=path, full_page=full_page)
        if question:
            image = ctx.sandbox.resolve(path).read_bytes()
            result["vision"] = await ctx.vision.describe(image, question)
        else:
            result["note"] = "Saved. Ask a question to have it described (needs vision) or use browser_read."
        return result

    @tool(
        description="Click an element on the current page. Selector: CSS, text=..., or role=button[name=\"...\"]. "
        "Form submissions caused by the click are blocked; use browser_submit for those. Clicking anything that "
        "pays, buys or books always needs the user's approval.",
        effect=Effect.SIDE_EFFECT, outbound=True, resolve=inspect_target, **web,
        args={"selector": "Playwright selector of the element to click."},
    )
    async def browser_click(ctx: ToolContext, selector: str) -> dict[str, Any]:
        return await _call(ctx, "browser_click", "click", network=True, selector=selector)

    @tool(
        description="Type text into a field on the current page, key by key (triggers search suggestions and "
        "similar). Pressing Enter cannot submit a form; use browser_submit for that.",
        effect=Effect.SIDE_EFFECT, outbound=True, **web,
        args={"selector": "Playwright selector of the field.", "text": "Text to type.",
              "press_enter": "Press Enter afterwards."},
    )
    async def browser_type(ctx: ToolContext, selector: str, text: str, press_enter: bool = False) -> dict[str, Any]:
        return await _call(ctx, "browser_type", "type", network=True, selector=selector, text=text,
                           press_enter=press_enter)

    @tool(
        description="Set the value of a form field on the current page (replaces its contents). Does not submit. "
        "Card and bank fields are refused: the user enters payment details themselves.",
        effect=Effect.SIDE_EFFECT, outbound=True, **web,
        args={"selector": "Playwright selector of the field.", "value": "Value to put in the field."},
    )
    async def browser_fill(ctx: ToolContext, selector: str, value: str) -> dict[str, Any]:
        return await _call(ctx, "browser_fill", "fill", network=True, selector=selector, value=value)

    @tool(
        description="Submit a form on the current page, by its form element or its submit button. Always needs "
        "the user's approval. Jig never types card or bank details: at a payment step, stop and let the user "
        "enter them.",
        effect=Effect.SIDE_EFFECT, outbound=True, human_only=True, resolve=inspect_target, **web,
        args={"selector": "Playwright selector of the form or its submit button."},
    )
    async def browser_submit(ctx: ToolContext, selector: str) -> dict[str, Any]:
        return await _call(ctx, "browser_submit", "submit", network=True, selector=selector)

    @tool(
        description="Sign in on the current page with credentials from the vault. The password must be a vault "
        "reference {{secret:NAME}} (the username may be one too); the real values never reach you. Always needs "
        "the user's approval.",
        effect=Effect.SIDE_EFFECT, outbound=True, human_only=True, resolve=inspect_login, **web,
        args={"username_selector": "Selector of the username or email field.",
              "username": "Username, or a vault reference {{secret:NAME}}.",
              "password_selector": "Selector of the password field.",
              "password": "Vault reference {{secret:NAME}} for the password.",
              "submit_selector": "Selector of the sign-in button."},
    )
    async def browser_login(ctx: ToolContext, username_selector: str, username: str, password_selector: str,
                            password: str, submit_selector: str) -> dict[str, Any]:
        return await _call(ctx, "browser_login", "login", network=True, username_selector=username_selector,
                           username=username, password_selector=password_selector, password=password,
                           submit_selector=submit_selector)

    # Literal passwords are refused at schema validation, before the vault resolves anything.
    registry.get("browser_login").parameters["properties"]["password"]["pattern"] = SECRET_REF.pattern
