"""The multi-step tool-calling loop, end to end against the real model."""

from __future__ import annotations

from jig.agent.loop import RunSpec
from jig.agent.prompts import agent_system_prompt
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


async def test_a_chat_turn_that_fails_is_kept_and_can_be_continued(jig):
    (jig.sandbox.root / "hello.txt").write_text("Hello there", encoding="utf-8")
    ask = ("Call the current_time tool for Europe/London and the list_files tool for the workspace root, then tell "
           "me the time and the names of the files.")
    jig.agent.max_steps = 1
    items = [item async for item in jig.chat(ask)]
    assert items[-1]["type"] == "error" and "StepLimitExceeded" in items[-1]["error"], items[-1]
    sid = items[-1]["session_id"]
    kept = jig.store.get_session(sid)
    assert kept[0] == {"role": "user", "content": ask}
    assert any(m["role"] == "tool" for m in kept), "what Jig did before it stopped is kept"
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


async def test_streaming_chat_yields_deltas_and_done(jig):
    items = [item async for item in jig.chat("Reply with one short sentence greeting me.")]
    kinds = [i["type"] for i in items]
    assert kinds[0] == "start" and kinds[-1] == "done"
    assert "content" in kinds
    assert "".join(i["text"] for i in items if i["type"] == "content") == items[-1]["final"]
    # The session is persisted and can be continued.
    assert jig.store.get_session(items[-1]["session_id"])[-1]["role"] == "assistant"
