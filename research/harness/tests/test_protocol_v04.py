"""Protocol v0.4 changes: explicit agent sampling in D1, the C1 summary prompt without a word limit, closed runs
and the queue. The wire-level tests use a real local HTTP server that records what the OpenAI client sends."""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import openai
import pytest
import yaml

pytest.importorskip("agentdojo")

from agentdojo.agent_pipeline import OpenAILLM
from agentdojo.functions_runtime import FunctionsRuntime
from agentdojo.types import text_content_block_from_string

from jigbench import runner
from jigbench.experiments import d1_agentdojo
from jigbench.experiments.c1_memory import summary_messages
from jigbench.experiments.d1_agentdojo import SampledOpenAILLM, sampling_request

CONFIGS = Path(__file__).resolve().parents[1] / "configs"
JIG_DEFAULT = {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.05}


def test_summary_prompt_with_and_without_the_word_limit() -> None:
    with_limit = summary_messages("(empty)", "2023/05/20", 1, 2, "PART", 400)[1]["content"]
    assert with_limit == ("Current summary:\n(empty)\n\nNew conversation on 2023/05/20 (part 1 of 2):\nPART\n\n"
                          "Rewrite the summary to include every durable fact about the user, each with its date. "
                          "Replace facts that have changed. Keep it under 400 words. Reply with the summary only.")
    without = summary_messages("(empty)", "2023/05/20", 1, 2, "PART", None)[1]["content"]
    assert without == with_limit.replace("Keep it under 400 words. ", "")


def test_sampling_request_sends_every_value_including_zero() -> None:
    assert sampling_request(JIG_DEFAULT, 2) == {"temperature": 1.0, "top_p": 0.95, "seed": 2,
                                                "extra_body": {"top_k": 20, "min_p": 0.05}}
    assert sampling_request({"temperature": 0.0}, 0) == {"temperature": 0.0, "seed": 0}


class _Recorder(BaseHTTPRequestHandler):
    bodies: list[dict] = []

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).bodies.append(body)
        reply = {"id": "x", "object": "chat.completion", "created": 0, "model": body["model"],
                 "choices": [{"index": 0, "finish_reason": "stop",
                              "message": {"role": "assistant", "content": "ok"}}]}
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a) -> None:
        pass


def _sent(llm: OpenAILLM) -> dict:
    messages = [{"role": "user", "content": [text_content_block_from_string("hello")]}]
    _Recorder.bodies.clear()
    llm.query("hello", FunctionsRuntime([]), messages=messages)
    return _Recorder.bodies[-1]


def test_agent_requests_carry_the_configured_sampling_on_the_wire() -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    client = openai.OpenAI(base_url=f"http://127.0.0.1:{srv.server_address[1]}/v1", api_key="none", max_retries=0)
    try:
        body = _sent(SampledOpenAILLM(client, "m", sampling_request(JIG_DEFAULT, 1)))
        assert {k: body[k] for k in ("temperature", "top_p", "top_k", "min_p", "seed")} == {**JIG_DEFAULT, "seed": 1}
        # Why v0.4 needed this: AgentDojo's own element drops a temperature of 0.0 (`temperature or NOT_GIVEN`).
        assert "temperature" not in _sent(OpenAILLM(client, "m", temperature=0.0))
    finally:
        srv.shutdown()


def test_closed_run_cannot_be_resumed() -> None:
    with pytest.raises(ValueError, match="closed"):
        asyncio.run(runner.run(CONFIGS / "full" / "d1_full.yaml"))


def test_queue_runs_c1_then_d1_v2_and_not_the_closed_run() -> None:
    queue = yaml.safe_load((CONFIGS / "queue.yaml").read_text(encoding="utf-8"))["queue"]
    assert queue == ["configs/full/c1_full.yaml", "configs/full/c1_full_summary.yaml",
                     "configs/full/d1_full_v2.yaml", "configs/full/d1_full_v2_qwen.yaml"]
    for entry in queue:
        assert not runner.load_config(CONFIGS.parent / entry).get("closed")


def test_d1_v2_is_whole_suites_three_seeds_jig_sampling() -> None:
    cfg = runner.load_config(CONFIGS / "full" / "d1_full_v2.yaml")
    assert cfg["seeds"] == [0, 1, 2] and cfg["sampling"] == JIG_DEFAULT
    assert "user_tasks_per_suite" not in cfg and "injection_tasks_per_suite" not in cfg
    trials = d1_agentdojo.plan(cfg)
    # v1 suites: banking 16x9, slack 21x5, travel 20x7, workspace 40x6 -> 97 benign + 2 x 629 injected.
    assert len(trials) == 4 * 3 * (97 + 2 * 629)
    qwen = runner.load_config(CONFIGS / "full" / "d1_full_v2_qwen.yaml")
    assert {k: qwen[k] for k in ("suites", "attacks", "seeds", "sampling", "max_steps", "agent")} == \
           {k: cfg[k] for k in ("suites", "attacks", "seeds", "sampling", "max_steps", "agent")}


def test_c1_summary_run_differs_only_in_strategy_and_word_limit() -> None:
    a = yaml.safe_load((CONFIGS / "full" / "c1_full.yaml").read_text(encoding="utf-8"))
    b = yaml.safe_load((CONFIGS / "full" / "c1_full_summary.yaml").read_text(encoding="utf-8"))
    differing = {k for k in a.keys() | b.keys() if a.get(k) != b.get(k)}
    assert differing == {"name", "strategies", "summary_word_limit"}
    assert b["strategies"] == ["rolling_summary"] and b["summary_word_limit"] is None
    assert "rolling_summary" not in a["strategies"]
