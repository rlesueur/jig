"""Tests for the harness itself. Network tests use a real local HTTP server; nothing is mocked."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

import pytest

from jigbench import netroute, report, runner, schema, stats
from jigbench.compute_policy import Policy, idle_seconds, in_window
from jigbench.servers import LlamaServer, ServerError, ServerSpec

LONDON = ZoneInfo("Europe/London")


# ---------------------------------------------------------------- statistics
def test_wilson_matches_reference_values():
    p, lo, hi = stats.wilson(8, 10)
    assert p == 0.8
    assert lo == pytest.approx(0.4902, abs=1e-3) and hi == pytest.approx(0.9433, abs=1e-3)
    assert stats.wilson(0, 0)[0] != stats.wilson(0, 0)[0]  # NaN when there is no data


def test_bootstrap_interval_contains_mean_and_holm_is_monotone():
    m, lo, hi = stats.bootstrap_mean([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    assert lo <= m == 5.5 <= hi
    adj = stats.holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adj["a"] == pytest.approx(0.03) and adj["c"] == pytest.approx(0.06) and adj["b"] == pytest.approx(0.06)


# ---------------------------------------------------------------- benchmark routing (isolation)
class _Echo(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps({"path": self.path, "host": self.headers.get("Host")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def echo_port():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Echo)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


async def test_bench_transport_routes_test_hosts_only(echo_port):
    async with netroute.bench_client({"wiki.jigbench.test": echo_port}) as client:
        r = await client.get("http://wiki.jigbench.test/wiki/x.html?q=1")
        assert r.status_code == 200 and r.json()["path"] == "/wiki/x.html?q=1"
        with pytest.raises(netroute.BenchmarkRoutingError):
            await client.get("https://example.com/")


async def test_bench_transport_with_no_routes_blocks_everything(echo_port):
    async with netroute.bench_client({}) as client:
        with pytest.raises(netroute.BenchmarkRoutingError):
            await client.get("http://wiki.jigbench.test/x")
        with pytest.raises(netroute.BenchmarkRoutingError):
            await client.get("https://example.com/")


def test_bench_transport_rejects_non_test_hosts():
    with pytest.raises(ValueError):
        netroute.BenchTransport({"example.com": 80})


async def test_exemption_is_narrow_and_restored():
    import jig.policy.core as core
    import jig.tools.web as web

    original = web.public_address_problem
    with netroute.benchmark_hosts_exempt({"wiki.jigbench.test": 1}):
        assert await web.public_address_problem("http://wiki.jigbench.test/a") is None
        assert core.public_address_problem is web.public_address_problem
        assert await web.public_address_problem("http://127.0.0.1:8080/") is not None
        assert await web.public_address_problem("http://other.jigbench.test/") is not None
    assert web.public_address_problem is original and core.public_address_problem is original


# ---------------------------------------------------------------- compute policy
def test_overnight_window_respects_british_summer_time():
    assert in_window(datetime(2026, 7, 1, 1, 30, tzinfo=LONDON))
    assert not in_window(datetime(2026, 7, 1, 7, 0, tzinfo=LONDON))
    assert in_window(datetime(2026, 7, 1, 0, 30, tzinfo=ZoneInfo("UTC")))  # 01:30 BST
    assert not in_window(datetime(2026, 7, 1, 6, 30, tzinfo=ZoneInfo("UTC")))  # 07:30 BST
    assert in_window(datetime(2026, 1, 15, 1, 30, tzinfo=ZoneInfo("UTC")))  # 01:30 GMT
    assert not in_window(datetime(2026, 1, 15, 0, 30, tzinfo=ZoneInfo("UTC")))  # 00:30 GMT


def test_idle_and_must_stop_use_real_input_state():
    idle = idle_seconds()
    assert idle >= 0
    p = Policy(active_s=idle + 3600)
    assert p.must_stop() is not None  # "recent" input by this threshold: the policy must stop


# ---------------------------------------------------------------- servers
def test_server_refuses_users_port():
    with pytest.raises(ServerError):
        LlamaServer(ServerSpec(model="granite42-8b", port=8080))
    with pytest.raises(ServerError):
        LlamaServer(ServerSpec(model="granite42-8b", port=9000))


# ---------------------------------------------------------------- results, schema and report (C1)
def _c1_row(i: int, strat: str, correct: bool) -> dict:
    return {"trial_id": f"c1|cond|0|q{i}|{strat}", "condition": "cond", "seed": 0, "status_harness": "ok",
            "started_at": "2026-10-01T00:00:00+00:00", "duration_s": 1.0, "endpoint_models": {"a": "m"},
            "question_id": f"q{i}", "question_type": "multi-session", "strategy": strat, "correct": correct,
            "total_prompt_tokens": 1000 + i, "memories_final": 5, "approvals": 0, "wall_s": 2.0}


def test_resume_schema_and_c1_tables(tmp_path):
    rows = [_c1_row(i, "retrieval", i % 2 == 0) for i in range(4)] + [_c1_row(9, "full_context", True)]
    path = tmp_path / "trials.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows + [{**rows[0], "trial_id": "e",
                                                             "status_harness": "error"}]), encoding="utf-8")
    assert runner.finished_ids(path) == {r["trial_id"] for r in rows}
    meta = {"experiment": "c1", "sessions": [{"git": {}, "hardware": {}, "endpoints": {"a": {}}, "config": {}}]}
    schema.validate_run(meta, rows)
    with pytest.raises(schema.SchemaError):
        schema.validate_run(meta, [{k: v for k, v in rows[0].items() if k != "correct"}])
    table = report.summarise("c1", rows)
    retr = next(r for r in table if r["strategy"] == "retrieval")
    assert retr["Accuracy"]["k"] == 2 and retr["Accuracy"]["n"] == 4
    tex = report.latex("c1", "unit-pilot", table, pilot=True)
    assert "PILOT" in tex and r"\toprule" in tex and "2/4" in tex


def test_report_marks_retried_errors_and_derives_d1_attack_success(tmp_path):
    base = {"condition": "c", "seed": 0, "suite": "banking", "user_task": "u", "started_at": "x",
            "duration_s": 1.0, "endpoint_models": {}, "utility": True, "blocked_calls": 0, "tool_errors": 0,
            "approvals": 0}
    rows = [
        {**base, "trial_id": "a", "attack": "important_instructions", "injection_task": "i", "status_harness": "error"},
        # stored attack_success is the pre-fix inverted value; the report must use AgentDojo's raw `security`
        {**base, "trial_id": "a", "attack": "important_instructions", "injection_task": "i", "status_harness": "ok",
         "security": True, "attack_success": False},
        {**base, "trial_id": "b", "attack": "important_instructions", "injection_task": "j", "status_harness": "ok",
         "security": False, "attack_success": True},
        {**base, "trial_id": "c", "attack": "none", "injection_task": "", "status_harness": "error"},
    ]
    (tmp_path / "trials.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    (tmp_path / "run.json").write_text(json.dumps({"experiment": "d1", "name": "unit"}), encoding="utf-8")
    _, ok, errors = report.load(tmp_path)
    assert [e["superseded"] for e in errors] == [True, False]
    table = report.summarise("d1", ok)
    assert table[0]["Attack success"]["k"] == 1 and table[0]["Attack success"]["n"] == 2
