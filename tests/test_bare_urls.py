"""Bare web addresses (``www.example.com``) are completed to ``https://`` before every check, never otherwise."""

from __future__ import annotations

import asyncio
import json

import pytest

from jig.constants import Mode
from jig.model import ToolCall
from jig.policy.gate import CallContext
from jig.policy.urls import complete_bare_web_address, complete_url_args

from .conftest import wait_for


@pytest.mark.parametrize("value", [
    "www.example.com",
    "example.com/path",
    "Example.COM/a/b?q=1#frag",
    "example.com:8443/x",
    "sub.domain.example.co.uk",
    "xn--bcher-kva.example",
    "93.184.215.14/index.html",
    "127.0.0.1/admin",          # completed, then refused by no-local-network like any local address
    "[::1]:8080/",
])
def test_unambiguous_hosts_are_completed_to_https(value):
    assert complete_bare_web_address(value) == "https://" + value


@pytest.mark.parametrize("value", [
    "http://example.com", "https://example.com", "HTTP://example.com", "ftp://example.com/file",
    "mailto:someone@example.com", "javascript:alert(1)", "data:text/html,hi", "file:///etc/passwd",
    "file:C:/Windows", "//example.com/path", "/etc/passwd", "C:\\Windows\\win.ini",
    "localhost", "localhost:8080/admin", "intranet/page", "example", "example.c0m", "-bad.example.com",
    "bad-.example.com", "user@example.com", "user:pw@example.com/x", "exa mple.com", " example.com",
    "example.com:99999", "", "[not-an-ip]/x", "a" * 64 + ".com",
])
def test_everything_else_is_left_exactly_as_written(value):
    assert complete_bare_web_address(value) is None


def test_only_url_arguments_are_completed_and_never_to_http():
    args = {"url": "www.example.com", "uri": "http://example.com", "endpoint": "example.org/api", "title": "example.com"}
    out, changes = complete_url_args(args)
    assert out == {"url": "https://www.example.com", "uri": "http://example.com",
                   "endpoint": "https://example.org/api", "title": "example.com"}
    assert {c["arg"] for c in changes} == {"url", "endpoint"}
    assert not any(c["normalised"].startswith("http://") for c in changes)
    assert complete_url_args({"url": "https://example.com"}) == ({"url": "https://example.com"}, [])


def _ctx(run_id: str) -> CallContext:
    return CallContext(run_id, None, Mode.ACTION, "read the example.com home page")


async def test_core_rule_sees_the_completed_url(jig):
    call = ToolCall(id="b1", name="web_fetch", arguments_raw=json.dumps({"url": "127.0.0.1/admin"}))
    outcome = await jig.executor.execute(call, _ctx("r_bare_local"))
    assert not outcome.ok and outcome.error_type == "PolicyBlocked"
    assert outcome.error.startswith("core rule no-local-network") and "127.0.0.1" in outcome.error, outcome.error
    rows = jig.audit.query(kind="policy.url_normalised", run_id="r_bare_local")
    assert len(rows) == 1
    data = json.loads(rows[0]["data_json"])
    assert data["normalised"] == [{"arg": "url", "original": "127.0.0.1/admin", "normalised": "https://127.0.0.1/admin"}]


async def test_single_label_and_other_schemes_stay_refused(jig):
    for i, url in enumerate(["localhost:8000/x", "file:///etc/passwd", "javascript:alert(1)"]):
        call = ToolCall(id=f"b2{i}", name="web_fetch", arguments_raw=json.dumps({"url": url}))
        outcome = await jig.executor.execute(call, _ctx(f"r_bare_scheme{i}"))
        assert not outcome.ok and outcome.error_type == "PolicyBlocked", url
        assert "only http and https URLs are allowed" in outcome.error, outcome.error
        assert not jig.audit.query(kind="policy.url_normalised", run_id=f"r_bare_scheme{i}")


async def test_custom_rule_matches_the_completed_url(jig):
    jig.rules.create(tool="web_fetch", decision="block", arg="url", pattern="https://www.example.com/*")
    call = ToolCall(id="b3", name="web_fetch", arguments_raw=json.dumps({"url": "www.example.com/page"}))
    outcome = await jig.executor.execute(call, _ctx("r_bare_rule"))
    assert not outcome.ok and outcome.error_type == "PolicyBlocked"
    assert outcome.error.startswith("custom rule"), outcome.error


async def test_approval_shows_the_completed_url_and_denial_still_stops_it(jig):
    jig.rules.create(tool="web_fetch", decision="ask", arg="url", pattern="https://example.com/*",
                     note="test: ask before fetching example.com")
    call = ToolCall(id="b4", name="web_fetch", arguments_raw=json.dumps({"url": "example.com/"}))
    running = asyncio.create_task(jig.executor.execute(call, _ctx("r_bare_ask")))
    pending = await wait_for(lambda: jig.approvals.list(status="pending"), what="a pending approval")
    approval = pending[0]
    assert approval["args"]["url"] == "https://example.com/"
    asked = [r for r in approval["reasons"] if r["decision"] != "info"]
    notes = [r for r in approval["reasons"] if r["decision"] == "info"]
    assert any(r["decision"] == "ask" for r in asked)
    assert [n["rule"] for n in notes] == ["url-normalised"] and "https://example.com/" in notes[0]["reason"]
    jig.approvals.respond(approval["id"], approve=False, note="not now")
    outcome = await running
    assert not outcome.ok and outcome.error_type == "ApprovalDenied"


async def test_completion_alone_never_asks_and_the_fetch_uses_https(jig):
    """No rule asks here. If the Sentinel asks, its reason is the only asking one; completion only ever adds a note."""
    call = ToolCall(id="b5", name="web_fetch", arguments_raw=json.dumps({"url": "example.com"}))
    running = asyncio.create_task(jig.executor.execute(call, _ctx("r_bare_fetch")))

    def done_or_answered() -> bool:
        for a in jig.approvals.list(status="pending"):
            assert {r["rule"] for r in a["reasons"] if r["decision"] != "info"} == {"sentinel"}, a["reasons"]
            jig.approvals.respond(a["id"], approve=True, note="test")
        return running.done()

    await wait_for(done_or_answered, what="the fetch to finish")
    outcome = running.result()
    assert outcome.ok, outcome.error
    assert outcome.result["url"] == "https://example.com" and outcome.result["final_url"].startswith("https://")
