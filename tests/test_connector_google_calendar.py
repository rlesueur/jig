"""Google Calendar, tested without the user's Google account: the real gate and Sentinel, and real requests
to Google's Calendar API and consent page, which answer a made-up token or client with their real errors.
The live tests are in test_connector_google_calendar_live.py."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from jig.config import ConnectorLimits
from jig.connectors import PROVIDERS, google, google_calendar as gcal, oauth
from jig.connectors.base import STATUS_NEEDS_RECONNECT, Connectors, Grant
from jig.constants import EventType, Mode
from jig.errors import ConnectorAuthError, ConnectorError, ToolArgumentError
from jig.model import ToolCall
from jig.policy.gate import CallContext
from jig.tools.builtin import http_client

from .conftest import audit_kinds, wait_for
from .test_connectors import FAKE_CLIENT, store  # noqa: F401  (fixture)

TOOLS = {"gcal_list_calendars", "gcal_list_events", "gcal_get_event", "gcal_create_event", "gcal_update_event",
         "gcal_cancel_event"}
READS = {"gcal_list_calendars", "gcal_list_events", "gcal_get_event"}
LIMITS = type("C", (), {"connectors": {"google-calendar": ConnectorLimits(
    allowed_targets=["[Jig test]"], allowed_recipients=["me@example.com"], required_prefix="[Jig test]")}})()


def test_tools_are_declared_safely_and_show_the_calendar_state(jig):
    specs = {t.name: t for t in jig.registry.all() if t.name.startswith("gcal_")}
    assert set(specs) == TOOLS
    for name, spec in specs.items():
        assert spec.category.value == "calendar" and spec.avatar_variant.value == "scheduling", name
        if name in READS:
            assert spec.effect.value == "read" and not spec.outbound
        else:
            assert spec.effect.value == "side_effect" and spec.outbound and spec.human_only, name
            assert spec.resolve is gcal.resolve_target and spec.precheck is gcal.limits_problem


def test_tools_are_offered_only_with_the_matching_access(jig):
    def offered(mode: Mode) -> set[str]:
        return {s["function"]["name"] for s in jig.registry.schemas_for_mode(mode)} & TOOLS

    assert offered(Mode.ACTION) == set()
    jig.connections.save(gcal.NAME, Grant(access_token="ya29.x", scopes=[gcal.S_CALENDARS, gcal.S_EVENTS_READ]),
                         account="me@example.com", access="read", via="test")
    assert offered(Mode.ACTION) == READS
    jig.connections.save(gcal.NAME, Grant(access_token="ya29.x", scopes=[gcal.S_CALENDARS, gcal.S_EVENTS]),
                         account="me@example.com", access="write", via="test")
    assert offered(Mode.ACTION) == TOOLS and offered(Mode.RESEARCH) == READS


async def test_the_real_calendar_api_rejects_a_bad_token(store):  # noqa: F811
    store.save(gcal.NAME, Grant(access_token="ya29.jig-test-not-real", scopes=[gcal.S_EVENTS]),
               account="me@example.com", access="write", via="test")
    async with http_client() as http:
        with pytest.raises(ConnectorAuthError, match="HTTP 401") as info:
            await Connectors(store, http, {}).request(gcal.NAME, "GET", f"{gcal.API}/users/me/calendarList")
    assert "jig-test-not-real" not in str(info.value)
    assert store.get(gcal.NAME)["status"] == STATUS_NEEDS_RECONNECT


async def test_requests_only_go_to_googles_api_over_https(store):  # noqa: F811
    store.save(gcal.NAME, Grant(access_token="ya29.x", scopes=[gcal.S_EVENTS]), account="me@example.com",
               access="write", via="test")
    async with http_client() as http:
        c = Connectors(store, http, {})
        for url in ("https://example.com/calendar/v3", "http://www.googleapis.com/calendar/v3/x"):
            with pytest.raises(ConnectorError, match="may only go to"):
                await c.request(gcal.NAME, "GET", url)


async def test_consent_page_asks_only_for_the_calendar_scopes():
    seen: list[str] = []
    task = asyncio.create_task(oauth.authorise(
        authorize_url=google.AUTHORIZE_URL, client_id=FAKE_CLIENT["client_id"],
        scopes=list(PROVIDERS[gcal.NAME].access_levels["write"].scopes), extra=google.authorise_params(),
        open_browser=seen.append, label="Google", timeout=5))
    await wait_for(lambda: seen, timeout=5, what="the sign-in link")
    scopes = dict(httpx.URL(seen[0]).params)["scope"].split()
    assert scopes == [gcal.S_CALENDARS, gcal.S_EVENTS]
    async with httpx.AsyncClient(trust_env=False, follow_redirects=True) as client:
        page = await client.get(seen[0])
    assert "invalid_client" in page.text or "OAuth client was not found" in page.text
    with pytest.raises(ConnectorError, match="no answer"):
        await task


async def test_read_only_mode_refuses_every_change(jig):
    for i, (name, args) in enumerate([
        ("gcal_create_event", {"summary": "x", "start": "2026-10-04", "end": "2026-10-05"}),
        ("gcal_update_event", {"event_id": "abcdef123", "summary": "x"}),
        ("gcal_cancel_event", {"event_id": "abcdef123"}),
    ]):
        outcome = await jig.executor.execute(ToolCall(id=f"c{i}", name=name, arguments_raw=json.dumps(args)),
                                             CallContext(run_id=f"r_c{i}", task_id=None, mode=Mode.RESEARCH,
                                                         intent="look only"))
        assert outcome.error_type == "ModeViolation", name


async def test_a_change_is_looked_up_before_review_and_fails_clearly_without_a_connection(jig):
    call = ToolCall(id="c9", name="gcal_create_event", arguments_raw=json.dumps(
        {"summary": "[Jig test] x", "start": "2026-10-04T15:00:00+01:00", "end": "2026-10-04T16:00:00+01:00"}))
    outcome = await jig.executor.execute(call, CallContext(run_id="r_c9", task_id=None, mode=Mode.ACTION,
                                                           intent="add a test event"))
    assert outcome.error_type == "ToolError" and "not connected" in outcome.error
    kinds = audit_kinds(jig, run_id="r_c9")
    assert "sentinel.verdict" not in kinds and "approval.requested" not in kinds


async def test_reading_shows_the_avatar_calendar_state(jig):
    """The avatar's 'scheduling' variant comes from these tools (the read fails without a connection,
    after it has started)."""
    seen: list[dict] = []
    jig.bus.add_listener(lambda e: seen.append(e.data) if e.type == EventType.TOOL_START else None)
    await jig.executor.execute(ToolCall(id="c10", name="gcal_list_calendars", arguments_raw="{}"),
                               CallContext(run_id="r_c10", task_id=None, mode=Mode.ACTION, intent="what's on"))
    assert any(d["tool"] == "gcal_list_calendars" and d["variant"] == "scheduling" for d in seen)


@pytest.mark.parametrize("args, resolved, why", [
    ({"calendar_id": "primary", "summary": "[Jig test] x"}, {"calendar_id": "me@example.com", "calendar": "Robyn"},
     "allowed_targets"),
    ({"calendar_id": "abc@group.calendar.google.com", "summary": "Dentist"},
     {"calendar_id": "abc@group.calendar.google.com", "calendar": "[Jig test]"}, "required_prefix"),
    ({"calendar_id": "abc@group.calendar.google.com", "event_id": "abcdef123"},
     {"calendar": "[Jig test]", "event_summary": "Someone else's meeting"}, "required_prefix"),
    ({"calendar_id": "abc@group.calendar.google.com", "summary": "[Jig test] x", "attendees": ["boss@example.com"]},
     {"calendar": "[Jig test]"}, "allowed_recipients"),
])
def test_limits_keep_changes_to_the_test_calendar_and_test_events(args, resolved, why):
    assert why in gcal.limits_problem(LIMITS, args, resolved)


def test_limits_allow_a_test_event_in_the_test_calendar():
    assert gcal.limits_problem(LIMITS, {"calendar_id": "abc@group.calendar.google.com", "summary": "[Jig test] x",
                                        "attendees": ["me@example.com"]},
                               {"calendar_id": "abc@group.calendar.google.com", "calendar": "[Jig test]",
                                "event_summary": "[Jig test] old"}) is None


@pytest.mark.parametrize("value, ok", [
    ("2026-10-04", {"date": "2026-10-04"}),
    ("2026-10-04T15:00:00+01:00", {"dateTime": "2026-10-04T15:00:00+01:00"}),
    ("2026-10-04T15:00:00Z", {"dateTime": "2026-10-04T15:00:00Z"}),
])
def test_times_are_dates_or_offset_date_times(value, ok):
    assert gcal._when(value, "start", "") == ok


@pytest.mark.parametrize("bad", ["tomorrow", "2026-13-01", "2026-10-04T15:00:00", "4 Oct 3pm"])
def test_ambiguous_times_are_refused(bad):
    with pytest.raises(ToolArgumentError):
        gcal._when(bad, "start", "")
    assert gcal._when("2026-10-04T15:00:00", "start", "Europe/London")["timeZone"] == "Europe/London"


@pytest.mark.parametrize("bad", [["Boss <boss@example.com>"], ["a@example.com,b@example.com"], "a@example.com",
                                 [f"g{i}@example.com" for i in range(21)]])
def test_guest_lists_must_be_plain_addresses(bad):
    with pytest.raises(ToolArgumentError):
        gcal._attendees(bad)


@pytest.mark.parametrize("value", ["primary", "me@example.com", "en.uk#holiday@group.v.calendar.google.com"])
def test_calendar_ids_are_checked_and_escaped(value):
    assert "/" not in gcal._cal_path(value) and "#" not in gcal._cal_path(value)
    for bad in ("../users/me", "primary/events", ""):
        with pytest.raises(ToolArgumentError):
            gcal._cal_path(bad)


async def test_an_allowed_change_still_needs_a_human(jig):
    """Even with a rule saying 'allow', creating an event is human-only. The lookup fails here without an
    account, so this checks the declaration the gate's core rule acts on, through the real core rules."""
    from jig.policy.core import evaluate_core

    jig.rules.create(tool="gcal_*", decision="allow", note="test: try to make it automatic")
    findings = await evaluate_core(jig.registry.get("gcal_create_event"), {"summary": "x"}, jig.vault)
    assert any(f.rule_id == "human-only-actions" and f.decision.value == "ask" for f in findings)
