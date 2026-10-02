"""Google Calendar, live, against the user's own account. Opt-in: skipped unless this is set:

  JIG_LIVE_GCAL_CONFIG   a Jig config whose data directory has Google Calendar connected with 'write' access
                         ('jig --config <it> connect google-calendar --access write'), and which sets
                             [connectors.google-calendar]
                             allowed_targets = ["[Jig test]"]
                             required_prefix = "[Jig test]"

The user creates a calendar named exactly "[Jig test]" first. The test creates, changes and cancels only
"[Jig test]" events in that calendar, with no guests, and cancels everything it created even if it fails.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from .connector_live import call, live_runtime

CONFIG = os.environ.get("JIG_LIVE_GCAL_CONFIG", "")
pytestmark = pytest.mark.skipif(not CONFIG, reason="live Google Calendar: set JIG_LIVE_GCAL_CONFIG after connecting "
                                "Google Calendar and creating a '[Jig test]' calendar (docs/connectors-setup.md)")
PREFIX = "[Jig test]"


async def test_google_calendar_end_to_end_on_test_events_only(capabilities):
    async with live_runtime(CONFIG, "google-calendar") as live:
        limits = live.config.connectors.get("google-calendar")
        assert limits and limits.allowed_targets == [PREFIX] and limits.required_prefix == PREFIX
        intent = f"Test Jig's Google Calendar connector with '{PREFIX}' events in the '{PREFIX}' calendar"
        cals = await call(live, "gcal_list_calendars", {}, intent=intent)
        assert cals.ok, cals.error
        test_cal = next((c for c in cals.result["calendars"] if c["name"] == PREFIX), None)
        assert test_cal, f"create a calendar named exactly {PREFIX!r} first"
        cal_id = test_cal["calendar_id"]

        tag = uuid.uuid4().hex[:8]
        start = (datetime.now(timezone.utc) + timedelta(days=2)).replace(minute=0, second=0, microsecond=0)
        iso = lambda d: d.isoformat(timespec="seconds")  # noqa: E731
        created = await call(live, "gcal_create_event", {
            "calendar_id": cal_id, "summary": f"{PREFIX} live {tag}", "start": iso(start),
            "end": iso(start + timedelta(hours=1)), "description": f"made by the live test {tag}"}, intent=intent)
        assert created.ok, created.error
        assert created.policy["approval"]["status"] == "approved"
        assert created.policy["resolved"]["calendar"] == PREFIX
        event_id = created.result["event_id"]
        try:
            got = await call(live, "gcal_get_event", {"calendar_id": cal_id, "event_id": event_id}, intent=intent)
            assert got.ok and got.result["summary"] == f"{PREFIX} live {tag}"
            assert "approval" not in got.policy and "sentinel" not in got.policy
            listed = await call(live, "gcal_list_events", {
                "calendar_id": cal_id, "time_min": iso(start - timedelta(hours=1)),
                "time_max": iso(start + timedelta(hours=2)), "query": tag}, intent=intent)
            assert listed.ok and [e["event_id"] for e in listed.result["events"]] == [event_id]
            moved = await call(live, "gcal_update_event", {
                "calendar_id": cal_id, "event_id": event_id, "start": iso(start + timedelta(hours=1)),
                "end": iso(start + timedelta(hours=2)), "location": "nowhere"}, intent=intent)
            assert moved.ok and moved.result["location"] == "nowhere", moved.error
            assert moved.policy["resolved"]["event_summary"] == f"{PREFIX} live {tag}"
            renamed = await call(live, "gcal_update_event", {"calendar_id": cal_id, "event_id": event_id,
                                                             "summary": "Not a test"}, intent=intent)
            assert renamed.error_type == "PolicyBlocked" and "required_prefix" in renamed.error
            denied = await call(live, "gcal_create_event", {
                "calendar_id": cal_id, "summary": f"{PREFIX} denied {tag}", "start": iso(start),
                "end": iso(start + timedelta(hours=1))}, intent=intent, approve=False)
            assert denied.error_type == "ApprovalDenied"
            primary = await call(live, "gcal_create_event", {
                "calendar_id": "primary", "summary": f"{PREFIX} wrong calendar", "start": iso(start),
                "end": iso(start + timedelta(hours=1))}, intent=intent)
            assert primary.error_type == "PolicyBlocked" and "allowed_targets" in primary.error
        finally:
            cancelled = await call(live, "gcal_cancel_event", {"calendar_id": cal_id, "event_id": event_id},
                                   intent=intent)
        assert cancelled.ok and cancelled.result["cancelled"], cancelled.error
