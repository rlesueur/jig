"""Approval queue: a task pauses while waiting for the user and resumes afterwards."""

from __future__ import annotations

from jig.constants import ApprovalStatus, AvatarState, EventType, Mode, TaskStatus

from .conftest import audit_kinds, wait_for

PROMPT = "Use the write_file tool to create greeting.txt containing exactly: Hello from Jig"


async def _pending(jig):
    approval = await wait_for(lambda: jig.approvals.list(status="pending"), what="a pending approval")
    return approval[0]


async def test_approval_pauses_and_resumes_task(jig, events):
    jig.rules.create(tool="write_file", decision="ask", note="test: always ask before writing")
    jig.scheduler.start()
    task = jig.create_task(title="Write greeting", description=PROMPT, mode=Mode.ACTION)

    approval = await _pending(jig)
    assert approval["tool"] == "write_file"
    assert any(r["decision"] == "ask" for r in approval["reasons"])
    assert approval["sentinel"]["verdict"] in ("allow", "ask_user")
    assert jig.store.get_task(task["id"])["status"] == TaskStatus.WAITING_APPROVAL
    assert not (jig.sandbox.root / "greeting.txt").exists(), "nothing may happen before approval"

    jig.approvals.respond(approval["id"], approve=True, note="go ahead")
    done = await wait_for(lambda: jig.store.get_task(task["id"])["status"] in (TaskStatus.DONE, TaskStatus.FAILED),
                          what="task to finish")
    assert done
    final = jig.store.get_task(task["id"])
    assert final["status"] == TaskStatus.DONE, final["error"]
    assert "Hello from Jig" in (jig.sandbox.root / "greeting.txt").read_text(encoding="utf-8")
    assert jig.approvals.get(approval["id"])["status"] == ApprovalStatus.APPROVED

    kinds = audit_kinds(jig, task_id=task["id"])
    for kind in ("sentinel.verdict", "approval.requested", "approval.resolved", "tool.result", "task.status"):
        assert kind in kinds, kind
    statuses = [e.data["status"] for e in events if e.type == EventType.TASK_STATUS and e.data["task_id"] == task["id"]]
    assert statuses.index("waiting_approval") < statuses.index("done")
    states = [(e.data["state"], e.data["variant"]) for e in events if e.type == EventType.AVATAR_STATE]
    assert (AvatarState.APPROVAL.value, None) in states
    assert "needs-approval" not in {s for s, _ in states}, "the runtime emits the avatar's canonical names"
    assert (AvatarState.WORKING.value, "writing") in states


async def test_denied_approval_stops_the_action(jig):
    jig.rules.create(tool="write_file", decision="ask")
    jig.scheduler.start()
    task = jig.create_task(title="Write greeting", description=PROMPT, mode=Mode.ACTION)
    approval = await _pending(jig)
    jig.approvals.respond(approval["id"], approve=False, note="not now")

    def finished_denying_retries() -> bool:
        for retry in jig.approvals.list(status="pending"):
            jig.approvals.respond(retry["id"], approve=False, note="still no")
        return jig.store.get_task(task["id"])["status"] in (TaskStatus.DONE, TaskStatus.FAILED)

    await wait_for(finished_denying_retries, what="task to finish")
    assert not (jig.sandbox.root / "greeting.txt").exists()
    runs = jig.store.runs_for_task(task["id"])
    denied = [s for s in runs[-1]["step_records"] if s["type"] == "tool_call" and s["name"] == "write_file"]
    assert denied and denied[0]["output"]["error_type"] == "ApprovalDenied"
