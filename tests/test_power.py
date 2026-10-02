"""Turning Jig off, with real processes only: `jig serve` on spare ports, the developer's model server on
8080 (used READ-ONLY: it is never stopped), and a real llama.cpp server that Jig launches on a spare port.

The launched-server test needs a stock llama.cpp build and a small GGUF model: set JIG_TEST_LLAMA_SERVER
and JIG_TEST_SMALL_MODEL (as for tests/test_model_server_supervision.py). Without both it is skipped.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from jig.procinfo import AdoptedProcess, process_start_time

from .server_helpers import config_path, free_port, kill, start_jig, token, wait_health, wait_stopped

LLAMA_SERVER = Path(os.environ.get("JIG_TEST_LLAMA_SERVER", ""))
SMALL_MODEL = Path(os.environ.get("JIG_TEST_SMALL_MODEL", ""))
needs_llama = pytest.mark.skipif(not (LLAMA_SERVER.is_file() and SMALL_MODEL.is_file()),
                                 reason="set JIG_TEST_LLAMA_SERVER and JIG_TEST_SMALL_MODEL")
ALIAS = "jig-test-small"
POWER_TEST_TASK = "\\Jig\\Jig Agent Power Test"
LONG_TASK = ("Write a detailed, 3000-word essay on the history of timekeeping, from sundials to atomic clocks. "
             "Do not use any tools; just write the essay as your answer.")


def db_rows(data_dir: Path, sql: str) -> list[tuple]:
    con = sqlite3.connect(data_dir / "jig.db")
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def audit(data_dir: Path, kind: str) -> list[dict]:
    return [json.loads(d) for (d,) in db_rows(data_dir, f"SELECT data_json FROM audit WHERE kind = '{kind}' ORDER BY id")]


def jig_cli(cfg: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "jig.cli", "--config", str(cfg), *args], capture_output=True,
                          text=True, timeout=600, stdin=subprocess.DEVNULL)


def listening(port: int) -> bool:
    try:
        httpx.get(f"http://127.0.0.1:{port}/health", timeout=2)
        return True
    except httpx.HTTPError:
        return False


# Refusals, confirmation, audit and autostart: the real 8080 server, read-only -----------------------

@pytest.mark.skipif(sys.platform != "win32", reason="registers a real (test) Task Scheduler entry")
def test_turn_off_through_the_api_refuses_an_external_model_server(tmp_path):
    from jig.autostart import LaunchSpec
    from jig.autostart.windows import WindowsTaskScheduler
    from jig.config import load_config

    data_dir, port = tmp_path / "data", free_port()
    marker = tmp_path / "launched.txt"
    cfg = tmp_path / "jig.toml"
    # The real config, plus a [model.launch] command that would leave a file behind if it ever ran.
    cfg.write_text(config_path().read_text(encoding="utf-8") + "\n[model.launch]\n"
                   f"command = '{sys.executable}'\nargs = ['-c', 'open(r\"{marker}\", \"w\").write(\"x\")']\n"
                   "readiness_timeout_s = 10\n", encoding="utf-8")
    autostart = WindowsTaskScheduler(LaunchSpec.from_config(load_config(cfg, data_dir=data_dir), port=port),
                                     entry=POWER_TEST_TASK)
    autostart.enable()  # a real (test) logon task, so we can show that turning off leaves it registered
    proc, log = start_jig(data_dir, port, config=cfg, env={"JIG_AUTOSTART_ENTRY": POWER_TEST_TASK})
    try:
        wait_health(port, proc=proc, log=log)
        base, h = f"http://127.0.0.1:{port}", token(data_dir)
        task = httpx.post(f"{base}/tasks", headers=h, json={"title": "long essay", "description": LONG_TASK,
                                                            "mode": "research"}).json()

        state = httpx.get(f"{base}/power", headers=h, timeout=60).json()
        print("GET /power:", json.dumps(state, indent=2))
        assert state["applicable"] is True and state["can_stop_model"] is False
        assert state["model_server"]["already_running"] is True and state["model_server"]["managed"] is False
        assert "Jig didn't start the model server" in state["model_server"]["refusal"]
        assert state["autostart"] == {"applicable": True, "registered": True, "entry": POWER_TEST_TASK,
                                      "other_install": False}
        assert "Start with Windows is on" in state["start_again"]
        assert httpx.get(f"{base}/power").status_code == 401  # authenticated like everything else

        # The confirmation field is required and must be exactly true; the scope must be known.
        for body, status, text in (({"scope": "jig"}, 400, '"confirm": true'),
                                   ({"scope": "jig", "confirm": False}, 400, '"confirm": true'),
                                   ({"scope": "everything", "confirm": True}, 400, "scope must be one of"),
                                   ({"confirm": True}, 422, "")):
            r = httpx.post(f"{base}/power/stop", headers=h, json=body)
            assert r.status_code == status and text in str(r.json().get("error", "")), (body, r.status_code, r.text)
        # Jig did not start the 8080 server, so it refuses to stop it, and says how to stop it yourself.
        r = httpx.post(f"{base}/power/stop", headers=h, json={"scope": "jig_and_model", "confirm": True})
        print("refusal:", r.json()["error"])
        assert r.status_code == 409 and "Jig didn't start the model server" in r.json()["error"]
        assert "Ctrl+C" in r.json()["error"] and "ollama stop" in r.json()["error"]
        # A browser session can only turn Jig off from Jig's own origin.
        code = httpx.post(f"{base}/auth/login-code", headers=h).json()["code"]
        browser = httpx.Client(base_url=base)
        assert browser.post("/auth/session", json={"code": code}).status_code == 200
        assert browser.post("/power/stop", json={"scope": "jig", "confirm": True},
                            headers={"Origin": "http://evil.example"}).status_code == 403
        assert httpx.get(f"{base}/health").status_code == 200  # still running after every refusal
        deadline = time.monotonic() + 120
        while httpx.get(f"{base}/tasks/{task['id']}", headers=h).json()["status"] != "running":
            assert time.monotonic() < deadline, "the task never started running"
            time.sleep(0.2)
        time.sleep(2)  # let the model call get under way

        r = browser.post("/power/stop", json={"scope": "jig", "confirm": True}, headers={"Origin": base})
        print("POST /power/stop:", json.dumps(r.json(), indent=2))
        assert r.status_code == 202 and r.json()["stopping"] is True
        assert r.json()["model_server"] == "not managed by Jig; left as it is"
        assert "Start with Windows is on" in r.json()["message"]
        assert proc.wait(60) == 0, log.read_text(errors="replace")
    finally:
        kill(proc)
        registered_after = autostart.is_registered()
        autostart.disable()
    wait_stopped(data_dir)
    assert registered_after, "turning Jig off must not unregister autostart"
    assert "graceful shutdown requested: POST /power/stop" in log.read_text(errors="replace")
    # Graceful: the running task went back to the queue with its checkpoint; nothing is left 'running'.
    assert db_rows(data_dir, f"SELECT status FROM tasks WHERE id = '{task['id']}'") == [("queued",)]
    assert db_rows(data_dir, "SELECT count(*) FROM run_steps WHERE status = 'running'") == [(0,)]
    [stop] = audit(data_dir, "power.stop")
    assert stop["scope"] == "jig" and stop["via"] == "api" and stop["auth_via"] == "cookie"
    assert stop["autostart_registered"] is True
    assert db_rows(data_dir, "SELECT kind FROM audit ORDER BY id DESC LIMIT 1") == [("runtime.stop",)]
    assert not marker.exists(), "the [model.launch] command ran although 8080 was already serving"
    assert httpx.get("http://127.0.0.1:8080/v1/models", timeout=10).status_code == 200  # untouched


def test_turning_off_closes_live_event_streams_at_once(tmp_path):
    """Jig's window holds the /events WebSocket open, and a program may hold /events/sse. Turning off ends both
    as shutdown begins instead of waiting out uvicorn's 10-second graceful timeout, and still puts a running
    task back on the queue with its checkpoint."""
    import asyncio

    import websockets

    data_dir, port = tmp_path / "data", free_port()
    proc, log = start_jig(data_dir, port)
    try:
        wait_health(port, proc=proc, log=log)
        base, h = f"http://127.0.0.1:{port}", token(data_dir)
        task = httpx.post(f"{base}/tasks", headers=h, json={"title": "long essay", "description": LONG_TASK,
                                                            "mode": "research"}).json()
        deadline = time.monotonic() + 120
        while httpx.get(f"{base}/tasks/{task['id']}", headers=h).json()["status"] != "running":
            assert time.monotonic() < deadline, "the task never started running"
            time.sleep(0.2)

        async def turn_off() -> tuple[float, int | None, float, float]:
            ws = await websockets.connect(f"ws://127.0.0.1:{port}/events", additional_headers=h)
            assert json.loads(await ws.recv())["snapshot"] is True
            ended: dict[str, float] = {}

            async def read_ws() -> None:
                try:
                    async for _ in ws:
                        pass
                except websockets.ConnectionClosed:
                    pass
                ended["ws"] = time.monotonic()

            async def read_sse(opened: asyncio.Event) -> None:
                async with httpx.AsyncClient(timeout=None) as client, \
                        client.stream("GET", f"{base}/events/sse", headers=h) as r:
                    async for line in r.aiter_lines():
                        if line.startswith("data:"):
                            opened.set()
                ended["sse"] = time.monotonic()

            opened = asyncio.Event()
            readers = [asyncio.create_task(read_ws()), asyncio.create_task(read_sse(opened))]
            await asyncio.wait_for(opened.wait(), 10)
            async with httpx.AsyncClient() as client:
                start = time.monotonic()
                r = await client.post(f"{base}/power/stop", headers=h, json={"scope": "jig", "confirm": True})
                assert r.status_code == 202, r.text
            await asyncio.wait_for(asyncio.gather(*readers), 8)
            return start, ws.close_code, ended["ws"] - start, ended["sse"] - start

        start, code, ws_s, sse_s = asyncio.run(turn_off())
        assert proc.wait(30) == 0, log.read_text(errors="replace")
        took = time.monotonic() - start
        print(f"turned off in {took:.2f}s; WebSocket closed ({code}) after {ws_s:.2f}s, SSE after {sse_s:.2f}s")
        assert code == 1012
        assert took < 5, f"turning off took {took:.1f}s with event streams open"
    finally:
        kill(proc)
    wait_stopped(data_dir)
    assert "timeout graceful shutdown exceeded" not in log.read_text(errors="replace")
    assert db_rows(data_dir, f"SELECT status FROM tasks WHERE id = '{task['id']}'") == [("queued",)]
    assert db_rows(data_dir, "SELECT count(*) FROM run_steps WHERE status = 'running'") == [(0,)]


def test_jig_stop_cli_refuses_in_container_mode(tmp_path):
    proc = subprocess.run([sys.executable, "-m", "jig.cli", "--config", str(config_path()), "stop", "--data-dir",
                           str(tmp_path)], capture_output=True, text=True, timeout=60,
                          env={**os.environ, "JIG_DEPLOYMENT": "container"})
    assert proc.returncode == 1 and "docker compose stop jig" in proc.stderr and "unless-stopped" in proc.stderr


# Jig and the model off: a real llama-server that Jig launches ----------------------------------------

def _llama_config(tmp_path: Path, model_port: int) -> Path:
    cfg = tmp_path / "llama.toml"
    args = ["-m", str(SMALL_MODEL), "--host", "127.0.0.1", "--port", str(model_port), "-ngl", "99", "-c", "8192",
            "--parallel", "2", "--jinja", "--alias", ALIAS]
    cfg.write_text(f"""
[model]
base_url = "http://127.0.0.1:{model_port}/v1"
name = "{ALIAS}"
connect_timeout_s = 3.0
max_tokens = 1024

[model.launch]
command = '{LLAMA_SERVER}'
args = {json.dumps(args)}
readiness_timeout_s = 300
poll_interval_s = 0.5

[vision]
enabled = false

[paths]
data_dir = "data"
sandbox_dir = "sandbox"
""", encoding="utf-8")
    return cfg


@needs_llama
def test_turn_jig_and_the_model_off(tmp_path):
    data_dir, port, model_port = tmp_path / "data", free_port(), free_port()
    cfg = _llama_config(tmp_path, model_port)
    env = {"JIG_AUTOSTART_ENTRY": POWER_TEST_TASK}
    procs: list[subprocess.Popen[bytes]] = []
    model_pids: list[int] = []
    try:
        proc, log = start_jig(data_dir, port, config=cfg, env=env)
        procs.append(proc)
        wait_health(port, proc=proc, log=log, timeout=400)
        base, h = f"http://127.0.0.1:{port}", token(data_dir)

        state = httpx.get(f"{base}/power", headers=h, timeout=60).json()
        print("GET /power:", json.dumps(state["model_server"], indent=2))
        model = state["model_server"]
        assert state["can_stop_model"] is True and model["managed"] is True and model["already_running"] is False
        first = model["pid"]
        model_pids.append(first)
        assert process_start_time(first) is not None
        if model["gpu"]["available"]:
            assert model["gpu"]["on_gpu"] is True and model["frees_gpu_memory"] is True
        status = jig_cli(cfg, "model", "status", "--data-dir", str(data_dir))
        print(status.stdout)
        assert status.returncode == 0 and f"launched and supervised by Jig (pid {first})" in status.stdout

        # jig model stop: the model server stops, Jig keeps running.
        assert httpx.post(f"{base}/model/stop", headers=h, json={}).status_code == 400
        r = httpx.post(f"{base}/model/stop", headers=h, json={"confirm": True}, timeout=60)
        assert r.status_code == 200 and r.json()["pid"] == first
        assert process_start_time(first) is None and not listening(model_port)
        assert httpx.get(f"{base}/health").status_code == 200
        r = httpx.post(f"{base}/power/stop", headers=h, json={"scope": "jig_and_model", "confirm": True})
        assert r.status_code == 409 and "already stopped" in r.json()["error"]

        # jig model start: Jig launches it again.
        r = httpx.post(f"{base}/model/start", headers=h, timeout=400)
        assert r.status_code == 200 and r.json()["started"] is True
        second = r.json()["pid"]
        model_pids.append(second)
        assert second != first and process_start_time(second) is not None

        # Turn Jig off (only): the model server Jig launched keeps running.
        r = httpx.post(f"{base}/power/stop", headers=h, json={"scope": "jig", "confirm": True})
        assert r.status_code == 202 and r.json()["model_server"].startswith(f"left running (pid {second})")
        assert proc.wait(60) == 0, log.read_text(errors="replace")
        assert process_start_time(second) is not None
        assert httpx.get(f"http://127.0.0.1:{model_port}/v1/models", timeout=10).json()["data"][0]["id"] == ALIAS
        assert (data_dir / "model-server.json").is_file()
        assert [a["pid"] for a in audit(data_dir, "model_server.left_running")] == [second]

        # The next start adopts it (same pid and start time), so it can be stopped from Jig again.
        proc, log = start_jig(data_dir, port, config=cfg, env=env)
        procs.append(proc)
        wait_health(port, proc=proc, log=log, timeout=400)
        model = httpx.get(f"{base}/model", headers=token(data_dir), timeout=60).json()
        assert model["managed"] is True and model["adopted"] is True and model["pid"] == second
        assert [a["pid"] for a in audit(data_dir, "model_server.adopted")] == [second]

        # jig stop --model: Jig and the model server it supervises both stop.
        stop = jig_cli(cfg, "stop", "--model", "--data-dir", str(data_dir))
        print(stop.stdout, stop.stderr)
        assert stop.returncode == 0 and f"stopping (pid {second})" in stop.stdout
        assert proc.wait(60) == 0, log.read_text(errors="replace")
        deadline = time.monotonic() + 30
        while process_start_time(second) is not None:
            assert time.monotonic() < deadline, "the model server was not stopped"
            time.sleep(0.5)
        assert not listening(model_port)
        assert not (data_dir / "model-server.json").exists()
        stops = audit(data_dir, "power.stop")
        assert [s["scope"] for s in stops] == ["jig", "jig_and_model"]
        assert [a["pid"] for a in audit(data_dir, "model_server.stopped")] == [first, second]
    finally:
        for p in procs:
            kill(p)
        for pid in model_pids:  # only the servers this test's Jig launched
            if process_start_time(pid) is not None:
                AdoptedProcess(pid).kill()
    wait_stopped(data_dir)
