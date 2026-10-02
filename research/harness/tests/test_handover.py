"""Tests for the GPU-handover machinery. Real local servers and real processes; nothing is mocked.

The record-and-restore cycle is tested two ways: a fast test against a real throwaway HTTP server process,
and (behind JIGBENCH_HANDOVER_LIVE=1) a real llama-server running the small granite model on a spare port,
standing in for the main server. Neither test ever touches the real 8080 server.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from jigbench import handover


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------------------------------------------------------------- 8080 idleness from /slots and /metrics
class _FakeLlama(BaseHTTPRequestHandler):
    busy = False
    slots_enabled = True

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self._send(200, b'{"status":"ok"}')
        elif self.path == "/slots":
            if not type(self).slots_enabled:
                self._send(501, b"slots disabled")
                return
            state = 1 if type(self).busy else 0
            self._send(200, json.dumps([{"id": 0, "state": state, "is_processing": type(self).busy}]).encode())
        elif self.path == "/metrics":
            val = 1.0 if type(self).busy else 0.0
            self._send(200, f"llamacpp:requests_processing {val}\n".encode(), "text/plain")
        else:
            self._send(404, b"no")

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_llama():
    _FakeLlama.busy = False
    _FakeLlama.slots_enabled = True
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeLlama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


def test_slots_report_idle_and_busy(fake_llama):
    idle, _ = handover.server_slots_idle(fake_llama)
    assert idle is True
    _FakeLlama.busy = True
    idle, _ = handover.server_slots_idle(fake_llama)
    assert idle is False


def test_metrics_fallback_when_slots_disabled(fake_llama):
    _FakeLlama.slots_enabled = False
    idle, detail = handover.server_slots_idle(fake_llama)
    assert idle is True and "requests_processing" in detail


def test_idle_unknown_when_server_down():
    idle, _ = handover.server_slots_idle(_free_port())  # nothing listening
    assert idle is None  # unknown -> callers must not stop


def test_idlewatch_requires_continuous_idle(fake_llama):
    watch = handover.IdleWatch(port=fake_llama, required_idle_s=0.4)
    ok, _ = watch.sample()
    assert ok is False  # not yet held long enough
    time.sleep(0.5)
    ok, detail = watch.sample()
    assert ok is True and "idle" in detail
    _FakeLlama.busy = True
    ok, _ = watch.sample()
    assert ok is False  # a busy sample resets the timer


# ---------------------------------------------------------------- Jig client
def test_jigclient_not_running_is_not_busy():
    jig = handover.JigClient(base_url=f"http://127.0.0.1:{_free_port()}", token="x")
    assert jig.running() is False
    busy, why = jig.busy()
    assert busy is False and "not running" in why


# ---------------------------------------------------------------- blocking processes
def test_blocking_processes_detects_a_real_marker_process():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)  # playwright marker"])
    try:
        time.sleep(0.5)
        found = handover.blocking_processes()
        assert any("python" in f.lower() for f in found)
    finally:
        proc.terminate()
        proc.wait(timeout=10)


# ---------------------------------------------------------------- handover file lifecycle
def test_handover_file_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(handover, "LOGS", tmp_path)
    monkeypatch.setattr(handover, "HANDOVER_LOG", tmp_path / "handover.log")
    rec = {"pid": 1, "cmdline": ["x"], "cwd": "."}
    path = handover.write_handover_file(rec, {"paused_jig": False, "supervised": False})
    assert handover.unrestored_handovers() == [path]
    handover.mark_restored(path, ok=True, detail="done")
    assert handover.unrestored_handovers() == []
    assert json.loads(path.read_text())["restored_ok"] is True


# ---------------------------------------------------------------- record + restore, real throwaway server
def test_record_and_restore_cycle_real_process(tmp_path):
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
                            cwd=str(tmp_path))
    try:
        # wait until something is listening (the venv launcher may run http.server in a child process,
        # so the listener pid need not equal proc.pid; the handover operates on the real listener).
        listener = None
        for _ in range(50):
            listener = handover.find_listener_pid(port)
            if listener:
                break
            time.sleep(0.1)
        assert listener is not None
        record = handover.capture_process(listener)
        assert "http.server" in " ".join(record["cmdline"]) and record["cwd"].lower() == str(tmp_path).lower()
        handover.stop_process_gracefully(listener)
        for _ in range(50):
            if handover.find_listener_pid(port) is None:
                break
            time.sleep(0.1)
        assert handover.find_listener_pid(port) is None
        # restore from the record
        restored = handover.restart_from_record(record, log_dir=tmp_path)
        try:
            back = None
            for _ in range(50):
                back = handover.find_listener_pid(port)
                if back:
                    break
                time.sleep(0.1)
            assert back is not None
        finally:
            for pid in {restored.pid, handover.find_listener_pid(port)}:
                if pid:
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    finally:
        if proc.poll() is None:
            proc.kill()


# ---------------------------------------------------------------- live llama-server stand-in (opt-in)
@pytest.mark.skipif(os.environ.get("JIGBENCH_HANDOVER_LIVE") != "1",
                    reason="set JIGBENCH_HANDOVER_LIVE=1 to run the real llama-server handover test")
def test_live_llama_record_stop_restart():
    from jigbench.servers import LlamaServer, ServerSpec, free_vram_mib

    if free_vram_mib() < 8000:
        pytest.skip("not enough free VRAM for the live granite stand-in server")
    port = 8096
    srv = LlamaServer(ServerSpec(model="granite42-8b", port=port, ctx=4096, parallel=1))
    srv.start(timeout_s=600)
    try:
        pid = handover.find_listener_pid(port)
        assert pid is not None
        record = handover.capture_process(pid)
        assert any("granite" in a.lower() for a in record["cmdline"])
        handover.stop_process_gracefully(pid)
        for _ in range(30):
            if handover.find_listener_pid(port) is None:
                break
            time.sleep(1)
        assert handover.find_listener_pid(port) is None
        restored = handover.restart_from_record(record)
        try:
            deadline = time.monotonic() + 600
            served = None
            while time.monotonic() < deadline:
                try:
                    r = httpx.get(f"http://127.0.0.1:{port}/v1/models", timeout=5)
                    if r.status_code == 200:
                        served = [m["id"] for m in r.json().get("data", [])]
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(3)
            assert served and "granite42-8b" in served
        finally:
            if restored.poll() is None:
                restored.terminate()
    finally:
        srv.stop()
