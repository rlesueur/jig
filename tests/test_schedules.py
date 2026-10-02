"""Schedules: calendar times and cron as well as intervals, in the schedule's own timezone, the scheduler firing
them, people's changes, and the agent's schedule_create tool, which always needs the user's approval."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from jig.constants import EventType, Mode
from jig.db import iso, now
from jig.errors import NotFound
from jig.recurrence import Recurrence, interval_words

from .conftest import wait_for
from .sandbox_helpers import gated_call

LONDON = "Europe/London"


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


# The repeat rules themselves ---------------------------------------------------------------------------------

def test_weekdays_daily_and_weekly_times_are_local():
    weekdays = Recurrence({"kind": "weekdays", "at": "08:00"}, LONDON)
    # Friday 2 October 2026, 09:00 BST: the next weekday 08:00 is Monday, 07:00 UTC.
    assert weekdays.next_after(utc(2026, 10, 2, 8, 0)) == utc(2026, 10, 5, 7, 0)
    assert weekdays.next_after(utc(2026, 10, 2, 6, 59)) == utc(2026, 10, 2, 7, 0)
    assert weekdays.next_after(utc(2026, 10, 2, 7, 0)) == utc(2026, 10, 5, 7, 0), "strictly after"
    daily = Recurrence({"kind": "daily", "at": "8:30"}, "America/New_York")
    assert daily.repeat == {"kind": "daily", "at": "08:30"}
    assert daily.next_after(utc(2026, 10, 2, 13, 0)) == utc(2026, 10, 3, 12, 30)  # 08:30 EDT
    weekly = Recurrence({"kind": "weekly", "days": ["Thursday", "mon"], "at": "19:15"}, LONDON)
    assert weekly.repeat == {"kind": "weekly", "at": "19:15", "days": ["mon", "thu"]}
    assert weekly.next_after(utc(2026, 10, 2, 12, 0)) == utc(2026, 10, 5, 18, 15)
    assert weekly.next_after(utc(2026, 10, 5, 18, 15)) == utc(2026, 10, 8, 18, 15)


def test_daylight_saving_changes():
    daily = Recurrence({"kind": "daily", "at": "08:00"}, LONDON)
    # Clocks go back on Sunday 25 October 2026: 08:00 is 07:00 UTC before and 08:00 UTC after.
    assert daily.next_after(utc(2026, 10, 24, 7, 0)) == utc(2026, 10, 25, 8, 0)
    # 01:30 happens twice that night: it runs once, the first time (01:30 BST, 00:30 UTC).
    early = Recurrence({"kind": "daily", "at": "01:30"}, LONDON)
    first = early.next_after(utc(2026, 10, 24, 12, 0))
    assert first == utc(2026, 10, 25, 0, 30)
    assert early.next_after(first) == utc(2026, 10, 26, 1, 30)
    # Clocks go forward on Sunday 29 March 2026: 01:30 doesn't exist, so it runs at 02:30 BST (01:30 UTC).
    assert early.next_after(utc(2026, 3, 28, 12, 0)) == utc(2026, 3, 29, 1, 30)
    assert early.next_after(utc(2026, 3, 29, 1, 30)) == utc(2026, 3, 30, 0, 30)


def test_cron_expressions():
    work = Recurrence({"kind": "cron", "cron": "*/15 9-17 * * mon-fri"}, LONDON)
    assert work.next_after(utc(2026, 10, 2, 8, 50)) == utc(2026, 10, 2, 9, 0)  # 10:00 BST
    assert work.next_after(utc(2026, 10, 2, 16, 45)) == utc(2026, 10, 5, 8, 0)  # Monday 09:00 BST
    # Day of month and day of week both given: either may match, as in cron.
    either = Recurrence({"kind": "cron", "cron": "0 9 1 * 1"}, "UTC")
    assert either.next_after(utc(2026, 10, 2, 0, 0)) == utc(2026, 10, 5, 9, 0)  # a Monday
    assert either.next_after(utc(2026, 10, 26, 10, 0)) == utc(2026, 11, 1, 9, 0)  # the 1st, a Sunday
    assert Recurrence({"kind": "cron", "cron": "@daily"}, "UTC").next_after(utc(2026, 1, 1, 0, 0)) == utc(2026, 1, 2)
    assert Recurrence({"kind": "cron", "cron": "0 12 * jan 0"}, "UTC").next_after(utc(2026, 10, 1)) == \
        utc(2027, 1, 3, 12, 0)  # Sunday as 0 and a month name
    assert Recurrence({"kind": "cron", "cron": "0 12 * * 7"}, "UTC").next_after(utc(2026, 10, 1)) == \
        utc(2026, 10, 4, 12, 0)  # Sunday as 7
    leap = Recurrence({"kind": "cron", "cron": "0 0 29 2 *"}, "UTC")
    assert leap.next_after(utc(2026, 10, 1)) == utc(2028, 2, 29)


@pytest.mark.parametrize("repeat, tz, message", [
    ({"kind": "cron", "cron": "0 8 * *"}, LONDON, "five fields"),
    ({"kind": "cron", "cron": "61 8 * * *"}, LONDON, "outside 0-59"),
    ({"kind": "cron", "cron": "0 0 31 2 *"}, LONDON, "never matches a real date"),
    ({"kind": "cron", "cron": "0 8 * * funday"}, LONDON, "not a number"),
    ({"kind": "daily", "at": "25:00"}, LONDON, "HH:MM"),
    ({"kind": "daily", "at": "08:00"}, "Mars/Olympus", "unknown timezone"),
    ({"kind": "daily", "at": "08:00"}, None, "needs a timezone"),
    ({"kind": "daily", "at": "08:00", "days": ["mon"]}, LONDON, "takes only at"),
    ({"kind": "weekly", "at": "08:00", "days": []}, LONDON, "needs days"),
    ({"kind": "weekly", "at": "08:00", "days": ["someday"]}, LONDON, "unknown day"),
    ({"kind": "interval", "interval_s": 5}, None, "at least 30"),
    ({"kind": "hourly"}, LONDON, "kind must be one of"),
])
def test_bad_repeats_are_refused_with_the_reason(repeat, tz, message):
    with pytest.raises(ValueError, match=message):
        Recurrence(repeat, tz)


def test_words_and_missed_runs():
    assert interval_words(3600) == "Every hour"
    assert interval_words(7200) == "Every 2 hours"
    assert interval_words(90) == "Every 90 seconds"
    assert interval_words(1800) == "Every 30 minutes"
    assert Recurrence({"kind": "weekdays", "at": "08:00"}, LONDON).describe() == \
        "Every weekday (Monday to Friday) at 08:00"
    assert Recurrence({"kind": "weekly", "days": ["sat", "mon", "wed"], "at": "07:00"}, LONDON).describe() == \
        "Every Monday, Wednesday and Saturday at 07:00"
    daily = Recurrence({"kind": "daily", "at": "08:00"}, "UTC")
    assert daily.missed_between(utc(2026, 10, 1, 8), utc(2026, 10, 4, 9)) == 3
    assert daily.missed_between(utc(2026, 10, 1, 8), utc(2026, 10, 1, 9)) == 0
    assert Recurrence({"kind": "interval", "interval_s": 600}, None).missed_between(
        utc(2026, 10, 1, 8), utc(2026, 10, 1, 10)) == 12


# Stored schedules and the scheduler --------------------------------------------------------------------------

async def test_calendar_schedule_is_stored_and_fires_at_its_time(jig, events):
    store = jig.store
    s = store.create_schedule(name="Morning news", prompt="Summarise the news", mode=Mode.RESEARCH,
                              repeat={"kind": "daily", "at": "08:00"}, timezone=LONDON)
    rec = Recurrence({"kind": "daily", "at": "08:00"}, LONDON)
    assert s["next_run_at"] == iso(rec.next_after(datetime.fromisoformat(s["created_at"])))
    assert s["repeat"] == {"kind": "daily", "at": "08:00"} and s["timezone"] == LONDON
    assert s["repeat_text"] == "Every day at 08:00" and s["last_task"] is None and s["created_by"] == "user"
    with pytest.raises(ValueError, match="start_in_s only applies"):
        store.create_schedule(name="x", prompt="y", mode=Mode.RESEARCH, repeat={"kind": "daily", "at": "08:00"},
                              timezone=LONDON, start_in_s=60)
    with pytest.raises(ValueError, match="either interval_s or repeat"):
        store.create_schedule(name="x", prompt="y", mode=Mode.RESEARCH)

    # As after two days switched off: due two days ago at 08:00. It runs once, then waits for the next 08:00.
    jig.scheduler.max_concurrent = 0  # queue the job but don't run it; this test is about the schedule
    due = rec.next_after(now() - timedelta(days=3))
    store.update_schedule(s["id"], next_run_at=iso(due))
    jig.scheduler.tick()
    after = store.get_schedule(s["id"])
    assert after["last_task"]["status"] == "queued"
    assert jig.store.get_task(after["last_task_id"])["schedule_id"] == s["id"]
    assert after["next_run_at"] == iso(rec.next_after(datetime.fromisoformat(after["updated_at"])))
    assert datetime.fromisoformat(after["next_run_at"]) > now()
    fired = [json.loads(r["data_json"]) for r in jig.audit.query(kind="schedule.fired")]
    assert [f["schedule_id"] for f in fired] == [s["id"]]
    assert fired[0]["missed_runs"] in (1, 2)  # depending on whether today's 08:00 has passed
    changes = [e.data for e in events if e.type == EventType.SCHEDULE_CHANGED]
    assert {"schedule_id": s["id"], "action": "created"} in changes
    assert {"schedule_id": s["id"], "action": "updated"} in changes

    # A schedule that has run can be deleted; the job it ran is kept.
    store.delete_schedule(s["id"])
    assert jig.store.get_task(after["last_task_id"])["schedule_id"] is None
    with pytest.raises(NotFound):
        store.get_schedule(s["id"])
    assert {"schedule_id": s["id"], "action": "deleted"} in [e.data for e in events
                                                             if e.type == EventType.SCHEDULE_CHANGED]


async def test_pause_resume_and_changing_the_time(jig):
    store = jig.store
    cal = store.create_schedule(name="Digest", prompt="p", mode=Mode.RESEARCH,
                                repeat={"kind": "weekdays", "at": "07:30"}, timezone=LONDON)
    every = store.create_schedule(name="Check", prompt="p", mode=Mode.RESEARCH, interval_s=600)
    assert every["repeat"] == {"kind": "interval", "interval_s": 600.0} and every["timezone"] is None
    for s in (cal, every):
        store.edit_schedule(s["id"], enabled=False)
        store.update_schedule(s["id"], next_run_at=iso(now() - timedelta(hours=5)))  # missed while paused
        assert not store.get_schedule(s["id"])["enabled"]
    # Resumed: the calendar schedule waits for its next time; the interval one is due straight away.
    resumed = store.edit_schedule(cal["id"], enabled=True)
    assert resumed["enabled"] and datetime.fromisoformat(resumed["next_run_at"]) > now()
    assert store.edit_schedule(every["id"], enabled=True)["next_run_at"] <= iso(now())
    # A new repeat takes effect from now.
    changed = store.edit_schedule(every["id"], repeat={"kind": "cron", "cron": "0 6 * * *"}, timezone="Asia/Tokyo")
    assert changed["repeat_text"] == "Cron 0 6 * * *" and changed["timezone"] == "Asia/Tokyo"
    assert changed["interval_s"] == 0
    assert datetime.fromisoformat(changed["next_run_at"]).astimezone(ZoneInfo("Asia/Tokyo")).strftime("%H:%M") == "06:00"
    back = store.edit_schedule(every["id"], interval_s=900)
    assert back["repeat_text"] == "Every 15 minutes" and back["interval_s"] == 900
    with pytest.raises(ValueError, match="HH:MM"):
        store.edit_schedule(cal["id"], repeat={"kind": "daily", "at": "noon"})
    assert store.get_schedule(cal["id"])["repeat"] == {"kind": "weekdays", "at": "07:30"}, "a bad change changes nothing"


async def test_clock_jump_brings_a_calendar_schedule_back(jig):
    store = jig.store
    s = store.create_schedule(name="Daily", prompt="p", mode=Mode.RESEARCH,
                              repeat={"kind": "daily", "at": "08:00"}, timezone=LONDON)
    jig.scheduler.tick()
    store.update_schedule(s["id"], next_run_at=iso(now() + timedelta(days=9)))  # set while the clock was wrong
    wall, mono = jig.scheduler._last_clock
    jig.scheduler._last_clock = (wall + 7200, mono)  # the clock has since gone back two hours
    jig.scheduler.tick()
    soonest = Recurrence(s["repeat"], LONDON).next_after(now())
    assert abs(datetime.fromisoformat(store.get_schedule(s["id"])["next_run_at"]) - soonest) < timedelta(minutes=2)
    jump = json.loads(jig.audit.query(kind="scheduler.clock_jump")[-1]["data_json"])
    assert jump["rescheduled"] == ["Daily"]


# The agent's schedule_create tool ---------------------------------------------------------------------------

INTENT = "The user said: every weekday at 8am, summarise the BBC technology headlines for me."


async def test_agent_schedule_needs_the_users_approval(jig):
    spec = jig.registry.get("schedule_create")
    assert spec.human_only and spec.effect.value == "side_effect" and not spec.allowed_in(Mode.RESEARCH)
    args = {"name": "Tech headlines", "prompt": "Summarise the BBC technology headlines.", "repeat": "weekdays",
            "at": "08:00"}

    outcome, asked = await gated_call(jig, "schedule_create", dict(args), intent=INTENT, on_approval=lambda a: False)
    assert len(asked) == 1, "creating a schedule always asks"
    approval = asked[0]
    assert any(r["rule"] == "human-only-actions" for r in approval["reasons"])
    assert approval["resolved"]["repeats"] == f"Every weekday (Monday to Friday) at 08:00 ({LONDON})"
    first = Recurrence({"kind": "weekdays", "at": "08:00"}, LONDON).next_after(now())
    assert approval["resolved"]["next_runs"].startswith(first.astimezone(ZoneInfo(LONDON)).strftime("%a %d %b %Y, 08:00"))
    assert not outcome.ok and jig.store.list_schedules() == [], "denied, so nothing was saved"

    outcome, asked = await gated_call(jig, "schedule_create", dict(args), intent=INTENT)
    assert outcome.ok, outcome.message_content()
    [s] = jig.store.list_schedules()
    assert s["name"] == "Tech headlines" and s["repeat"] == {"kind": "weekdays", "at": "08:00"}
    assert s["timezone"] == LONDON and s["mode"] == "research" and s["created_by"].startswith("agent (run:")
    result = json.loads(outcome.message_content())
    assert result["schedule_id"] == s["id"] and result["repeats"] == "Every weekday (Monday to Friday) at 08:00"
    listed = (await gated_call(jig, "schedule_list", {}, intent="what is scheduled?"))[0]
    assert "Tech headlines" in listed.message_content()


async def test_agent_schedule_with_bad_times_fails_before_asking(jig):
    outcome, asked = await gated_call(jig, "schedule_create", {
        "name": "x", "prompt": "y", "repeat": "daily", "at": "8am"}, intent=INTENT)
    assert not outcome.ok and asked == []
    assert "HH:MM" in outcome.message_content()
    outcome, asked = await gated_call(jig, "schedule_create", {
        "name": "x", "prompt": "y", "repeat": "interval"}, intent=INTENT)
    assert not outcome.ok and asked == [] and "every_minutes" in outcome.message_content()
    assert jig.store.list_schedules() == []


async def test_asking_in_the_chat_proposes_a_schedule(jig):
    """The real model, asked in plain words, proposes the schedule; it's saved only once the user says yes."""
    approved: list[dict] = []

    async def converse() -> list[dict]:
        items = []
        async for item in jig.chat("Every weekday at 8am, summarise the BBC News technology headlines for me. "
                                   "Please set that up as a schedule."):
            items.append(item)
            if item["type"] == "event" and item["event"]["type"] == "approval.requested":
                a = jig.approvals.get(item["event"]["data"]["approval_id"])
                assert a["tool"] == "schedule_create", a
                assert jig.store.list_schedules() == [], "nothing is saved before the user approves"
                approved.append(a)
                jig.approvals.respond(a["id"], approve=True, note="yes please")
        return items

    items = await converse()
    assert items[-1]["type"] == "done", items[-1]
    assert approved, "the model should have proposed a schedule with schedule_create"
    [s] = await wait_for(lambda: jig.store.list_schedules(), what="the approved schedule")
    assert s["repeat"] in ({"kind": "weekdays", "at": "08:00"}, {"kind": "cron", "cron": "0 8 * * 1-5"}), s["repeat"]
    assert s["timezone"] == LONDON and s["created_by"].startswith("agent")
    assert "technology" in s["prompt"].lower()
