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


async def test_streaming_chat_yields_deltas_and_done(jig):
    items = [item async for item in jig.chat("Reply with one short sentence greeting me.")]
    kinds = [i["type"] for i in items]
    assert kinds[0] == "start" and kinds[-1] == "done"
    assert "content" in kinds
    assert "".join(i["text"] for i in items if i["type"] == "content") == items[-1]["final"]
    # The session is persisted and can be continued.
    assert jig.store.get_session(items[-1]["session_id"])[-1]["role"] == "assistant"
