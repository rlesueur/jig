"""A background task ends with a checked record of how it went: done, partial or could_not, with what it rests on."""

from __future__ import annotations

import json

from jig.constants import Mode, TaskStatus

from .conftest import audit_kinds


async def test_a_task_that_is_done_records_how_and_on_what(jig):
    (jig.sandbox.root / "hello.txt").write_text("The meeting moved to Thursday at 3pm.", encoding="utf-8")
    task = jig.create_task(title="Read a note", mode=Mode.RESEARCH,
                           description="Read hello.txt in the workspace and say when the meeting is.")
    await jig.run_task(task["id"])
    task = jig.store.get_task(task["id"])
    assert task["status"] == TaskStatus.DONE and "thursday" in task["result"].lower(), task
    outcome = task["outcome"]
    assert outcome["status"] == "done", outcome
    assert outcome["summary"].strip() and outcome["basis"].strip()
    assert "hello.txt" in outcome["basis"] or "read_file" in outcome["basis"], outcome
    assert "task.outcome" in audit_kinds(jig, task_id=task["id"])
    recorded = [json.loads(r["data_json"]) for r in jig.audit.query(kind="task.outcome", task_id=task["id"])]
    assert recorded[0]["status"] == "done" and "summary" not in recorded[0], "the audit keeps no content"


async def test_a_task_that_could_not_be_done_fails_with_its_own_account(jig):
    task = jig.create_task(title="Total the figures", mode=Mode.RESEARCH,
                           description="Read quarterly-figures.csv in the workspace and report the total of its "
                                       "amount column. That file is the only source; there is no other way to get "
                                       "these figures.")
    await jig.run_task(task["id"])
    task = jig.store.get_task(task["id"])
    assert task["outcome"]["status"] == "could_not", task
    assert task["status"] == TaskStatus.FAILED and task["error"].startswith("The task reports it could not be done")
    assert task["result"].strip(), "what the task said is kept"


def test_a_later_task_is_told_how_the_earlier_one_ended(jig):
    goal = jig.store.create_goal(title="Trip", description="Plan a trip to Bath")
    first = jig.store.create_task(title="Find trains", description="Find trains to Bath", mode=Mode.RESEARCH,
                                  goal_id=goal["id"])
    second = jig.store.create_task(title="Book", description="Suggest a train", mode=Mode.RESEARCH,
                                   goal_id=goal["id"], depends_on=[first["id"]])
    outcome = {"status": "partial", "summary": "Found Saturday trains only; Sunday's timetable was unavailable.",
               "basis": "web_fetch of the GWR timetable page"}
    jig.store.update_task(first["id"], status=TaskStatus.DONE, result="Saturday: 09:15 and 11:15.",
                          outcome_json=json.dumps(outcome))
    assert jig.store.get_task(first["id"])["outcome"] == outcome
    prompt, _ = jig._task_prompt(jig.store.get_task(second["id"]))
    assert "Saturday: 09:15 and 11:15." in prompt
    assert ("That task recorded that it ended partial: Found Saturday trains only; Sunday's timetable was "
            "unavailable. (based on: web_fetch of the GWR timetable page)") in prompt
