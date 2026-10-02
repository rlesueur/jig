"""Deleting conversations and job results, one at a time and all at once: every copy of their text leaves the
database files (runs, run steps, approvals with Sentinel's review, the recent-events cache), running ones are
refused until stopped, and the append-only audit log never holds their words. Against the real model."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from jig.api import create_app
from jig.audit import AuditLog
from jig.constants import EventType, GoalStatus, Mode, RunStatus, TaskStatus
from jig.db import Database
from jig.errors import CannotDelete

from .conftest import wait_for


def _files(config) -> bytes:
    path = config.db_path
    wal = path.with_name(path.name + "-wal")
    return path.read_bytes() + (wal.read_bytes() if wal.exists() else b"")


def _recent(jig) -> str:
    return json.dumps([e.as_dict() for e in jig.bus.recent], default=str)


def _audit(jig) -> str:
    return json.dumps(jig.audit.query(limit=10_000))


async def _chat(jig, message: str, session_id: str | None = None) -> dict:
    items = [item async for item in jig.chat(message, session_id=session_id)]
    assert items[-1]["type"] == "done", items[-1]
    return items[-1]


async def test_deleting_a_finished_task_removes_its_text_everywhere(jig, events):
    jig.rules.create(tool="write_file", decision="ask", note="test: always ask before writing")
    jig.scheduler.start()
    task = jig.create_task(title="Write the wombatlattice file", mode=Mode.ACTION,
                           description="Use the write_file tool to create wombatlattice.txt containing exactly: "
                                       "narwhalbiscuit")
    approval = (await wait_for(lambda: jig.approvals.list(status="pending"), what="a pending approval"))[0]
    assert approval["args"]["content"] == "narwhalbiscuit" and approval["sentinel"]["reason"]

    with pytest.raises(CannotDelete, match="stop it first"):
        jig.delete_task(task["id"])
    jig.approvals.respond(approval["id"], approve=True, note="fine by me capybaraquill")
    await wait_for(lambda: jig.store.get_task(task["id"])["status"] in (TaskStatus.DONE, TaskStatus.FAILED),
                   what="the task to finish")
    await wait_for(lambda: task["id"] not in jig.scheduler.running_task_ids, what="the scheduler to let go")
    assert jig.store.get_task(task["id"])["status"] == TaskStatus.DONE

    jig.db.checkpoint()
    before = _files(jig.config)
    for word in (b"wombatlattice", b"narwhalbiscuit", b"capybaraquill"):
        assert word in before, word
    assert "narwhalbiscuit" in _recent(jig), "approval events carry the arguments"
    for word in ("wombatlattice", "narwhalbiscuit", "capybaraquill"):
        assert word not in _audit(jig), f"{word!r} was copied into the audit log"

    out = jig.delete_task(task["id"])
    assert out["tasks"] == 1 and out["runs"] >= 1 and out["approvals"] == 1 and out["wal_cleared"] is True
    after = _files(jig.config)
    for word in (b"wombatlattice", b"narwhalbiscuit", b"capybaraquill"):
        assert word not in after, f"{word!r} is still in the database files"
    assert "narwhalbiscuit" not in _recent(jig) and task["id"] not in _recent(jig)
    assert [e.data for e in events if e.type == EventType.HISTORY_CHANGED] == [{"action": "task.deleted"}]
    for table in ("runs", "approvals"):
        assert not jig.db.query(f"SELECT id FROM {table} WHERE task_id = ?", (task["id"],)), table
    with pytest.raises(Exception, match="does not exist"):
        jig.store.get_task(task["id"])
    # The file the job made is the user's, in the sandbox folder; deleting the job's record leaves it alone.
    assert (jig.sandbox.root / "wombatlattice.txt").read_text(encoding="utf-8") == "narwhalbiscuit"


async def test_deleting_a_goal_takes_its_plan_and_tasks(jig):
    goal = jig.create_goal(description="Find out why the sky is blue, and write the answer for a curious "
                                       "child called Marmaduke Quillfeather")
    await wait_for(lambda: jig.store.get_goal(goal["id"])["status"] != GoalStatus.PLANNING, what="planning")
    assert jig.store.get_goal(goal["id"])["status"] == GoalStatus.ACTIVE
    tasks = jig.store.list_tasks(goal_id=goal["id"])
    assert tasks and jig.db.query("SELECT id FROM runs WHERE kind = 'plan' AND goal_id = ?", (goal["id"],))

    with pytest.raises(CannotDelete, match="stop it first"):
        jig.delete_goal(goal["id"])
    with pytest.raises(CannotDelete, match="delete the goal"):
        jig.delete_task(tasks[0]["id"])
    jig.cancel_goal(goal["id"])
    out = jig.delete_goal(goal["id"])
    assert out["goals"] == 1 and out["tasks"] == len(tasks) and out["runs"] == 1
    assert b"Quillfeather" not in _files(jig.config)
    assert not jig.db.query("SELECT id FROM runs WHERE goal_id = ?", (goal["id"],))


async def test_conversations_one_at_a_time_and_all_at_once(jig):
    first = await _chat(jig, "Please answer in one short sentence: what colour is a ripe lemon? (code word "
                             "dugongtrellis)")
    second = await _chat(jig, "Say hello to Persimmonwhistle in five words or fewer.")
    # A conversation whose first reply failed has a run but no saved session; its words must go too.
    orphan = jig.store.create_run(kind="chat", mode=Mode.ACTION, session_id="sess_orphan")
    jig.store.checkpoint_run(orphan, [{"role": "user", "content": "the bandicootmarble question"}], 0)
    jig.store.finish_run(orphan, status=RunStatus.FAILED, error="ModelError: the model server went away")

    listed = jig.store.list_conversations()
    assert [c["id"] for c in listed] == ["sess_orphan", second["session_id"], first["session_id"]]
    assert listed[2]["title"].startswith("Please answer in one short sentence") and listed[2]["messages"] == 2
    assert listed[0] == {**listed[0], "title": "the bandicootmarble question", "messages": 1, "replying": False}
    said = jig.store.get_conversation(first["session_id"], with_messages=True)["transcript"]
    assert [m["role"] for m in said] == ["user", "assistant"] and "dugongtrellis" in said[0]["text"]

    # A follow-up turn joins the same conversation.
    await _chat(jig, "And a ripe lime? One word.", session_id=first["session_id"])
    assert jig.store.get_conversation(first["session_id"])["messages"] == 4

    # While Jig is replying, the conversation can't be deleted.
    busy = jig.store.create_run(kind="chat", mode=Mode.ACTION, session_id=second["session_id"])
    with pytest.raises(CannotDelete, match="still replying"):
        jig.store.delete_conversation(second["session_id"])
    jig.store.finish_run(busy, status=RunStatus.DONE, final="ok")

    jig.db.checkpoint()
    assert b"dugongtrellis" in _files(jig.config)
    out = jig.store.delete_conversation(first["session_id"])
    assert out["conversations"] == 1 and out["runs"] == 2 and out["wal_cleared"] is True
    assert b"dugongtrellis" not in _files(jig.config) and first["run_id"] not in _recent(jig)
    assert b"Persimmonwhistle" in _files(jig.config), "only the one conversation goes"

    out = jig.store.wipe_conversations()
    assert out == {"conversations": 2, "runs": 3, "approvals": 0, "kept_replying": 0, "wal_cleared": True}
    files = _files(jig.config)
    assert b"Persimmonwhistle" not in files and b"bandicootmarble" not in files
    assert jig.store.list_conversations() == [] and jig.store.count_conversations() == 0
    assert jig.db.one("PRAGMA freelist_count")["freelist_count"] == 0, "the bulk delete rebuilds the file"
    for word in ("dugongtrellis", "Persimmonwhistle", "bandicootmarble", "lemon"):
        assert word not in _audit(jig), f"{word!r} was copied into the audit log"


async def test_wipe_jobs_keeps_unfinished_ones(jig):
    done = jig.store.create_task(title="Old job ocelotgrammar", description="d", mode=Mode.RESEARCH)
    jig.store.update_task(done["id"], status=TaskStatus.DONE, result="The answer was ocelotgrammar")
    run = jig.store.create_run(kind="task", mode=Mode.RESEARCH, task_id=done["id"])
    jig.store.finish_run(run, status=RunStatus.DONE, final="ocelotgrammar")
    waiting = jig.store.create_task(title="Later job", description="tapirlanyard", mode=Mode.RESEARCH, delay_s=3600)
    needed = jig.store.create_task(title="Needed", description="d", mode=Mode.RESEARCH)
    jig.store.update_task(needed["id"], status=TaskStatus.DONE, result="salamanderkiln")
    jig.store.create_task(title="Needs it", description="d", mode=Mode.RESEARCH, depends_on=[needed["id"]],
                          delay_s=3600)
    # A planning run from before runs had a goal_id is found through its first step.
    goal = jig.store.create_goal(title="Old goal", description="lemurtoffee")
    jig.store.update_goal(goal["id"], status=GoalStatus.DONE, result="lemurtoffee result")
    old_plan = jig.store.create_run(kind="plan", mode=Mode.RESEARCH)
    jig.store.start_step(old_plan, 1, "model_call", "m", {"goal_id": goal["id"]})
    jig.store.finish_run(old_plan, status=RunStatus.DONE, final="lemurtoffee plan")

    out = jig.wipe_jobs()
    assert out == {"tasks": 1, "goals": 1, "runs": 2, "approvals": 0, "kept_unfinished": 3, "wal_cleared": True}
    files = _files(jig.config)
    assert b"ocelotgrammar" not in files and b"lemurtoffee" not in files
    assert b"tapirlanyard" in files and b"salamanderkiln" in files, "unfinished work and what it needs are kept"
    assert {t["id"] for t in jig.store.list_tasks()} >= {waiting["id"], needed["id"]}


def test_api_delete_and_forget_everything(config):
    app = create_app(config)
    token = app.state.auth.tokens.get()
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        jig = app.state.jig
        jig.store.save_session("sess_a", [{"role": "user", "content": "about the gannetpebble"},
                                          {"role": "assistant", "content": "Noted."}])
        jig.store.save_session("sess_b", [{"role": "user", "content": "about the puffinladder"}])
        task = jig.store.create_task(title="Job grebeharp", description="d", mode=Mode.RESEARCH, delay_s=3600)
        jig.store.update_task(task["id"], status=TaskStatus.DONE, result="grebeharp found")
        unfinished = jig.store.create_task(title="Still to do", description="d", mode=Mode.RESEARCH, delay_s=3600)
        jig.memory.add("Robyn likes the shagcormorant cafe", source="user")
        jig.store.add_note(title="n", body="kittiwakemuffin", task_id=None)

        listed = client.get("/sessions").json()
        assert [(c["id"], c["title"], c["messages"]) for c in listed] == [
            ("sess_b", "about the puffinladder", 1), ("sess_a", "about the gannetpebble", 2)]
        assert client.get("/sessions/sess_a/transcript").json()["transcript"][1] == {"role": "assistant",
                                                                                   "text": "Noted."}
        assert client.delete("/sessions/sess_a").status_code == 204
        assert client.delete("/sessions/sess_a").status_code == 404
        assert client.get("/sessions/sess_a/transcript").status_code == 404

        refused = client.delete(f"/tasks/{unfinished['id']}")
        assert refused.status_code == 409 and "stop it first" in refused.json()["error"]
        assert client.post("/jobs/wipe", json={}).status_code == 400
        assert client.post("/sessions/wipe", json={}).status_code == 400
        refused = client.post("/forget", json={"confirm": False})
        assert refused.status_code == 400 and "Nothing was deleted" in refused.json()["error"]
        assert b"grebeharp" in _files(config)

        out = client.post("/forget", json={"confirm": True}).json()
        assert out == {"memories": 1, "notes": 1, "conversations": 1, "goals": 0, "tasks": 1, "kept_replying": 0,
                       "kept_unfinished": 1, "wal_cleared": True}
        files = _files(config)
        for word in (b"gannetpebble", b"puffinladder", b"grebeharp", b"shagcormorant", b"kittiwakemuffin"):
            assert word not in files, f"{word!r} is still in the database files"
        assert client.get("/sessions").json() == [] and client.get("/memory").json() == []
        assert [t["id"] for t in client.get("/tasks").json()] == [unfinished["id"]]

        audit = [{**a, "data": json.loads(a["data_json"])} for a in jig.audit.query(limit=1000)]
        kinds = [a["kind"] for a in audit]
        assert "conversation.deleted" in kinds and kinds[-1] == "everything.forgotten"
        assert audit[-1]["data"]["conversations"] == 1 and audit[-1]["actor"] == "user"
        dump = json.dumps(audit)
        for word in ("gannetpebble", "puffinladder", "grebeharp", "shagcormorant", "kittiwakemuffin"):
            assert word not in dump, f"{word!r} was copied into the audit log"
        assert client.get("/audit/older-with-content").json()["count"] == 0


def test_older_audit_entries_are_counted_not_rewritten(tmp_path):
    db = Database(tmp_path / "jig.db")
    # As an older Jig wrote them, before the no-content rule.
    for kind, data in (("tool.call", {"args": {"query": "my sister's flat"}}), ("run.end", {"final_preview": "Hi"}),
                       ("runtime.start", {})):
        db.execute("INSERT INTO audit(ts, kind, actor, summary, data_json) VALUES ('2026-01-01T00:00:00Z', ?, "
                   "'agent', 's', ?)", (kind, json.dumps(data)))
    audit = AuditLog(db)
    audit.record("tool.call", "web_search requested", args={"query": "[5 characters]"})
    older = audit.older_entries_with_content()
    assert older == {"count": 2, "first": "2026-01-01T00:00:00Z", "last": "2026-01-01T00:00:00Z", "before_id": 4}
    assert "my sister's flat" in json.dumps(audit.query()), "older entries are left exactly as they were"
    # Reopening keeps the same boundary.
    assert AuditLog(db).older_entries_with_content() == older
    db.close()
