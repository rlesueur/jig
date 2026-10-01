"""Headless browser tools in the container sandbox, through the real gate and Sentinel. Nothing is mocked.

Needs Docker, the sandbox image (`jig sandbox build`) and internet access (example.com, httpbin.org).
"""

from __future__ import annotations

import pytest

from jig.config import load_config
from jig.constants import EventType, Mode
from jig.runtime import Jig
from jig.tools.browser import register_browser_tools
from jig.tools.registry import ToolRegistry

from .sandbox_helpers import gated_call

pytestmark = pytest.mark.network


@pytest.fixture
async def bjig(tmp_path, capabilities):
    config = load_config(data_dir=tmp_path / "data", sandbox_dir=tmp_path / "sandbox", sandbox_backend="container")
    runtime = Jig(config)
    await runtime.start(run_scheduler=False, check_capabilities=False)
    try:
        yield runtime
    finally:
        await runtime.stop()


def test_browser_tools_are_classified():
    registry = ToolRegistry()
    register_browser_tools(registry)
    for name in ("browser_open", "browser_read", "browser_screenshot"):
        assert registry.get(name).allowed_in(Mode.RESEARCH), name
    for name in ("browser_click", "browser_type", "browser_fill", "browser_submit", "browser_login"):
        assert not registry.get(name).allowed_in(Mode.RESEARCH), name
    assert registry.get("browser_submit").human_only and registry.get("browser_login").human_only
    assert all(t.avatar_variant.value == "browsing" for t in registry.all())


async def test_read_tools_on_a_real_page_in_research_mode(bjig):
    events = []
    bjig.bus.add_listener(events.append)
    intent = "Research: find out what example.com says about itself."

    opened, _ = await gated_call(bjig, "browser_open", {"url": "https://example.com"}, intent=intent,
                                 mode=Mode.RESEARCH)
    assert opened.ok, opened.error
    assert opened.result["title"] == "Example Domain"
    assert "documentation" in opened.result["text"].lower() or "example" in opened.result["text"].lower()

    snap, _ = await gated_call(bjig, "browser_read", {"format": "snapshot"}, intent=intent, mode=Mode.RESEARCH)
    assert snap.ok, snap.error
    assert 'link "Learn more"' in snap.result["snapshot"]
    assert "documentation examples" in snap.result["snapshot"]

    shot, _ = await gated_call(bjig, "browser_screenshot", {}, intent=intent, mode=Mode.RESEARCH)
    assert shot.ok, shot.error
    png = bjig.sandbox.resolve(shot.result["path"]).read_bytes()
    assert png.startswith(b"\x89PNG") and len(png) > 2000

    if bjig.vision.enabled:
        seen, _ = await gated_call(bjig, "browser_screenshot", {"question": "Quote the first English sentence "
                                   "on this page and name the link text."}, intent=intent, mode=Mode.RESEARCH)
        assert seen.ok, seen.error
        answer = seen.result["vision"]["answer"].lower()
        assert "domain" in answer and "learn more" in answer, answer

    clicked, _ = await gated_call(bjig, "browser_click", {"selector": "a"}, intent=intent, mode=Mode.RESEARCH)
    assert not clicked.ok and clicked.error_type == "ModeViolation"

    starts = [e for e in events if e.type == EventType.TOOL_START]
    assert starts and all(e.data["variant"] == "browsing" for e in starts)
    assert any(r["kind"] == "egress.allow" and "example.com" in r["summary"] for r in bjig.audit.query(kind="egress"))


async def test_form_submission_pauses_for_approval(bjig):
    intent = "Fill in the customer name on the httpbin.org test order form with Robyn and submit it."
    opened, _ = await gated_call(bjig, "browser_open", {"url": "https://httpbin.org/forms/post"}, intent=intent)
    assert opened.ok, opened.error
    filled, _ = await gated_call(bjig, "browser_fill", {"selector": "input[name=custname]", "value": "Robyn"},
                                 intent=intent)
    assert filled.ok, filled.error

    # A click on the submit button must not send the form.
    clicked, _ = await gated_call(bjig, "browser_click", {"selector": "form button"}, intent=intent)
    assert clicked.ok, clicked.error
    assert clicked.result["url"].endswith("/forms/post"), clicked.result
    assert clicked.result.get("blocked_form_submissions", 0) >= 1 or clicked.result.get("blocked_requests")

    session = await bjig.container.browser()
    state_during_approval: dict = {}

    async def inspect_then_approve(approval: dict) -> bool:
        if approval["tool"] == "browser_submit":
            assert any(r["rule"] == "human-only-actions" for r in approval["reasons"]), approval["reasons"]
            state_during_approval.update(await session.call("read", format="text", max_chars=200))
        return True

    submitted, approvals = await gated_call(bjig, "browser_submit", {"selector": "form"}, intent=intent,
                                            on_approval=inspect_then_approve)
    assert any(a["tool"] == "browser_submit" for a in approvals), "browser_submit must ask for approval"
    assert state_during_approval["url"].endswith("/forms/post"), "nothing may be sent before approval"
    assert submitted.ok, submitted.error
    assert submitted.result["url"].endswith("/post") and "/forms/" not in submitted.result["url"]
    page, _ = await gated_call(bjig, "browser_read", {"format": "text"}, intent=intent)
    assert '"custname": "Robyn"' in page.result["text"], page.result["text"][:500]


async def test_denied_submission_sends_nothing(bjig):
    intent = "Fill in the httpbin.org test order form with the name Robyn and submit it."
    assert (await gated_call(bjig, "browser_open", {"url": "https://httpbin.org/forms/post"}, intent=intent))[0].ok
    denied, _ = await gated_call(bjig, "browser_submit", {"selector": "form"}, intent=intent,
                                 on_approval=lambda a: a["tool"] != "browser_submit")
    assert not denied.ok and denied.error_type == "ApprovalDenied"
    page, _ = await gated_call(bjig, "browser_read", {"format": "text"}, intent=intent)
    assert page.result["url"].endswith("/forms/post")


async def test_login_password_must_be_a_vault_reference(bjig):
    args = {"username_selector": "#u", "username": "robyn", "password_selector": "#p", "password": "hunter2",
            "submit_selector": "button"}
    outcome, approvals = await gated_call(bjig, "browser_login", args, intent="Sign in to the test site.")
    assert not outcome.ok and outcome.error_type == "ToolArgumentError"
    assert not approvals
