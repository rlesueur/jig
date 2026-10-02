"""Protocol v0.5: the Qwen reviewer run is dropped, the low-memory D1 configuration is added, the queue takes
turns between the two D1 v2 runs, and every trial records peak VRAM from the real Windows GPU counter."""

from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("agentdojo")

from jigbench import runner
from jigbench.experiments import d1_agentdojo
from jigbench.overnight import entries
from jigbench.paths import HARNESS
from jigbench.vram import DedicatedCounter, PeakSampler, VramError, listening_pid

CONFIGS = Path(__file__).resolve().parents[1] / "configs"
SAME = ("suites", "attacks", "sampling", "max_steps", "benchmark")


def test_queue_runs_c1_first_then_the_two_d1_v2_runs_take_turns() -> None:
    queue = entries(CONFIGS / "queue.yaml")
    assert [(p.relative_to(HARNESS).as_posix(), n) for p, n in queue] == [
        ("configs/full/c1_full.yaml", None), ("configs/full/c1_full_summary.yaml", None),
        ("configs/full/d1_full_v2.yaml", 200), ("configs/full/d1_full_v2_lowmem.yaml", 200)]
    for path, _ in queue:
        assert not runner.load_config(path).get("closed")


def test_the_qwen_reviewer_run_is_gone() -> None:
    assert not (CONFIGS / "full" / "d1_full_v2_qwen.yaml").exists()
    for path, _ in entries(CONFIGS / "queue.yaml"):
        assert "qwen" not in path.read_text(encoding="utf-8").split("conditions:")[-1].lower()


def test_lowmem_is_one_granite_model_as_agent_and_sentinel_on_upstream_llama_cpp() -> None:
    low = runner.load_config(CONFIGS / "full" / "d1_full_v2_lowmem.yaml")
    v2 = runner.load_config(CONFIGS / "full" / "d1_full_v2.yaml")
    assert {k: low[k] for k in SAME} == {k: v2[k] for k in SAME}
    assert low["seeds"] == [0]
    assert list(low["endpoints"]) == ["granite8b"]
    server = low["endpoints"]["granite8b"]["server"]
    assert (server["model"], server["build"], server["ctx"]) == ("granite42-8b", "upstream", 32768)
    assert low["agent"] == {"endpoint": "granite8b", "name": "granite42-8b"}
    conds = {c["id"]: c for c in low["conditions"]}
    assert set(conds) == {"sentinel-granite-action", "sentinel-granite-readonly", "no-reviewer-action"}
    for cid in ("sentinel-granite-action", "sentinel-granite-readonly"):
        assert conds[cid]["sentinel"] == low["agent"]
    assert conds["sentinel-granite-readonly"]["mode"] == "research"
    assert len(d1_agentdojo.plan(low)) == 3 * (97 + 2 * 629)


class _Ok(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a) -> None:
        pass


def test_listening_pid_finds_the_real_process_behind_a_port() -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Ok)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert listening_pid(f"http://127.0.0.1:{srv.server_address[1]}/v1") == os.getpid()
    finally:
        srv.shutdown()
        srv.server_close()
    with pytest.raises(VramError, match="not on this machine"):
        listening_pid("https://api.openai.com/v1")


def test_dedicated_counter_reads_real_per_process_gpu_memory() -> None:
    counter = DedicatedCounter()
    try:
        usage = counter.by_pid()
    finally:
        counter.close()
    assert usage, "the Windows GPU process memory counter returned no processes"
    assert all(isinstance(p, int) and v >= 0 for p, v in usage.items())


def test_peak_sampler_counts_a_shared_server_once() -> None:
    sampler = PeakSampler(interval_s=0.2)
    sampler.start()
    try:
        pid = os.getpid()
        sampler.watch({"agent": pid, "sentinel": pid})
        peak = sampler.take()
    finally:
        sampler.stop()
    assert peak["samples"] >= 2 and peak["pids"] == {"agent": pid, "sentinel": pid}
    assert peak["total_mib"] == peak["by_endpoint"]["agent"] == peak["by_endpoint"]["sentinel"]
