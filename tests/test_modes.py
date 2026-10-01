"""Read-only research mode is enforced at the tool level, not just by the prompt."""

from __future__ import annotations

import json

from jig.agent.loop import RunSpec
from jig.agent.prompts import agent_system_prompt
from jig.constants import Mode
from jig.model import ToolCall
from jig.policy.gate import CallContext

from .conftest import audit_kinds


async def test_gate_refuses_action_tool_in_research_mode(jig):
    call = ToolCall(id="c1", name="write_file", arguments_raw=json.dumps({"path": "x.txt", "content": "nope"}))
    ctx = CallContext(run_id="r_test", task_id=None, mode=Mode.RESEARCH, intent="research only")
    outcome = await jig.executor.execute(call, ctx)

    assert not outcome.ok
    assert outcome.error_type == "ModeViolation"
    assert not (jig.sandbox.root / "x.txt").exists()
    assert "policy.mode_violation" in audit_kinds(jig, run_id="r_test")
    # The refusal happens before the Sentinel is ever consulted.
    assert "sentinel.verdict" not in audit_kinds(jig, run_id="r_test")


async def test_research_mode_allows_private_notes(jig):
    call = ToolCall(id="c2", name="note_write", arguments_raw=json.dumps({"title": "t", "body": "private"}))
    outcome = await jig.executor.execute(call, CallContext("r_test2", None, Mode.RESEARCH, "take a note"))
    assert outcome.ok
    assert jig.store.list_notes()[0]["body"] == "private"


async def test_research_run_cannot_write_files(jig):
    offered = {t.name for t in jig.registry.for_mode(Mode.RESEARCH)}
    assert "write_file" not in offered and "memory_forget" not in offered
    assert {"web_fetch", "read_file", "note_write", "memory_add"} <= offered

    messages = [
        {"role": "system", "content": agent_system_prompt(Mode.RESEARCH, "Europe/London")},
        {"role": "user", "content": "Create a file called out.txt containing the word hi."},
    ]
    result = await jig.agent.run(messages, RunSpec(kind="task", mode=Mode.RESEARCH, intent="write out.txt"))
    assert not (jig.sandbox.root / "out.txt").exists()
    run = jig.store.get_run(result.run_id)
    for step in run["step_records"]:
        if step["type"] == "model_call":
            assert "write_file" not in step["input"]["tools"]
        if step["type"] == "tool_call" and step["name"] == "write_file":
            assert step["output"]["error_type"] == "ModeViolation"
