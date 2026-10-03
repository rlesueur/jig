"""The multi-step tool-calling loop, end to end against the real model."""

from __future__ import annotations

from jig.agent.loop import RunSpec
from jig.agent.prompts import CONTEXT_KEY, agent_system_prompt
from jig.constants import AvatarState, EventType, Mode, RunStatus

from .conftest import audit_kinds


async def test_tool_calling_loop_end_to_end(jig, events):
    (jig.sandbox.root / "hello.txt").write_text("Hello there", encoding="utf-8")
    messages = [
        {"role": "system", "content": agent_system_prompt(Mode.ACTION, "Europe/London")},
        {"role": "user", "content": "Call the current_time tool for Europe/London and the list_files tool for the "
                                    "workspace root, then tell me the time and the names of the files."},
    ]
    spec = RunSpec(kind="chat", mode=Mode.ACTION, intent="Tell me the time and list my workspace files.")
    result = await jig.agent.run(messages, spec)

    assert result.status == RunStatus.DONE
    assert result.steps >= 2
    assert "hello.txt" in result.final

    run = jig.store.get_run(result.run_id)
    tool_steps = [s for s in run["step_records"] if s["type"] == "tool_call"]
    names = {s["name"] for s in tool_steps}
    assert {"current_time", "list_files"} <= names
    assert all(s["status"] == "ok" for s in tool_steps)
    model_steps = [s for s in run["step_records"] if s["type"] == "model_call"]
    assert model_steps and all(s["output"]["finish_reason"] in ("tool_calls", "stop") for s in model_steps)
    # Reasoning is optional (not every server sends it) but is always recorded when it is.
    assert all(isinstance(s["output"]["reasoning"], str) for s in model_steps)
    if jig.capabilities["agent"]["reasoning_reported"]:
        assert any(s["output"]["reasoning"] for s in model_steps)

    kinds = audit_kinds(jig, run_id=result.run_id)
    assert "model.call" in kinds and "tool.call" in kinds and "tool.result" in kinds and "run.end" in kinds

    states = [(e.data["state"], e.data["variant"]) for e in events if e.type == EventType.AVATAR_STATE]
    assert (AvatarState.THINKING.value, None) in states
    assert (AvatarState.WORKING.value, "scheduling") in states or (AvatarState.WORKING.value, "browsing") in states
    assert states[-1][0] == AvatarState.SUCCESS.value


def test_a_stopped_turn_keeps_only_tool_calls_that_got_a_result():
    from jig.runtime import answered_only
    call = lambda i: {"id": i, "type": "function", "function": {"name": "current_time", "arguments": "{}"}}  # noqa: E731
    messages = [{"role": "user", "content": "hi"},
                {"role": "assistant", "content": "", "tool_calls": [call("a"), call("b")]},
                {"role": "tool", "tool_call_id": "a", "content": "{}"},
                {"role": "assistant", "content": "", "tool_calls": [call("c")]},
                {"role": "assistant", "content": "Looking", "tool_calls": [call("d")]}]
    kept = answered_only(messages)
    assert [m["role"] for m in kept] == ["user", "assistant", "tool", "assistant"]
    assert [c["id"] for c in kept[1]["tool_calls"]] == ["a"] and "tool_calls" not in kept[3]
    assert kept[3]["content"] == "Looking" and len(messages[1]["tool_calls"]) == 2, "the input is not changed"


async def test_a_turn_at_the_step_limit_says_what_it_did_and_can_be_continued(jig):
    (jig.sandbox.root / "hello.txt").write_text("Hello there", encoding="utf-8")
    ask = ("Call the current_time tool for Europe/London and the list_files tool for the workspace root, then tell "
           "me the time and the names of the files.")
    jig.agent.max_steps = 1
    items = [item async for item in jig.chat(ask)]
    done = items[-1]
    assert done["type"] == "done" and done["limit_reached"] and done["steps"] == 2, done
    assert done["final"].strip(), "the user is given the model's own account, not a bare error"
    run = jig.store.get_run(done["run_id"])
    model_steps = [s for s in run["step_records"] if s["type"] == "model_call"]
    assert model_steps[-1]["input"]["tools"] == [], "the last call offers no tools, so it can take no action"
    tool_steps = [s for s in run["step_records"] if s["type"] == "tool_call"]
    assert all(s["idx"] == 1 for s in tool_steps), "nothing ran after the limit"
    assert "step limit" in run["error"] and run["status"] == "done"
    assert "run.step_limit_calls_ignored" not in audit_kinds(jig, run_id=done["run_id"])

    sid = done["session_id"]
    kept = jig.store.get_session(sid)
    assert kept[0]["content"] == ask, "saved as the user wrote it, with Jig's context kept apart"
    assert "<jig-context>" in kept[0][CONTEXT_KEY]
    assert any(m["role"] == "tool" for m in kept), "what Jig did before it stopped is kept"
    assert not any("[Jig budget]" in m["content"] for m in kept if m["role"] == "tool")
    assert not any("step limit" in m["content"] and m["role"] == "user" for m in kept), \
        "the step-limit instruction is not kept as if the user had said it"
    assert kept[-1] == {"role": "assistant", "content": done["final"]}
    jig.agent.max_steps = 8
    more = [item async for item in jig.chat("Please carry on and finish.", session_id=sid)]
    assert more[-1]["type"] == "done" and "hello.txt" in more[-1]["final"], more[-1]
    assert jig.store.get_session(sid)[0]["content"] == ask


async def test_a_long_conversation_still_gets_the_whole_step_limit_each_turn(jig):
    earlier = [m for i in range(jig.agent.max_steps + 2) for m in (
        {"role": "user", "content": f"Question {i}: say a number."}, {"role": "assistant", "content": str(i)})]
    jig.store.save_session("sess_long", earlier)
    items = [item async for item in jig.chat("Use current_time for Europe/London, then tell me the hour.",
                                             session_id="sess_long")]
    assert items[-1]["type"] == "done" and not items[-1].get("limit_reached"), items[-1]
    run = jig.store.get_run(items[-1]["run_id"])
    assert any(s["name"] == "current_time" for s in run["step_records"] if s["type"] == "tool_call")


async def test_a_task_at_the_step_limit_ends_as_its_checked_outcome_says(jig):
    (jig.sandbox.root / "notes.txt").write_text("Buy milk", encoding="utf-8")
    jig.agent.max_steps = 1
    task = jig.create_task(title="Read and time", mode=Mode.RESEARCH,
                           description="Call current_time and read_file on notes.txt, then report both.")
    await jig.run_task(task["id"])
    task = jig.store.get_task(task["id"])
    assert task["result"] and task["result"].strip()
    outcome = task["outcome"]
    if outcome is None:
        assert task["status"] == "failed" and task["error"].startswith("StepLimitExceeded"), task
    elif outcome["status"] == "could_not":
        assert task["status"] == "failed" and "step limit of 1" in task["error"], task
        assert outcome["step_limit"] == 1
    else:
        assert task["status"] == "done" and not task["error"], task
        assert outcome["step_limit"] == 1, outcome


async def test_the_prompt_starts_the_same_every_turn_and_ends_with_what_changes(jig):
    first = jig._system_prompt(Mode.ACTION)
    jig.memory.add("Robyn's cat is called Biscuit")
    assert jig._system_prompt(Mode.ACTION) == first, "memories never change the system prompt"
    assert "It is now" not in first and "Biscuit" not in first

    items = [item async for item in jig.chat("What is my cat called? Answer in a few words.")]
    run = jig.store.get_run(items[-1]["run_id"])
    system, user = run["messages"][0], run["messages"][1]
    assert system["content"] == first
    assert user["content"].startswith("What is my cat called?")
    assert user["content"].rstrip().endswith("</jig-context>") and "It is now" in user["content"]
    assert "Biscuit" in user["content"]
    assert "Biscuit" in items[-1]["final"]


async def test_each_step_ends_with_a_budget_line(jig):
    (jig.sandbox.root / "hello.txt").write_text("Hello there", encoding="utf-8")
    items = [item async for item in jig.chat("Use list_files on the workspace root, then tell me the file names.")]
    run = jig.store.get_run(items[-1]["run_id"])
    tools = [m for m in run["messages"] if m["role"] == "tool"]
    assert tools and "[Jig budget]" in tools[-1]["content"]
    assert f"of {jig.agent.max_steps} model calls left" in tools[-1]["content"]
    if jig.model.server_info.get("context_tokens"):
        assert "tokens used" in tools[-1]["content"]
    saved = jig.store.get_session(items[-1]["session_id"])
    assert all("[Jig budget]" not in m["content"] for m in saved if m["role"] == "tool")
    assert "[Jig budget]" in [m for m in saved if m["role"] == "tool"][-1][CONTEXT_KEY]
    assert "hello.txt" in items[-1]["final"]


async def test_a_conversation_reaches_the_model_only_ever_growing_at_the_end(jig):
    """Each turn's prompt starts with the previous turn's, byte for byte (servers that cannot roll back a cached
    prompt reuse it whole), and memories shown once are not repeated."""
    (jig.sandbox.root / "hello.txt").write_text("Hello there", encoding="utf-8")
    cat = jig.memory.add("Robyn's cat is called Biscuit")
    first = [item async for item in jig.chat("Use list_files on the workspace root, then name the files.")]
    sid = first[-1]["session_id"]
    second = [item async for item in jig.chat("What is my cat called? A few words.", session_id=sid)]
    before = jig.store.get_run(first[-1]["run_id"])["messages"]
    after = jig.store.get_run(second[-1]["run_id"])["messages"]
    assert after[:len(before)] == before
    assert "Biscuit" in before[1]["content"] and "Biscuit" not in after[len(before)]["content"]
    assert "Biscuit" in second[-1]["final"], "a memory shown in an earlier turn still holds"

    jig.memory.forget(cat["id"])
    third = [item async for item in jig.chat("Say hello in one word.", session_id=sid)]
    ask = jig.store.get_run(third[-1]["run_id"])["messages"][len(after)]["content"]
    assert f"No longer saved (forgotten): #{cat['id']}." in ask


async def test_streaming_chat_yields_deltas_and_done(jig):
    items = [item async for item in jig.chat("Reply with one short sentence greeting me.")]
    kinds = [i["type"] for i in items]
    assert kinds[0] == "start" and kinds[-1] == "done"
    assert "content" in kinds
    assert "".join(i["text"] for i in items if i["type"] == "content") == items[-1]["final"]
    # The session is persisted and can be continued.
    assert jig.store.get_session(items[-1]["session_id"])[-1]["role"] == "assistant"
