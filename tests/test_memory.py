"""Local memory: add, list, search, edit and a real forget."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

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


async def test_conversations_and_tasks_are_given_the_saved_memories(jig):
    """A saved preference must shape an everyday request in a new conversation, without the model having to
    think of searching for it; edits and forgets apply to the next message."""
    ask = "Suggest a dinner"
    assert "What you remember about the user" not in jig._turn_context(ask)

    older = jig.memory.add("Robyn cycles to work")
    vegan = jig.memory.add("Robyn is   vegetarian")
    context = jig._turn_context(ask)
    assert "What you remember about the user" in context
    assert context.index("- Robyn is vegetarian") < context.index("- Robyn cycles to work")

    jig.memory.edit(vegan["id"], content="Robyn is vegan")
    assert "- Robyn is vegan" in jig._turn_context(ask)
    assert "vegetarian" not in jig._turn_context(ask)

    jig.memory.forget(vegan["id"])
    jig.memory.forget(older["id"])
    assert "What you remember about the user" not in jig._turn_context(ask)
    # The memories are never in the system prompt, which stays the same whatever is remembered.
    assert "Robyn" not in jig._system_prompt(Mode.ACTION)


async def test_with_many_memories_the_newest_and_the_matching_ones_are_given(jig):
    for i in range(80):
        jig.memory.add(f"Robyn noted fact number {i} about the garden shed and its {i} plant pots")
    old = jig.memory.add("Robyn's passport number expires in March 2031")
    for i in range(80, 140):
        jig.memory.add(f"Robyn noted fact number {i} about the garden shed and its {i} plant pots")
    newest = jig.memory.list(limit=1)[0]
    jig.config = replace(jig.config, runtime=replace(jig.config.runtime, memory_prompt="relevant"))

    context = jig._turn_context("When does my passport expire?")
    assert old["content"] in context, "a memory that matches the message is given, however old"
    assert newest["content"] in context, "the newest memories are always given"
    assert f"of {jig.memory.count()} saved memories are shown" in context
    assert old["content"] not in jig._turn_context("Plan my week"), "a memory that matches nothing is not"

    jig.config = replace(jig.config, runtime=replace(jig.config.runtime, memory_prompt="recent"))
    assert old["content"] not in jig._turn_context("When does my passport expire?"), "recent: newest only"


async def test_memory_update_changes_a_memory_after_review(jig):
    memory = jig.memory.add("Robyn's dentist is Dr Patel")
    call = ToolCall(id="u1", name="memory_update", arguments_raw=json.dumps(
        {"memory_id": memory["id"], "content": "Robyn's dentist is Dr Okafor"}))
    running = asyncio.create_task(jig.executor.execute(call, CallContext(
        "r_upd", None, Mode.ACTION, "User message: my dentist is now Dr Okafor, please update your memory")))
    # The Sentinel may ask the user; the user here says yes.
    while not running.done():
        for approval in jig.approvals.list(status="pending"):
            assert approval["tool"] == "memory_update"
            jig.approvals.respond(approval["id"], approve=True)
        await asyncio.sleep(0.25)
    outcome = running.result()
    assert outcome.ok, outcome.error
    assert jig.memory.get(memory["id"])["content"] == "Robyn's dentist is Dr Okafor"
    assert outcome.policy["sentinel"], "memory_update is reviewed, as memory_forget is"

    research = await jig.executor.execute(
        ToolCall(id="u2", name="memory_update", arguments_raw=json.dumps({"memory_id": memory["id"],
                                                                          "content": "x"})),
        CallContext("r_upd2", None, Mode.RESEARCH, "update"))
    assert not research.ok and jig.memory.get(memory["id"])["content"] == "Robyn's dentist is Dr Okafor"


def test_memory_prompt_is_bounded():
    from jig.agent.prompts import memory_prompt

    long = memory_prompt([{"content": "x" * 2000}])
    assert long.startswith("What you remember about the user")
    assert long.endswith("- " + "x" * 500 + "…")
    many = memory_prompt([{"content": f"fact {i} " + "y" * 400} for i in range(100)])
    assert 10 <= many.count("\n- ") < 20
    assert "fact 0 " in many and "fact 99 " not in many
    assert memory_prompt([]) == ""


async def test_memory_search_is_injection_safe(jig):
    jig.memory.add("The cat is called Biscuit")
    assert jig.memory.search('Biscuit" OR content:*') [0]["content"] == "The cat is called Biscuit"
    assert jig.memory.search("!!! ***") == []
