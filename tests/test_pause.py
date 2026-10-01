"""Pause and resume (tasks and the whole agent) and the avatar states they produce, with the real model."""

from __future__ import annotations

import asyncio

import pytest

from jig.constants import ApprovalStatus, AvatarState, EventType, Mode, TaskStatus

from .conftest import wait_for

WRITE_PROMPT = "Use the write_file tool to create greeting.txt containing exactly: Hello from Jig"


def _avatar_states(events, task_id=None):
    return [(e.data["state"], e.data["variant"], e.data["background"]) for e in events
            if e.type == EventType.AVATAR_STATE and (task_id is None or e.data.get("task_id") in (task_id, None))]


def _status(jig, task_id):
    return jig.store.get_task(task_id)["status"]


async def test_pause_while_waiting_for_approval_then_resume(jig, events):
    jig.rules.create(tool="write_file", decision="ask", note="test: always ask before writing")
    jig.scheduler.start()
    task = jig.create_task(title="Write greeting", description=WRITE_PROMPT, mode=Mode.ACTION)
    approval = (await wait_for(lambda: jig.approvals.list(status="pending"), what="a pending approval"))[0]

    paused = await jig.pause_task(task["id"])
    assert paused["status"] == TaskStatus.PAUSED
    assert jig.tracker.current["state"] == AvatarState.PAUSED
    assert jig.approvals.get(approval["id"])["status"] == ApprovalStatus.PENDING, "the approval stays open"
    assert not (jig.sandbox.root / "greeting.txt").exists()
    with pytest.raises(ValueError, match="cannot be paused"):
        await jig.pause_task(task["id"])

    jig.resume_task(task["id"])
    await wait_for(lambda: _status(jig, task["id"]) == TaskStatus.WAITING_APPROVAL, what="the resumed task to wait again")
    assert [a["id"] for a in jig.approvals.list(status="pending")] == [approval["id"]], "the same approval is re-used"
    jig.approvals.respond(approval["id"], approve=True)
    await wait_for(lambda: _status(jig, task["id"]) in (TaskStatus.DONE, TaskStatus.FAILED), what="task to finish")
    final = jig.store.get_task(task["id"])
    assert final["status"] == TaskStatus.DONE, final["error"]
    assert "Hello from Jig" in (jig.sandbox.root / "greeting.txt").read_text(encoding="utf-8")
    assert len(jig.store.runs_for_task(task["id"])) == 1, "the paused run resumed from its checkpoint"

    states = [s for s, _, _ in _avatar_states(events)]
    assert states.index(AvatarState.APPROVAL) < states.index(AvatarState.PAUSED)
    assert AvatarState.SUCCESS in states[states.index(AvatarState.PAUSED):]


async def test_pause_interrupts_a_model_call_and_resumes(jig, events):
    jig.scheduler.start()
    task = jig.create_task(title="Essay", mode=Mode.RESEARCH, description=(
        "Without using any tools, describe the River Thames in three sentences."))
    await wait_for(lambda: any(e.type == EventType.MODEL_START and e.data.get("task_id") == task["id"] for e in events),
                   what="the model call to start")
    paused = await jig.pause_task(task["id"])
    assert paused["status"] == TaskStatus.PAUSED, paused
    run = jig.store.runs_for_task(task["id"])[0]
    assert run["status"] == "running", "a paused run stays resumable"
    assert any(s["status"] == "cancelled" for s in run["step_records"] if s["type"] == "model_call")

    jig.resume_task(task["id"])
    await wait_for(lambda: _status(jig, task["id"]) in (TaskStatus.DONE, TaskStatus.FAILED), what="task to finish")
    final = jig.store.get_task(task["id"])
    assert final["status"] == TaskStatus.DONE, final["error"]
    assert "Thames" in final["result"]


async def test_agent_pause_holds_work_until_resumed(jig, events):
    status = await jig.pause_agent()
    assert status["paused"] is True and jig.store.get_meta("agent_paused") == "1"
    assert jig.tracker.current["state"] == AvatarState.PAUSED
    jig.scheduler.start()
    task = jig.create_task(title="Say ok", description="Reply with the single word: ok", mode=Mode.ACTION)
    await asyncio.sleep(jig.config.runtime.heartbeat_s * 2 + 0.5)
    assert _status(jig, task["id"]) == TaskStatus.QUEUED, "nothing starts while the agent is paused"
    with pytest.raises(ValueError, match="already paused"):
        await jig.pause_agent()

    assert jig.resume_agent()["paused"] is False
    await wait_for(lambda: _status(jig, task["id"]) in (TaskStatus.DONE, TaskStatus.FAILED), what="task to finish")
    assert _status(jig, task["id"]) == TaskStatus.DONE
    assert AvatarState.PAUSED in [s for s, _, _ in _avatar_states(events)]


@pytest.mark.network
async def test_background_research_shows_dimmed_browsing(jig, events):
    jig.scheduler.start()
    task = jig.create_task(title="Check example.com", mode=Mode.RESEARCH, description=(
        "Use the web_fetch tool to fetch https://example.com and then say in one sentence what the page is for."))
    await wait_for(lambda: _status(jig, task["id"]) in (TaskStatus.DONE, TaskStatus.FAILED), what="research to finish")
    assert _status(jig, task["id"]) == TaskStatus.DONE, jig.store.get_task(task["id"])["error"]

    states = _avatar_states(events)
    assert (AvatarState.WORKING.value, "browsing", True) in states, states
    assert not any(s == AvatarState.WORKING and not bg for s, _, bg in states), "research is never foreground work"
    assert not {AvatarState.THINKING, AvatarState.MONITORING} & {s for s, _, _ in states}, \
        "running research stays dimmed working between tool calls"
    assert not {"sleeping", "needs-approval"} & {s for s, _, _ in states}
