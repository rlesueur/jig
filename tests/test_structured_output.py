"""Structured answers (the planner's plan, the Sentinel's verdict): checked against their schema, an invalid one sent
back once saying what was wrong, and a clear failure after that. The Sentinel fails closed.

The model tests use the real model server from jig.toml. To start from a real invalid answer, the first request is
sent without the tool or response format (so the model answers in prose), then Jig's own retry takes over."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from pathlib import Path

import pytest

from jig.agent.prompts import PLAN_SCHEMA
from jig.config import load_config
from jig.errors import ModelError
from jig.model import _UNPARSED, STRUCTURED_RETRIES, ChatResult, ModelClient, _Refusal
from jig.policy.sentinel import VERDICT_SCHEMA
from jig.runtime import Jig
from jig.schema import check_schema, problems

from .conftest import audit_kinds
from .sandbox_helpers import gated_call

REPO = Path(__file__).resolve().parent.parent
QUESTION = [{"role": "user", "content": "Classify this action: reading a public web page. Answer in one short "
                                        "sentence of plain English."}]


def test_answers_are_checked_against_the_schema():
    assert problems({"verdict": "allow", "risk": "low", "reason": "fine"}, VERDICT_SCHEMA) == []
    assert problems({"verdict": "maybe", "risk": "low", "note": "x"}, VERDICT_SCHEMA) == [
        "$.reason is missing", "$ has properties the schema does not allow: note",
        "$.verdict must be one of 'allow', 'ask_user', 'deny'"]
    plan = {"summary": "s", "tasks": [{"title": "t", "description": "d", "mode": "research", "depends_on": [True]}]}
    assert problems(plan, PLAN_SCHEMA) == ["$.tasks[0].depends_on[0] must be integer"]
    assert problems({"summary": "s", "tasks": []}, PLAN_SCHEMA) == ["$.tasks must have at least 1 items"]
    assert problems([1], VERDICT_SCHEMA) == ["$ must be object"]


def test_a_schema_jig_cannot_fully_check_is_refused():
    check_schema(PLAN_SCHEMA)
    check_schema(VERDICT_SCHEMA)
    with pytest.raises(ValueError, match=r"keywords Jig cannot check: \['pattern'\]"):
        check_schema({"type": "object", "properties": {"code": {"type": "string", "pattern": "^[A-Z]+$"}}})


def _body(config_text: str, tmp_path: Path) -> dict:
    (tmp_path / "jig.toml").write_text(f"[model]\n{config_text}\n", encoding="utf-8")
    cfg = load_config(tmp_path / "jig.toml", data_dir=tmp_path / "d", sandbox_dir=tmp_path / "s")
    return ModelClient(cfg.model, api_key="test-key")._body(QUESTION, tools=None, stream=False, model="m",
                                                           max_tokens=None, response_schema=VERDICT_SCHEMA)


def test_a_tool_call_answer_is_forced_where_the_provider_allows_it(tmp_path):
    local = _body('base_url = "http://127.0.0.1:8080/v1"\nstructured_output = "tool_call"', tmp_path)
    assert [t["function"]["name"] for t in local["tools"]] == ["respond"]
    assert local["tool_choice"] == "required" and local["parallel_tool_calls"] is False
    assert "response_format" not in local
    # Anthropic refuses forced tool use when thinking is on, so its call stays unforced and is checked instead.
    anthropic = _body('base_url = "https://api.anthropic.com/v1"\nprovider = "anthropic"\nmax_tokens = 1000\n'
                      'api_key_env = "X"\nallow_cloud = true', tmp_path)
    assert [t["function"]["name"] for t in anthropic["tools"]] == ["respond"]
    assert "tool_choice" not in anthropic and "parallel_tool_calls" not in anthropic
    schema = _body('base_url = "http://127.0.0.1:8080/v1"', tmp_path)
    assert schema["response_format"]["json_schema"]["schema"] == VERDICT_SCHEMA
    assert "tool_choice" not in schema and "tools" not in schema


def test_an_answer_that_was_cut_off_or_unreadable_is_described_not_repeated(tmp_path):
    (tmp_path / "jig.toml").write_text('[model]\nbase_url = "http://127.0.0.1:8080/v1"\nstructured_output = "tool_call"\n',
                                       encoding="utf-8")
    client = ModelClient(load_config(tmp_path / "jig.toml", data_dir=tmp_path / "d").model)
    runaway = ChatResult(content='{"answer": 5}\n' * 5000, reasoning="", tool_calls=[], finish_reason="length",
                         usage={"completion_tokens": 65186})
    refusal = client._refusal({}, runaway, VERDICT_SCHEMA)
    assert refusal.kind == "cut_off"
    sent = client._correction(runaway, refusal)
    assert sent == [{"role": "user", "content": "Your previous answer to this was not accepted: the answer was cut off "
                     "before it was complete (output 65186 tokens). Keep your thinking short. Call the respond tool "
                     "once, with arguments that match its schema."}]
    unread = _Refusal("unparsed", "the server could not read the answer as a call to the respond tool")
    assert client._correction(None, unread)[0]["content"].startswith(
        "Your previous answer to this was not accepted: the server could not read the answer as a call")
    for message in ("The model produced output that does not match the expected peg-native format",
                    "Failed to parse input at pos 12: <tool_call>"):
        assert _UNPARSED.search(json.dumps({"error": {"code": 500, "message": message}}))


# Against the real model server ---------------------------------------------------------------------------------

@pytest.mark.parametrize("mode, unconstrained, kind", [("tool_call", ("tools", "tool_choice", "parallel_tool_calls"),
                                                        "no_call"),
                                                       ("json_schema", ("response_format",), "invalid_json")])
async def test_an_invalid_answer_is_sent_back_once_and_the_next_is_accepted(config, caplog, mode, unconstrained,
                                                                            kind):
    client = ModelClient(replace(config.model, structured_output=mode), label="agent model")
    try:
        await client.health()
        body = client._body(QUESTION, tools=None, stream=True, model=None, max_tokens=None,
                            response_schema=VERDICT_SCHEMA)
        prose = await client._send({k: v for k, v in body.items() if k not in unconstrained}, None)
        assert prose.content.strip() and not prose.tool_calls
        with caplog.at_level(logging.WARNING, logger="jig.model"):
            result = await client._settle(body, VERDICT_SCHEMA, prose)
    finally:
        await client.aclose()
    assert result.structured_retries == [kind]
    assert result.summary()["structured_retries"] == [kind]
    assert problems(json.loads(result.content), VERDICT_SCHEMA) == [] and not result.tool_calls
    warnings = [r.getMessage() for r in caplog.records if r.name == "jig.model"]
    assert warnings == [f"agent model: structured answer not accepted ({kind}); sending it back, retry 1 of 1"]


@pytest.mark.parametrize("mode", ["tool_call", "json_schema"])
async def test_a_structured_answer_that_is_never_valid_fails_clearly_after_one_retry(config, caplog, mode):
    client = ModelClient(replace(config.model, structured_output=mode, max_tokens=8), label="agent model")
    try:
        await client.health()
        with caplog.at_level(logging.WARNING, logger="jig.model"), pytest.raises(ModelError) as failed:
            await client.chat(QUESTION, response_schema=VERDICT_SCHEMA)
    finally:
        await client.aclose()
    text = str(failed.value)
    assert STRUCTURED_RETRIES == 1
    assert text.startswith("agent model gave no valid structured answer in 2 attempts; Jig sent each one before the "
                           "last back, saying what was wrong. Attempt 1: the answer was cut off before it was complete")
    assert "Attempt 2: the answer was cut off" in text and "(max_tokens = 8" in text and "raise or remove" in text
    assert [r.getMessage() for r in caplog.records if r.name == "jig.model"] == [
        "agent model: structured answer not accepted (cut_off); sending it back, retry 1 of 1"]


async def test_the_sentinel_fails_closed_when_it_cannot_give_a_verdict(config, tmp_path):
    jig = Jig(replace(config, sentinel=replace(config.sentinel, max_tokens=8)))
    await jig.start(run_scheduler=False, check_capabilities=False)
    try:
        outcome, approvals = await gated_call(jig, "web_fetch", {"url": "https://example.com/"},
                                              intent="Find out what the example.com domain is used for.")
        assert not outcome.ok and outcome.result is None and approvals == []
        assert outcome.error_type == "SentinelError"
        assert outcome.error.startswith("Sentinel model call failed: Sentinel model gave no valid structured answer "
                                        "in 2 attempts")
        kinds = audit_kinds(jig)
        assert "sentinel.error" in kinds and "sentinel.verdict" not in kinds
    finally:
        await jig.stop()


async def test_the_planner_fails_the_goal_clearly_when_it_cannot_plan(config):
    jig = Jig(replace(config, model=replace(config.model, max_tokens=8)))
    await jig.start(run_scheduler=False, check_capabilities=False)
    try:
        goal = jig.store.create_goal(title="Weekend", description="Plan a weekend in York.")
        with pytest.raises(ModelError, match="gave no valid structured answer in 2 attempts"):
            await jig.planner.plan(goal["id"])
        failed = jig.store.get_goal(goal["id"])
        assert failed["status"] == "failed" and "gave no valid structured answer in 2 attempts" in failed["error"]
        assert "goal.plan_failed" in audit_kinds(jig) and jig.store.list_tasks(goal_id=goal["id"]) == []
    finally:
        await jig.stop()
