"""Microsoft (Outlook calendar and OneDrive), live, against the user's own Microsoft account (personal, work or school). Opt-in:
skipped unless this is set:

  JIG_LIVE_MICROSOFT_CONFIG   a Jig config whose data directory has Microsoft connected with 'write' access
                              ('jig --config <it> connect microsoft --access write'), and which sets
                                  [connectors.microsoft]
                                  allowed_targets = ["[Jig test]", "Jig test"]
                                  required_prefix = "[Jig test]"

The user first creates an Outlook calendar named exactly "[Jig test]" and a OneDrive folder "Jig test" at the
top of OneDrive. The test creates, changes and cancels one "[Jig test]" event (no guests) in that calendar, and
saves, reads and deletes one "[Jig test]" file in that folder. It cleans up even if it fails.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from jig.connectors import microsoft as ms

from .connector_live import call, live_runtime

CONFIG = os.environ.get("JIG_LIVE_MICROSOFT_CONFIG", "")
pytestmark = pytest.mark.skipif(not CONFIG, reason="live Microsoft: set JIG_LIVE_MICROSOFT_CONFIG after connecting "
                                "Microsoft and creating the '[Jig test]' calendar and 'Jig test' folder "
                                "(docs/connectors-setup.md)")
PREFIX = "[Jig test]"
FOLDER = "Jig test"


def _limits(live):
    limits = live.config.connectors.get("microsoft")
    assert limits and set(limits.allowed_targets) == {PREFIX, FOLDER} and limits.required_prefix == PREFIX


async def test_outlook_calendar_end_to_end_on_test_events_only(capabilities):
    async with live_runtime(CONFIG, ms.NAME) as live:
        _limits(live)
        intent = f"Test Jig's Outlook connector with '{PREFIX}' events in the '{PREFIX}' calendar"
        cals = await call(live, "outlook_list_calendars", {}, intent=intent)
        assert cals.ok, cals.error
        test_cal = next((c for c in cals.result["calendars"] if c["name"] == PREFIX), None)
        assert test_cal, f"create an Outlook calendar named exactly {PREFIX!r} first"
        cal_id = test_cal["calendar_id"]
        tag = uuid.uuid4().hex[:8]
        start = (datetime.now(timezone.utc) + timedelta(days=2)).replace(minute=0, second=0, microsecond=0)
        iso = lambda d: d.isoformat(timespec="seconds")  # noqa: E731
        created = await call(live, "outlook_create_event", {
            "calendar_id": cal_id, "subject": f"{PREFIX} live {tag}", "start": iso(start),
            "end": iso(start + timedelta(hours=1)), "notes": f"made by the live test {tag}"}, intent=intent)
        assert created.ok, created.error
        assert created.policy["approval"]["status"] == "approved" and created.policy["resolved"]["calendar"] == PREFIX
        event_id = created.result["event_id"]
        try:
            got = await call(live, "outlook_get_event", {"calendar_id": cal_id, "event_id": event_id}, intent=intent)
            assert got.ok and got.result["subject"] == f"{PREFIX} live {tag}" and "approval" not in got.policy
            listed = await call(live, "outlook_list_events", {
                "calendar_id": cal_id, "time_min": iso(start - timedelta(hours=1)),
                "time_max": iso(start + timedelta(hours=2))}, intent=intent)
            assert listed.ok and event_id in [e["event_id"] for e in listed.result["events"]]
            moved = await call(live, "outlook_update_event", {"calendar_id": cal_id, "event_id": event_id,
                                                              "location": "nowhere"}, intent=intent)
            assert moved.ok and moved.result["location"] == "nowhere", moved.error
            assert moved.policy["resolved"]["event_summary"] == f"{PREFIX} live {tag}"
            renamed = await call(live, "outlook_update_event", {"calendar_id": cal_id, "event_id": event_id,
                                                                "subject": "Not a test"}, intent=intent)
            assert renamed.error_type == "PolicyBlocked" and "required_prefix" in renamed.error
            denied = await call(live, "outlook_create_event", {
                "calendar_id": cal_id, "subject": f"{PREFIX} denied {tag}", "start": iso(start),
                "end": iso(start + timedelta(hours=1))}, intent=intent, approve=False)
            assert denied.error_type == "ApprovalDenied"
            default = await call(live, "outlook_create_event", {
                "subject": f"{PREFIX} wrong calendar", "start": iso(start), "end": iso(start + timedelta(hours=1))},
                intent=intent)
            assert default.error_type == "PolicyBlocked" and "allowed_targets" in default.error
        finally:
            cancelled = await call(live, "outlook_cancel_event", {"calendar_id": cal_id, "event_id": event_id},
                                   intent=intent)
        assert cancelled.ok and cancelled.result["cancelled"], cancelled.error


async def test_a_calendar_can_be_named_and_a_miscopied_id_gets_the_real_calendars_back(capabilities):
    async with live_runtime(CONFIG, ms.NAME) as live:
        _limits(live)
        intent = f"Test Jig's Outlook connector with a '{PREFIX}' event in the '{PREFIX}' calendar, named not by id"
        cals = await call(live, "outlook_list_calendars", {}, intent=intent)
        assert cals.ok, cals.error
        cal_id = next(c["calendar_id"] for c in cals.result["calendars"] if c["name"] == PREFIX)
        # What a model sent in a capability test: the real id with a repeated stretch from the middle left out.
        miscopied = cal_id[:72] + cal_id[106:] if len(cal_id) > 110 else cal_id[:-6] + cal_id[-3:]
        start = (datetime.now(timezone.utc) + timedelta(days=3)).replace(minute=0, second=0, microsecond=0)
        iso = lambda d: d.isoformat(timespec="seconds")  # noqa: E731
        wrong = await call(live, "outlook_list_events", {
            "calendar_id": miscopied, "time_min": iso(start), "time_max": iso(start + timedelta(hours=1))},
            intent=intent)
        assert wrong.error_type == "ToolArgumentError" and repr(PREFIX) in wrong.error, wrong.error
        tag = uuid.uuid4().hex[:8]
        created = await call(live, "outlook_create_event", {
            "calendar_id": PREFIX.lower(), "subject": f"{PREFIX} by name {tag}", "start": iso(start),
            "end": iso(start + timedelta(hours=1))}, intent=intent)
        assert created.ok, created.error
        event_id = created.result["event_id"]
        try:
            assert created.result["calendar_id"] == cal_id and created.policy["resolved"]["calendar"] == PREFIX
            listed = await call(live, "outlook_list_events", {
                "calendar_id": PREFIX, "time_min": iso(start), "time_max": iso(start + timedelta(hours=1))},
                intent=intent)
            assert listed.ok and event_id in [e["event_id"] for e in listed.result["events"]], listed.error
        finally:
            cancelled = await call(live, "outlook_cancel_event", {"calendar_id": PREFIX, "event_id": event_id},
                                   intent=intent)
        assert cancelled.ok and cancelled.result["cancelled"], cancelled.error


async def test_onedrive_end_to_end_on_a_test_file_only(capabilities):
    async with live_runtime(CONFIG, ms.NAME) as live:
        _limits(live)
        tag = uuid.uuid4().hex[:8]
        name = f"{PREFIX} live {tag}.txt"
        intent = f"Test Jig's OneDrive connector with a file named '{name}' in the '{FOLDER}' folder"
        saved = await call(live, "onedrive_upload_file", {"folder": FOLDER, "name": name, "content": f"first {tag}"},
                           intent=intent)
        assert saved.ok, saved.error
        assert saved.policy["approval"]["status"] == "approved" and saved.policy["resolved"]["replaces"] is False
        item_id = saved.result["item_id"]
        try:
            read = await call(live, "onedrive_read_file", {"item_id": item_id}, intent=intent)
            assert read.ok and read.result["text"] == f"first {tag}" and "approval" not in read.policy
            listed = await call(live, "onedrive_list_folder", {"folder": FOLDER}, intent=intent)
            assert listed.ok and item_id in [i["item_id"] for i in listed.result["items"]]
            again = await call(live, "onedrive_upload_file", {"folder": FOLDER, "name": name, "content": "x"},
                               intent=intent)
            assert again.error_type == "ConnectorError" and "already has" in again.error, again.error
            replaced = await call(live, "onedrive_upload_file", {"folder": FOLDER, "name": name,
                                                                 "content": f"second {tag}", "replace": True},
                                  intent=intent)
            assert replaced.ok and replaced.policy["resolved"]["replaces"] is True, replaced.error
            assert (await call(live, "onedrive_read_file", {"item_id": item_id}, intent=intent)).result["text"] == \
                f"second {tag}"
            outside = await call(live, "onedrive_upload_file", {"name": name, "content": "x"}, intent=intent)
            assert outside.error_type == "PolicyBlocked" and "allowed_targets" in outside.error
            denied = await call(live, "onedrive_upload_file", {"folder": FOLDER, "name": f"{PREFIX} denied {tag}.txt",
                                                               "content": "x"}, intent=intent, approve=False)
            assert denied.error_type == "ApprovalDenied"
        finally:
            await live.connectors.request(ms.NAME, "DELETE", f"{ms.API}/me/drive/items/{item_id}")
