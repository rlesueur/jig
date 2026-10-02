"""Always-on robustness with real processes: the single-instance lock, graceful shutdown (``jig stop`` and a
real Windows session-end message), the bounded model readiness wait, and schedule catch-up after a clock jump."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from jig.config import load_config
from jig.db import iso, now
from jig.errors import ModelServerUnavailable
from jig.instance import EXIT_INSTANCE_LOCKED, InstanceLock, InstanceLocked, read_info, running_instance
from jig.model import ModelClient
from jig.model_server import ModelServerNotReady, ModelServerSupervisor, wait_until_ready
from jig.runtime import Jig

from .conftest import audit_kinds
from .server_helpers import config_path, free_port, kill, start_jig, token, wait_health, wait_stopped

LONG_TASK = ("Write a detailed, 3000-word essay on the history of timekeeping, from sundials to atomic clocks. "
             "Do not use any tools; just write the essay as your answer.")


def db_rows(data_dir: Path, sql: str) -> list[tuple]:
    con = sqlite3.connect(data_dir / "jig.db")
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


# Single-instance lock ------------------------------------------------------------------------------

def test_second_process_on_same_data_dir_is_refused(tmp_path):
    data_dir = tmp_path / "data"
    port_a, port_b = free_port(), free_port()
    a, log_a = start_jig(data_dir, port_a)
    try:
        wait_health(port_a, proc=a, log=log_a)
        holder = running_instance(data_dir)
        assert holder and holder["port"] == port_a and holder["start_reason"] == "manual"

        # A second real 'jig serve' on another port but the same data directory.
        b, log_b = start_jig(data_dir, port_b)
        assert b.wait(60) == EXIT_INSTANCE_LOCKED
        refused = log_b.read_text(errors="replace")
        assert f"Jig is already running for this data folder, at http://127.0.0.1:{port_a}" in refused, refused

        # The runtime's own lock, taken from this (third) process, is refused too.
        with pytest.raises(InstanceLocked, match="already using the data directory"):
            InstanceLock(data_dir).acquire()
        assert httpx.get(f"http://127.0.0.1:{port_a}/health").status_code == 200
    finally:
        kill(a)
    # The OS releases the lock when the holder dies, even when it is killed.
    wait_stopped(data_dir)
    lock = InstanceLock(data_dir)
    lock.acquire(start_reason="test")
    lock.release()


# Graceful shutdown ---------------------------------------------------------------------------------

def _start_with_running_task(data_dir: Path) -> tuple[subprocess.Popen[bytes], Path, int, str]:
    port = free_port()
    proc, log = start_jig(data_dir, port)
    wait_health(port, proc=proc, log=log)
    headers = token(data_dir)
    task = httpx.post(f"http://127.0.0.1:{port}/tasks", headers=headers,
                      json={"title": "long essay", "description": LONG_TASK, "mode": "research"}).json()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if httpx.get(f"http://127.0.0.1:{port}/tasks/{task['id']}", headers=headers).json()["status"] == "running":
            break
        time.sleep(0.2)
    else:
        raise AssertionError("the task never started running")
    time.sleep(2)  # let the model call get under way
    return proc, log, port, task["id"]


def _assert_nothing_left_running(data_dir: Path, task_id: str) -> None:
    assert db_rows(data_dir, f"SELECT status FROM tasks WHERE id = '{task_id}'") == [("queued",)]
    assert db_rows(data_dir, "SELECT count(*) FROM tasks WHERE status IN ('running', 'waiting_approval')") == [(0,)]
    assert db_rows(data_dir, "SELECT count(*) FROM run_steps WHERE status = 'running'") == [(0,)]
    kinds = [k for (k,) in db_rows(data_dir, f"SELECT kind FROM audit WHERE task_id = '{task_id}' ORDER BY id")]
    assert "task.interrupted" in kinds
    assert db_rows(data_dir, "SELECT kind FROM audit ORDER BY id DESC LIMIT 1") == [("runtime.stop",)]
    # The run keeps its checkpoint, so the task resumes from it on the next start.
    assert db_rows(data_dir, f"SELECT status FROM runs WHERE task_id = '{task_id}'") == [("running",)]


def test_jig_stop_is_graceful_and_leaves_no_task_running(tmp_path):
    data_dir = tmp_path / "data"
    proc, log, _, task_id = _start_with_running_task(data_dir)
    try:
        stop = subprocess.run([sys.executable, "-m", "jig.cli", "--config", str(config_path()), "stop",
                               "--data-dir", str(data_dir)], capture_output=True, text=True, timeout=60)
        assert stop.returncode == 0, stop.stderr
        assert proc.wait(60) == 0, log.read_text(errors="replace")
    finally:
        kill(proc)
    assert "graceful shutdown requested: 'jig stop'" in log.read_text(errors="replace")
    _assert_nothing_left_running(data_dir, task_id)
    assert read_info(data_dir) is None and running_instance(data_dir) is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows session-end messages")
def test_windows_logoff_message_shuts_down_gracefully(tmp_path):
    import ctypes
    from ctypes import wintypes

    from jig.lifecycle import WINDOW_CLASS

    data_dir = tmp_path / "data"
    proc, log, _, task_id = _start_with_running_task(data_dir)
    try:
        pid = running_instance(data_dir)["pid"]
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.FindWindowW.restype = wintypes.HWND
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                               wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
        hwnd = user32.FindWindowW(WINDOW_CLASS, f"Jig {pid}")
        assert hwnd, f"no session-end window for pid {pid}"
        result = ctypes.c_size_t()
        # What Windows sends at logoff: WM_QUERYENDSESSION, then WM_ENDSESSION(TRUE, ENDSESSION_LOGOFF).
        assert user32.SendMessageTimeoutW(hwnd, 0x0011, 0, 0x80000000, 0x0002, 10_000, ctypes.byref(result))
        assert result.value == 1  # Jig never vetoes the logoff
        started = time.monotonic()
        assert user32.SendMessageTimeoutW(hwnd, 0x0016, 1, 0x80000000, 0x0002, 60_000, ctypes.byref(result))
        print(f"WM_ENDSESSION handled in {time.monotonic() - started:.1f}s")
        # The message returns only once the shutdown has finished, so everything is already recorded.
        _assert_nothing_left_running(data_dir, task_id)
        assert proc.wait(30) == 0
    finally:
        kill(proc)
    assert "Windows session ending (logoff)" in log.read_text(errors="replace")


# Bounded readiness wait ----------------------------------------------------------------------------

def _dead_endpoint_config(tmp_path: Path, *, timeout_s: float, launch: str = "") -> Path:
    dead = free_port()  # bound and released: nothing listens on it
    path = tmp_path / "dead.toml"
    path.write_text(f"""
[model]
base_url = "http://127.0.0.1:{dead}/v1"
name = "any-model"
connect_timeout_s = 2.0

[model.launch]
{launch}
readiness_timeout_s = {timeout_s}
poll_interval_s = 0.5

[paths]
data_dir = "data"
sandbox_dir = "sandbox"
""", encoding="utf-8")
    return path


async def test_readiness_wait_fails_loudly_within_timeout(tmp_path):
    config = load_config(_dead_endpoint_config(tmp_path, timeout_s=3), data_dir=tmp_path / "data")
    runtime = Jig(config)
    started = time.monotonic()
    with pytest.raises(ModelServerNotReady, match=r"was not ready within 3s .*Last error: .*unreachable"):
        await runtime.start()
    elapsed = time.monotonic() - started
    print(f"failed after {elapsed:.1f}s")
    assert 3 <= elapsed < 7  # the timeout plus at most one connect attempt (connect_timeout_s = 2)
    # A failed start releases everything, including the data-directory lock.
    assert running_instance(config.data_dir) is None


async def test_launched_server_that_exits_fails_at_once(tmp_path):
    launch = f"command = '{sys.executable}'\nargs = [\"-c\", \"import sys; print('boom'); sys.exit(7)\"]"
    config = load_config(_dead_endpoint_config(tmp_path, timeout_s=60, launch=launch), data_dir=tmp_path / "data")
    runtime = Jig(config)
    started = time.monotonic()
    with pytest.raises(ModelServerNotReady, match="exited with code 7"):
        await runtime.start()
    assert time.monotonic() - started < 15
    assert "boom" in (config.data_dir / "logs" / "model-server.log").read_text()


async def _relay(listen_port: int, target_host: str, target_port: int) -> asyncio.Server:
    """A plain TCP relay to the real model server: the model app 'comes up' on listen_port."""

    async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()

    async def handle(client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
        server_r, server_w = await asyncio.open_connection(target_host, target_port)
        await asyncio.gather(pipe(client_r, server_w), pipe(server_r, client_w))

    return await asyncio.start_server(handle, "127.0.0.1", listen_port)


async def test_serve_waits_in_setup_mode_and_starts_when_the_model_comes_up(tmp_path):
    """A model app that isn't up yet (Jig started at sign-in before it, say) keeps Jig in set-up mode, not
    exited, and Jig tries the same model again by itself, starting the agent once it answers."""
    cfg = _dead_endpoint_config(tmp_path, timeout_s=3)
    real = httpx.URL(load_config().model.base_url)
    dead_port = httpx.URL(load_config(cfg).model.base_url).port
    cfg.write_text(cfg.read_text(encoding="utf-8").replace('name = "any-model"\n', ""), encoding="utf-8")
    data, port = tmp_path / "data", free_port()
    proc, out = start_jig(data, port, config=cfg)
    relay = None
    try:
        wait_health(port, proc=proc, log=out, timeout=60)
        auth = token(data)
        status = httpx.get(f"http://127.0.0.1:{port}/status", headers=auth, timeout=10).json()
        assert status["status"] == "setup", status
        assert status["setup"]["recheck"] is True and status["setup"]["configured"] is True
        assert "set-up mode" in out.read_text(errors="replace")

        relay = await _relay(dead_port, real.host, real.port)
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            status = await asyncio.to_thread(
                lambda: httpx.get(f"http://127.0.0.1:{port}/status", headers=auth, timeout=10).json())
            if status.get("status") != "setup":
                break
            await asyncio.sleep(2)
        assert status.get("status") != "setup", status
        assert proc.poll() is None
    finally:
        await asyncio.to_thread(subprocess.run, [sys.executable, "-m", "jig.cli", "--config", str(cfg), "stop",
                                                 "--data-dir", str(data)], capture_output=True, timeout=120)
        await asyncio.to_thread(wait_stopped, data)
        kill(proc)
        if relay is not None:
            relay.close()


async def test_already_running_server_is_not_launched_again(tmp_path, config):
    """With a [model.launch] command and the real server already answering, Jig uses it and starts nothing."""
    from jig.config import ModelLaunchConfig

    launch = ModelLaunchConfig(command=sys.executable, args=["-c", "raise SystemExit(9)"], readiness_timeout_s=5)
    sup = ModelServerSupervisor(launch, tmp_path / "logs")
    client = ModelClient(config.model, label="agent model")
    try:
        info = await sup.ensure_ready([client])
    finally:
        await client.aclose()
    assert info["already_running"] is True and info["launched"] is False and sup.process is None


async def test_wait_until_ready_returns_for_real_server(config):
    client = ModelClient(config.model, label="agent model")
    try:
        info = await wait_until_ready(client, timeout_s=10)
    finally:
        await client.aclose()
    assert info["status"] == "ok"
    assert issubclass(ModelServerNotReady, ModelServerUnavailable)  # existing handlers (HTTP 503) still apply


# Clock jumps ---------------------------------------------------------------------------------------

async def test_clock_jump_catches_up_each_schedule_once(jig):
    store = jig.store
    missed = store.create_schedule(name="digest", prompt="say hi", mode="research", interval_s=600,
                                   start_in_s=3600)
    ahead = store.create_schedule(name="far ahead", prompt="say hi", mode="research", interval_s=60,
                                  start_in_s=3600)
    jig.scheduler.tick()  # baseline heartbeat; nothing is due yet
    assert jig.audit.query(kind="schedule.fired") == []
    # As after two hours asleep: the first schedule is two hours overdue; the second is a day ahead because
    # the clock had been set wrongly when it was scheduled and has since been corrected.
    store.update_schedule(missed["id"], next_run_at=iso(now() - timedelta(hours=2)))
    store.update_schedule(ahead["id"], next_run_at=iso(now() + timedelta(days=1)))
    wall, mono = jig.scheduler._last_clock
    jig.scheduler._last_clock = (wall - 7200, mono - 7200)  # the previous heartbeat, two hours ago
    jig.scheduler.tick()

    jumps = jig.audit.query(kind="scheduler.clock_jump")
    assert len(jumps) == 1
    data = json.loads(jumps[0]["data_json"])
    assert data["overdue_schedules"] == ["digest"] and data["rescheduled"] == ["far ahead"]
    assert data["wall_gap_s"] >= 7200
    assert store.get_schedule(ahead["id"])["next_run_at"] < iso(now() + timedelta(seconds=120))
    fired = [json.loads(r["data_json"]) for r in jig.audit.query(kind="schedule.fired")]
    assert [f["schedule_id"] for f in fired] == [missed["id"]]  # once, not twelve times
    assert fired[0]["missed_runs"] == 12
    assert len(store.list_tasks()) == 1
    assert "scheduler.clock_jump" in audit_kinds(jig)
