"""Local memory: add, list, search, edit and a real forget."""

from __future__ import annotations

import json

import pytest

from jig.constants import EventType, Mode
from jig.errors import NotFound
from jig.model import ToolCall
from jig.policy.gate import CallContext


async def test_memory_add_search_edit_forget(jig):
    keep = jig.memory.add("Robyn prefers tea to coffee", tags=["preference"])
    gone = jig.memory.add("Robyn's dentist appointment is on Tuesday", tags=["calendar"])

    assert {m["id"] for m in jig.memory.list()} == {keep["id"], gone["id"]}
    assert [m["id"] for m in jig.memory.search("dentist")] == [gone["id"]]
    assert jig.memory.search("tea")[0]["id"] == keep["id"]
    # Porter stemming: "preferences" finds "prefers".
    assert jig.memory.search("preferences")[0]["id"] == keep["id"]

    edited = jig.memory.edit(keep["id"], content="Robyn prefers green tea")
    assert edited["content"] == "Robyn prefers green tea"
    assert jig.memory.search("green")[0]["id"] == keep["id"]

    jig.memory.forget(gone["id"])
    assert jig.memory.search("dentist") == []
    with pytest.raises(NotFound):
        jig.memory.get(gone["id"])
    with pytest.raises(NotFound):
        jig.memory.forget(gone["id"])
    # Gone from the full-text index too, not just hidden.
    assert jig.db.query("SELECT rowid FROM memories_fts WHERE memories_fts MATCH 'dentist'") == []
    assert [m["id"] for m in jig.memory.list()] == [keep["id"]]


async def test_every_memory_change_is_published(jig, events):
    """The web UI refreshes its memory list on memory.changed, so changes made by the agent's own tools must
    publish it too, not only the REST routes."""
    call = ToolCall(id="m1", name="memory_add", arguments_raw=json.dumps({"content": "Robyn is vegetarian"}))
    outcome = await jig.executor.execute(call, CallContext("r_mem", None, Mode.ACTION, "remember a preference"))
    assert outcome.ok, outcome.error
    memory_id = outcome.result["id"]

    jig.memory.edit(memory_id, content="Robyn is vegan")
    jig.memory.forget(memory_id)

    changes = [(e.data["memory_id"], e.data["action"]) for e in events if e.type == EventType.MEMORY_CHANGED]
    assert changes == [(memory_id, "added"), (memory_id, "edited"), (memory_id, "forgotten")]


async def test_memory_search_is_injection_safe(jig):
    jig.memory.add("The cat is called Biscuit")
    assert jig.memory.search('Biscuit" OR content:*') [0]["content"] == "The cat is called Biscuit"
    assert jig.memory.search("!!! ***") == []
