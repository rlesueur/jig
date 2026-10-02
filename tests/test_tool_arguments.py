"""A tool call with wrong arguments is refused with every problem at once, nested ones included."""

from __future__ import annotations

import json

import pytest

from jig.constants import Mode
from jig.errors import ToolArgumentError
from jig.model import ToolCall
from jig.policy.gate import CallContext
from jig.tools.builtin import build_registry


def test_every_problem_is_reported_at_once():
    spec = build_registry().get("schedule_create")
    days = spec.parameters["properties"]["days"]
    assert days["type"] == "array" and days["items"] == {"type": "string"}, "the model is told what the list holds"
    with pytest.raises(ToolArgumentError) as raised:
        spec.validate({"name": 5, "repeat": "fortnightly", "days": ["mon", 3, None], "every_minutes": 1.5,
                       "colour": "blue"})
    message = str(raised.value)
    assert message.startswith("schedule_create: 7 problems with the arguments: ")
    for problem in ("unknown argument 'colour'", "missing required argument 'prompt'", "name must be string",
                    "repeat must be one of 'daily', 'weekdays', 'weekly', 'interval', 'cron'",
                    "days[1] must be string", "days[2] must be string", "every_minutes must be integer"):
        assert problem in message, problem


def test_good_arguments_pass_and_a_whole_number_is_taken_as_an_integer():
    spec = build_registry().get("read_file")
    args = spec.validate({"path": "notes.txt", "offset": 200.0, "find": None})
    assert args == {"path": "notes.txt", "offset": 200} and isinstance(args["offset"], int)


async def test_the_model_is_told_every_problem_in_one_tool_result(jig):
    call = ToolCall(id="c1", name="memory_add", arguments_raw=json.dumps({"tags": ["a", 2], "colour": "red"}))
    outcome = await jig.executor.execute(call, CallContext("r_args", None, Mode.ACTION, "remember something"))
    assert not outcome.ok and outcome.error_type == "ToolArgumentError"
    assert "3 problems" in outcome.error
    assert "missing required argument 'content'" in outcome.error and "tags[1] must be string" in outcome.error
    assert jig.memory.count() == 0
