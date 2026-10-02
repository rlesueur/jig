"""Jig's private notes: view, edit and delete one, delete them all, and the text really leaving the database files
(and never being copied into the append-only audit log), against real services."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from jig.api import create_app
from jig.constants import EventType, Mode
from jig.model import ToolCall
from jig.policy.gate import CallContext


def _files(config) -> bytes:
    path = config.db_path
    wal = path.with_name(path.name + "-wal")
    return path.read_bytes() + (wal.read_bytes() if wal.exists() else b"")


def _checkpoint(db) -> None:
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")


async def test_edit_delete_and_wipe_notes_leave_nothing_in_the_files(jig, events):
    keep = jig.store.add_note(title="Shopping", body="Oat milk and bread", task_id=None)
    gone = jig.store.add_note(title="Pelicanquartz plan", body="Meet at the pelicanquartz cafe", task_id="t_1")
    edited = jig.store.add_note(title="Draft", body="Old wording marmosetvelvet", task_id=None)
    _checkpoint(jig.db)
    before = _files(jig.config)
    assert b"pelicanquartz" in before and b"marmosetvelvet" in before

    assert jig.store.get_note(gone["id"])["body"] == "Meet at the pelicanquartz cafe"
    jig.store.delete_note(gone["id"])
    out = jig.store.edit_note(edited["id"], body="New wording")
    assert out["body"] == "New wording" and out["title"] == "Draft" and out["updated_at"]
    after = _files(jig.config)
    assert b"pelicanquartz" not in after, "a deleted note's text is still in the database files"
    assert b"marmosetvelvet" not in after, "an edited note's old text is still in the database files"
    assert [n["id"] for n in jig.store.list_notes()] == [edited["id"], keep["id"]]

    assert jig.store.wipe_notes() == {"deleted": 2, "wal_cleared": True}
    assert jig.store.list_notes() == [] and jig.store.count_notes() == 0
    assert b"Oat milk" not in _files(jig.config) and b"New wording" not in _files(jig.config)
    seen = [(e.type, e.data) for e in events if e.type == EventType.NOTE_CHANGED]
    assert seen == [(EventType.NOTE_CHANGED, {"note_id": i, "action": a}) for i, a in
                    [(keep["id"], "added"), (gone["id"], "added"), (edited["id"], "added"),
                     (gone["id"], "deleted"), (edited["id"], "edited")]] + \
        [(EventType.NOTE_CHANGED, {"note_id": None, "action": "wiped"})]
    # Ids are never reused, so the audit log's references stay unambiguous.
    assert jig.store.add_note(title="t", body="b", task_id=None)["id"] == edited["id"] + 1


async def test_the_agents_note_text_is_never_copied_into_the_audit_log(jig):
    secret = "Robyn's spare key is under the heronlantern pot"
    call = ToolCall(id="n1", name="note_write", arguments_raw=json.dumps({"title": "Spare key", "body": secret}))
    outcome = await jig.executor.execute(call, CallContext("r_note", None, Mode.RESEARCH, "remember the key"))
    assert outcome.ok and jig.store.list_notes()[0]["body"] == secret
    listed = await jig.executor.execute(ToolCall(id="n2", name="note_list", arguments_raw="{}"),
                                        CallContext("r_note", None, Mode.RESEARCH, "list notes"))
    assert listed.ok and secret in listed.message_content(), "the agent itself still reads its notes"
    remembered = await jig.executor.execute(
        ToolCall(id="m1", name="memory_add", arguments_raw=json.dumps({"content": "Robyn's PIN hint is egretmosaic"})),
        CallContext("r_note", None, Mode.RESEARCH, "remember"))
    assert remembered.ok

    audit = [{**a, "data": json.loads(a["data_json"])} for a in jig.audit.query(limit=5000)]
    dump = json.dumps(audit)
    for word in ("heronlantern", "Spare key", "egretmosaic"):
        assert word not in dump, f"{word!r} was copied into the audit log"
    calls = {a["data"]["tool"]: a["data"] for a in audit if a["kind"] == "tool.call"}
    assert calls["note_write"]["args"] == {"title": "[private, 9 characters]", "body": f"[private, {len(secret)} characters]"}
    results = {a["data"]["tool"]: a["data"]["result_preview"] for a in audit if a["kind"] == "tool.result"}
    note_id = jig.store.list_notes()[0]["id"]
    assert results["note_write"] == f"[private: id {note_id}]"
    assert results["note_list"] == f"[private: 1 found, ids [{note_id}]]"
    tools = {t.name: t.describe() for t in jig.registry.all()}
    assert tools["note_write"]["private"] and tools["memory_search"]["private"] and not tools["web_fetch"]["private"]

    # So once the note and the memory are deleted, their text is nowhere in the database files.
    jig.store.delete_note(note_id)
    jig.memory.forget(remembered.result["id"])
    after = _files(jig.config)
    assert b"heronlantern" not in after and b"egretmosaic" not in after


def test_notes_api(config):
    app = create_app(config)
    token = app.state.auth.tokens.get()
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        jig = app.state.jig
        a = jig.store.add_note(title="Train times", body="The 07:42 to Leeds avocetsprocket", task_id=None)
        b = jig.store.add_note(title="Ideas", body="Write about kestrelbanjo", task_id="t_x")
        assert [n["id"] for n in client.get("/notes").json()] == [b["id"], a["id"]]
        assert client.get(f"/notes/{a['id']}").json()["body"] == "The 07:42 to Leeds avocetsprocket"
        assert client.get("/notes/9999").status_code == 404

        r = client.patch(f"/notes/{a['id']}", json={"title": "Trains", "body": "The 08:10 to York"})
        assert r.status_code == 200 and r.json()["title"] == "Trains" and r.json()["body"] == "The 08:10 to York"
        assert client.patch(f"/notes/{a['id']}", json={"title": "   "}).status_code == 400
        assert client.patch(f"/notes/{a['id']}", json={"body": ""}).status_code == 422
        assert client.patch("/notes/9999", json={"title": "x"}).status_code == 404

        assert client.delete(f"/notes/{b['id']}").status_code == 204
        assert client.delete(f"/notes/{b['id']}").status_code == 404
        refused = client.post("/notes/wipe", json={})
        assert refused.status_code == 400 and "No note was deleted" in refused.json()["error"]
        assert client.get("/notes").json()[0]["id"] == a["id"]
        assert client.post("/notes/wipe", json={"confirm": True}).json() == {"deleted": 1, "wal_cleared": True}
        assert client.get("/notes").json() == []

        # Forget everything can take the notes with it.
        jig.store.add_note(title="n", body="ptarmiganfold", task_id=None)
        client.post("/memory", json={"content": "Likes rowing"})
        out = client.post("/memory/wipe", json={"confirm": True, "notes": True}).json()
        assert out == {"forgotten": 1, "notes_deleted": 1, "wal_cleared": True}
        assert client.post("/memory/wipe", json={"confirm": True}).json() == {"forgotten": 0, "wal_cleared": True}

        for word in (b"avocetsprocket", b"kestrelbanjo", b"07:42 to Leeds", b"08:10 to York", b"ptarmiganfold"):
            assert word not in _files(config), f"{word!r} is still in the database files"
        audit = client.get("/audit", params={"kind": "note"}).json()
        assert [(x["kind"], x["data"].get("note_id"), x["data"].get("count")) for x in audit] == [
            ("note.edited", a["id"], None), ("note.deleted", b["id"], None), ("note.wiped", None, 1),
            ("note.wiped", None, 1)]
        assert audit[0]["data"]["fields"] == ["body", "title"] and {x["actor"] for x in audit} == {"user"}
        dump = json.dumps(client.get("/audit", params={"limit": 1000}).json())
        for word in ("avocetsprocket", "kestrelbanjo", "Leeds", "York", "Trains", "ptarmiganfold"):
            assert word not in dump, f"{word!r} was copied into the audit log"
