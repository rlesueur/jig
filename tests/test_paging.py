"""Long files, code output and earlier task results are read a part at a time, and every cut is said out loud.

Real files in a real workspace, a real task store and real Docker for the code sandbox. Nothing is mocked."""

from __future__ import annotations

import pytest

from jig.config import load_config
from jig.constants import Mode
from jig.errors import ToolArgumentError, ToolError
from jig.policy.gate import CallContext
from jig.runtime import Jig
from jig.tools.builtin import READ_FILE_CHARS, TASK_RESULT_CHARS
from jig.tools.sandbox_exec import SHOWN_HEAD, SHOWN_TAIL


def _tool(jig, name, *, task_id=None):
    spec = jig.registry.get(name)
    ctx = jig._tool_context(CallContext("r_paging", task_id, Mode.RESEARCH, "Read what is needed."))
    return lambda **args: spec.fn(ctx, **args)


def lines(n: int) -> str:
    return "".join(f"line {i:05d}: the quick brown fox\n" for i in range(n))


async def test_a_long_file_is_read_in_parts_or_searched(jig):
    text = lines(2000) + "THE END\n"
    (jig.sandbox.root / "big.txt").write_text(text, encoding="utf-8")
    read = _tool(jig, "read_file")

    first = await read(path="big.txt")
    assert first["content"] == text[:READ_FILE_CHARS] and first["truncated"]
    assert first["total_chars"] == len(text) and first["next_offset"] == READ_FILE_CHARS
    assert f"offset={READ_FILE_CHARS}" in first["note"]
    parts, part = [first["content"]], first
    while "next_offset" in part:
        part = await read(path="big.txt", offset=part["next_offset"])
        parts.append(part["content"])
    assert "".join(parts) == text, "nothing is lost between parts"

    found = await read(path="big.txt", find="line 01234")
    assert found["truncated"] and found["matches"] == 1 and "line 01234: the quick" in found["content"]
    assert found["content"].startswith("[from offset ")

    small = await read(path="big.txt", max_chars=100, offset=50)
    assert small["content"] == text[50:150] and small["next_offset"] == 150

    (jig.sandbox.root / "short.txt").write_text("short", encoding="utf-8")
    whole = await read(path="short.txt")
    assert whole["content"] == "short" and not whole["truncated"] and "note" not in whole
    with pytest.raises(ToolArgumentError, match="past the end"):
        await read(path="short.txt", offset=99)


async def test_a_later_task_reads_all_of_an_earlier_result_and_nothing_else(jig):
    goal = jig.store.create_goal(title="Report", description="Gather, then write.")
    result = lines(1500) + "Total: 42\n"
    first = jig.store.create_task(title="Gather", description="Gather.", mode=Mode.RESEARCH, goal_id=goal["id"])
    jig.store.update_task(first["id"], result=result)
    other = jig.store.create_task(title="Other", description="Other.", mode=Mode.RESEARCH, goal_id=goal["id"])
    second = jig.store.create_task(title="Write", description="Write.", mode=Mode.RESEARCH, goal_id=goal["id"],
                                   depends_on=[first["id"]])
    read = _tool(jig, "task_result_read", task_id=second["id"])

    parts, part = [], {"next_offset": 0}
    while "next_offset" in part:
        part = await read(task_id=first["id"], offset=part["next_offset"])
        assert len(part["text"]) <= TASK_RESULT_CHARS
        parts.append(part["text"])
    assert "".join(parts) == result and part["text"].endswith("Total: 42\n")
    found = await read(task_id=first["id"], find="Total:")
    assert found["matches"] == 1 and "Total: 42" in found["passages"][0]["text"]

    with pytest.raises(ToolError, match="not a task this task depends on"):
        await read(task_id=other["id"])
    with pytest.raises(ToolError, match="only works in a task"):
        await _tool(jig, "task_result_read")(task_id=first["id"])
    chat_tools = {s["function"]["name"] for s in jig.registry.schemas_for_mode(Mode.ACTION, task=False)}
    task_tools = {s["function"]["name"] for s in jig.registry.schemas_for_mode(Mode.RESEARCH)}
    assert "task_result_read" in task_tools and "task_result_read" not in chat_tools


@pytest.fixture
async def cjig(tmp_path, capabilities):
    runtime = Jig(load_config(data_dir=tmp_path / "data", sandbox_dir=tmp_path / "sandbox",
                              sandbox_backend="container"))
    await runtime.start(run_scheduler=False, check_capabilities=False)
    try:
        yield runtime
    finally:
        await runtime.stop()


async def test_long_code_output_shows_its_start_and_end_and_keeps_all_of_it(cjig):
    expected = lines(3000)
    script = "for i in range(3000):\n    print(f'line {i:05d}: the quick brown fox')\n"
    out = await _tool(cjig, "run_python")(code=script)
    assert out["exit_code"] == 0 and out["truncated"]
    shown = out["stdout"]
    assert shown.startswith(expected[:SHOWN_HEAD]) and shown.endswith(expected[-SHOWN_TAIL:])
    assert f"{len(expected) - SHOWN_HEAD - SHOWN_TAIL} characters not shown here" in shown
    saved = out["output_files"]["stdout"]
    assert saved in out["note"] and "read_file" in out["note"]
    read = _tool(cjig, "read_file")
    parts, part = [], {"next_offset": 0}
    while "next_offset" in part:
        part = await read(path=saved, offset=part["next_offset"])
        parts.append(part["content"])
    assert "".join(parts) == expected, "the middle that was not shown can be read in full"

    short = await _tool(cjig, "run_command")(command="echo hello")
    assert short["stdout"] == "hello\n" and not short["truncated"] and "output_files" not in short
