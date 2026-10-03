"""Stopping a reply that is not getting anywhere, and what happens next: a structured answer is asked for again
afresh, a chat reply or a task is stopped and left for the user to continue or try
again, a server that goes silent is reported, and a run that keeps making the same call is stopped.

The model tests use the real model server from jig.toml. A reply that repeats itself is asked for in plain words
("on 3,000 lines"), which no request for repetition (``jig.progress.requested_repeats``) recognises."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import replace

import httpx
import pytest

from jig.agent.loop import REPEATED_ACTION_LIMIT
from jig.agent.prompts import CONTINUE_KEY, CONTINUE_PROMPT, STOPPED_KEY
from jig.config import load_config
from jig.constants import Mode, TaskStatus
from jig.errors import ConfigError, ModelStalled, RepeatedActions
from jig.model import ModelClient, ToolCall, _Refusal
from jig.policy.sentinel import VERDICT_SCHEMA
from jig.progress import Stop, requested_repeats
from jig.runtime import CHAT_STOPPED, TASK_STOPPED, Jig
from jig.schema import problems

from .conftest import audit_kinds, wait_for

LOOP = "Print the word banana on 3000 lines, one per line, and nothing else."
QUESTION = [{"role": "user", "content": "Classify this action: reading a public web page. Answer in one short "
                                        "sentence of plain English."}]


def greedy(config):
    """At temperature 0 the model asked for LOOP runs straight into it (its reasoning, then its reply)."""
    return replace(config, model=replace(config.model, sampling={**config.model.sampling, "temperature": 0.0}))


def test_the_loop_request_is_not_taken_as_a_request_for_repetition():
    assert requested_repeats([{"role": "user", "content": LOOP}]) is None


# Settings ------------------------------------------------------------------------------------------------------

def test_there_is_no_total_time_limit_only_a_wait_for_a_silent_server(tmp_path):
    path = tmp_path / "jig.toml"
    path.write_text('[model]\nbase_url = "http://127.0.0.1:8080/v1"\n', encoding="utf-8")
    cfg = load_config(path, data_dir=tmp_path / "d").model
    assert (cfg.first_token_timeout_s, cfg.liveness_timeout_s) == (600.0, 120.0)
    assert not hasattr(cfg, "read_timeout_s")
    client = ModelClient(cfg)
    assert client._client.timeout.read is None
    # An older config's read_timeout_s is the wait for the first token, for the Sentinel too.
    path.write_text('[model]\nbase_url = "http://127.0.0.1:8080/v1"\nread_timeout_s = 900\n'
                    '[sentinel]\nread_timeout_s = 300\n', encoding="utf-8")
    old = load_config(path, data_dir=tmp_path / "d")
    assert old.model.first_token_timeout_s == 900 and old.sentinel.first_token_timeout_s == 300
    path.write_text('[model]\nbase_url = "http://127.0.0.1:8080/v1"\nliveness_timeout_s = 0\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="liveness_timeout_s must be more than 0"):
        load_config(path, data_dir=tmp_path / "d")


def _client(tmp_path, text: str) -> ModelClient:
    (tmp_path / "jig.toml").write_text(f"[model]\n{text}\n", encoding="utf-8")
    return ModelClient(load_config(tmp_path / "jig.toml", data_dir=tmp_path / "d").model, api_key="test-key")


def test_a_fresh_retry_changes_the_seed_and_temperature_only_where_the_provider_takes_them(tmp_path):
    local = _client(tmp_path, 'base_url = "http://127.0.0.1:8080/v1"\n[model.sampling]\ntemperature = 0.6')
    body = local._body(QUESTION, tools=None, stream=True, model="m", max_tokens=None, response_schema=VERDICT_SCHEMA)
    extras, note = local._fresh_sampling(body, attempt=2)
    assert extras == {"seed": note["seed"], "temperature": 0.8} and note["temperature_was"] == 0.6
    assert extras["seed"] != local._fresh_sampling(body, attempt=2)[0]["seed"]
    # With no temperature configured, the server's default is unknown, so Jig does not guess one.
    bare = _client(tmp_path, 'base_url = "http://127.0.0.1:8080/v1"')
    body = bare._body(QUESTION, tools=None, stream=True, model="m", max_tokens=None, response_schema=VERDICT_SCHEMA)
    extras, note = bare._fresh_sampling(body, attempt=2)
    assert set(extras) == {"seed"} and "temperature" in note["not_sent"]
    # Anthropic does not document a seed.
    anthropic = _client(tmp_path, 'base_url = "https://api.anthropic.com/v1"\nprovider = "anthropic"\n'
                                  'max_tokens = 1000\napi_key_env = "X"\nallow_cloud = true\n'
                                  '[model.sampling]\ntemperature = 1.0')
    body = anthropic._body(QUESTION, tools=None, stream=True, model="m", max_tokens=None,
                           response_schema=VERDICT_SCHEMA)
    extras, note = anthropic._fresh_sampling(body, attempt=2)
    assert "seed" not in extras and "does not document it" in note["not_sent"]["seed"]
    assert extras.get("temperature", 1.0) <= 1.0


def test_the_correction_after_a_repeat_does_not_quote_it(tmp_path):
    client = _client(tmp_path, 'base_url = "http://127.0.0.1:8080/v1"')
    stop = Stop("repeated_block", "content", 580, 1160, ratio=0.031, period=14, repeat_chars=1000)
    sent = client._correction(None, _Refusal("repetition", stop.describe(), stop=stop))
    assert sent == [{"role": "user", "content": "Your previous answer to this was stopped because you were "
                     "repeating yourself. Answer once, then stop. Answer again with only the JSON object, matching "
                     "the schema."}]


# Repeated actions -------------------------------------------------------------------------------------------------

def test_a_run_that_keeps_making_the_same_call_for_the_same_result_is_stopped(config):
    jig = Jig(config)
    try:
        call = ToolCall(id="c1", name="list_files", arguments_raw='{"path": "."}')
        reordered = ToolCall(id="c2", name="list_files", arguments_raw='{ "path" : "." }')
        actions: dict[str, int] = {}
        ids = {"run_id": "run_x", "task_id": None}

        def step(c: ToolCall, result: str) -> None:
            messages = [{"role": "assistant", "content": "", "tool_calls": []},
                        {"role": "tool", "tool_call_id": c.id, "content": result}]
            jig.agent._check_repeats([c], messages, actions, ids)

        step(call, "notes.md")
        step(call, "notes.md, plan.md")  # a different result is progress
        step(reordered, "notes.md")
        with pytest.raises(RepeatedActions, match=r"made the same call \(list_files, with the same arguments\) and "
                                                  r"got the same result 3 times"):
            step(call, "notes.md")
        entry = jig.audit.query(kind="run.repeated_actions", limit=1)[0]
        assert REPEATED_ACTION_LIMIT == 3 and "notes.md" not in json.dumps(entry)
    finally:
        jig.db.close()


# Chat and tasks: nothing is retried unless the user asks ------------------------------------------------------

async def test_continue_and_try_again_need_a_reply_that_was_stopped(config):
    jig = Jig(config)
    try:
        jig.store.save_session("sess_x", [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Hi."}])
        for action in ("retry", "continue"):
            with pytest.raises(ValueError, match="was not stopped for repeating itself"):
                [item async for item in jig.chat(session_id="sess_x", action=action)]
        with pytest.raises(ValueError, match="needs the conversation's session_id"):
            [item async for item in jig.chat(action="retry")]
        task = jig.store.create_task(title="t", description="d", mode=Mode.RESEARCH)
        with pytest.raises(ValueError, match="was not stopped for repeating itself"):
            jig.retry_task(task["id"], continue_anyway=False)
    finally:
        jig.db.close()


# Against the real model server ---------------------------------------------------------------------------------

async def test_a_server_that_sends_nothing_is_reported_as_stuck(config):
    client = ModelClient(replace(config.model, first_token_timeout_s=0.001), label="agent model")
    try:
        await client.health()
        with pytest.raises(ModelStalled, match="sent nothing for 0.001 seconds after Jig asked it for a reply.*raise "
                                               "first_token_timeout_s"):
            await client.chat([{"role": "user", "content": LOOP}])
    finally:
        await client.aclose()


async def test_streamed_replies_still_report_their_token_counts(config):
    client = ModelClient(config.model, label="agent model")
    try:
        await client.health()
        plain = await client.chat([{"role": "user", "content": "Say hello in five words."}])
        verdict = await client.chat(QUESTION, response_schema=VERDICT_SCHEMA)
    finally:
        await client.aclose()
    for result in (plain, verdict):
        assert result.usage["prompt_tokens"] > 0 and result.usage["completion_tokens"] > 0
        assert result.usage_missing is None


async def test_a_repeating_structured_answer_is_asked_for_again_afresh(config, caplog):
    client = ModelClient(greedy(config).model, label="agent model")
    try:
        await client.health()
        body = client._body(QUESTION, tools=None, stream=True, model=None, max_tokens=None,
                            response_schema=VERDICT_SCHEMA)
        # A real reply that repeats itself, from the same request without its format, asked to loop.
        loose = {k: v for k, v in body.items() if k not in ("tools", "tool_choice", "parallel_tool_calls",
                                                             "response_format")}
        first = await client._attempt({**loose, "messages": [{"role": "user", "content": LOOP}]}, None,
                                      client._check(None))
        assert isinstance(first, _Refusal) and first.kind == "repetition"
        with caplog.at_level(logging.WARNING, logger="jig.model"):
            result = await client._settle(body, VERDICT_SCHEMA, first)
    finally:
        await client.aclose()
    assert result.structured_retries == ["repetition"]
    assert problems(json.loads(result.content), VERDICT_SCHEMA) == []
    assert result.stops[0]["attempt"] == 1 and result.stops[0]["kind"] == "repetition"
    assert "seed" in result.retry_sampling[0] or "seed" in result.retry_sampling[0].get("not_sent", {})
    summary = json.dumps(result.summary())
    assert "banana" not in summary and "banana" not in caplog.text


async def test_a_chat_reply_that_repeats_itself_is_stopped_and_can_be_continued_or_tried_again(config):
    # Even at temperature 0 a shared server's batching varies the reply, and now and then it finishes its bananas,
    # so the request is made again (a new chat) until the model really loops.
    jig = Jig(greedy(config))
    await jig.start(run_scheduler=False, check_capabilities=False)
    try:
        for _ in range(3):
            stopped = [item async for item in jig.chat(LOOP)][-1]
            if stopped["type"] == "stopped":
                break
        assert stopped["type"] == "stopped", "the model finished LOOP 3 times without repeating itself"
        assert stopped["message"] == CHAT_STOPPED and stopped["relaxed"] is False
        assert stopped["stop"]["kind"] == "repetition" and "banana" not in json.dumps(stopped["stop"])
        sid = stopped["session_id"]
        history = jig.store.get_session(sid)
        assert history[-1][STOPPED_KEY]["kind"] == "repetition"
        assert "model.stopped" in audit_kinds(jig) and "banana" not in json.dumps(jig.audit.query(limit=200))
        run = jig.store.get_run(stopped["run_id"])
        step = run["step_records"][-1]
        assert step["status"] == "stopped" and "banana" not in json.dumps(step["output"])

        again = [item async for item in jig.chat(session_id=sid, action="retry")]
        assert again[0]["action"] == "retry" and again[-1]["type"] in ("stopped", "done")
        assert sum(m["role"] == "user" for m in jig.store.get_session(sid)) == 1  # the same message, asked again
        assert "chat.retried" in audit_kinds(jig)
        if again[-1]["type"] != "stopped":
            return
        before = jig.store.get_session(sid)
        more = [item async for item in jig.chat(session_id=sid, action="continue")]
        assert more[0]["action"] == "continue" and more[-1]["type"] in ("done", "stopped")
        after = jig.store.get_session(sid)
        if more[-1]["type"] == "stopped":
            # Relaxed: only a very long exact repeat stops a reply the user chose to continue.
            assert more[-1]["relaxed"] is True and more[-1]["stop"]["repeat_chars"] >= 20_000
            assert after[-2].get(CONTINUE_KEY) and after[-1][STOPPED_KEY]["kind"] == "repetition"
        else:  # carried on and finished: saved as one reply
            assert len(after) == len(before) and after[-1]["content"].startswith(before[-1]["content"])
    finally:
        await jig.stop()


async def test_stopping_a_chat_reply_cancels_it_at_the_server_and_keeps_what_arrived(config):
    # The server may be busy with other work, so the reply's own slot is found by a seed no one else uses.
    seed = random.SystemRandom().randrange(10**8, 2**31)
    jig = Jig(replace(config, model=replace(config.model, sampling={**config.model.sampling, "seed": seed})))
    await jig.start(run_scheduler=False, check_capabilities=False)
    root = jig.config.model.base_url.rstrip("/").removesuffix("/v1")
    try:
        async with httpx.AsyncClient(base_url=root, timeout=5) as http:
            if (await http.get("/slots")).status_code != 200:
                pytest.skip("the server has no /slots to check")

            async def mine() -> dict | None:
                return next((s for s in (await http.get("/slots")).json()
                             if s.get("is_processing") and (s.get("params") or {}).get("seed") == seed), None)

            stream = jig.chat(LOOP)
            sid, slot, pieces, wrote = None, None, 0, False
            async for item in stream:
                sid = sid or item.get("session_id")
                if item["type"] in ("reasoning", "content"):
                    pieces += 1
                    wrote = wrote or item["type"] == "content"
                    if not slot and pieces % 10 == 1:
                        slot = await mine()
                if slot and pieces >= 40:
                    break
            assert slot, "the reply's slot was not seen working"
            started = time.monotonic()
            await stream.aclose()
            while (now := await mine()) and now["id_task"] == slot["id_task"]:
                assert time.monotonic() - started < 5, "the server was still generating 5 seconds after Stop"
                await asyncio.sleep(0.1)
            freed = time.monotonic() - started
        saved = jig.store.get_session(sid)
        assert saved[0]["content"] == LOOP
        if wrote:  # what had arrived of the reply itself is kept, marked as stopped by the user
            assert saved[-1][STOPPED_KEY] == {"kind": "cancelled"} and saved[-1]["content"]
        print(f"slot {slot['id']} free {freed:.2f} s after Stop")
    finally:
        await jig.stop()


async def test_a_task_that_repeats_itself_waits_for_the_user_then_continues_or_tries_again(config):
    jig = Jig(greedy(config))
    await jig.start(check_capabilities=False)
    try:
        task = jig.create_task(title="Bananas", description=LOOP, mode=Mode.RESEARCH)
        failed = await wait_for(lambda: (t := jig.store.get_task(task["id"]))["status"] == TaskStatus.FAILED and t,
                                what="the task to be stopped", timeout=1800)
        assert failed["error"].startswith(TASK_STOPPED) and "try it again, or continue it anyway" in failed["error"]
        assert "banana" not in failed["error"]
        jig.retry_task(task["id"], continue_anyway=True)
        run = jig.store.list_runs(task_id=task["id"], limit=1)[0]
        assert jig.store.get_run(run["id"], with_steps=False)["messages"][-1]["content"] == CONTINUE_PROMPT
        await wait_for(lambda: jig.store.get_task(task["id"])["status"] in (TaskStatus.DONE, TaskStatus.FAILED),
                       what="the continued task to finish", timeout=1800)
        assert "task.retried" in audit_kinds(jig)
    finally:
        await jig.stop()
