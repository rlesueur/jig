"""``[model.launch]`` when the endpoint is already in use, and supervision of a server Jig launched. Real
processes only: the developer's model server on 8080 (used read-only), a real HTTP server that is not a model
server, and a real llama.cpp server launched on a free port with a small model.

The launched-server tests need a stock llama.cpp build and a small GGUF model. Point JIG_TEST_LLAMA_SERVER
and JIG_TEST_SMALL_MODEL at them; by default they look in the developer's usual places and are skipped if
neither is there.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from jig.config import EndpointConfig, ModelLaunchConfig, load_config
from jig.model import ModelClient
from jig.model_server import ModelServerNotReady, ModelServerSupervisor
from jig.runtime import Jig

from .server_helpers import free_port

LLAMA_SERVER = Path(os.environ.get("JIG_TEST_LLAMA_SERVER")
                    or r"C:\Users\you\tools\llama-upstream-b8664-cuda131\llama-server.exe")
SMALL_MODEL = Path(os.environ.get("JIG_TEST_SMALL_MODEL")
                   or r"C:\Users\you\models\jig-research\granite-4.2-8b-Q4_K_M.gguf")
needs_llama = pytest.mark.skipif(not (LLAMA_SERVER.is_file() and SMALL_MODEL.is_file()),
                                 reason="set JIG_TEST_LLAMA_SERVER and JIG_TEST_SMALL_MODEL")
ALIAS = "jig-test-small"


def _marker_command(marker: Path) -> ModelLaunchConfig:
    """A launch command that leaves a file behind if it ever runs, so the tests can prove it did not."""
    return ModelLaunchConfig(command=sys.executable,
                             args=["-c", f"open({str(marker)!r}, 'w').write('launched')"],
                             readiness_timeout_s=10, poll_interval_s=0.5)


def _llama_args(port: int) -> list[str]:
    return ["-m", str(SMALL_MODEL), "--host", "127.0.0.1", "--port", str(port), "-ngl", "99", "-c", "4096",
            "--parallel", "1", "--alias", ALIAS]


def _endpoint(port: int) -> EndpointConfig:
    return EndpointConfig(base_url=f"http://127.0.0.1:{port}/v1", name=ALIAS, connect_timeout_s=3.0)


def _listening(port: int) -> bool:
    try:
        httpx.get(f"http://127.0.0.1:{port}/health", timeout=2)
        return True
    except httpx.HTTPError:
        return False


# Already running -----------------------------------------------------------------------------------

async def test_real_running_server_is_used_and_audited(tmp_path, config):
    """The developer's real server on 8080 already serves the model: Jig launches nothing, runs the real
    capability checks against it and audits 'model server already running; not launching'."""
    marker = tmp_path / "launched.txt"
    cfg = replace(config, model_launch=_marker_command(marker))
    runtime = Jig(cfg)
    try:
        await runtime.start(run_scheduler=False, check_capabilities=True)
        rows = runtime.audit.query(limit=100)
        reused = [r for r in rows if r["kind"] == "model_server.already_running"]
        assert len(reused) == 1 and reused[0]["summary"] == "model server already running; not launching"
        assert runtime.capabilities["agent"]  # the real probes ran and passed
        assert runtime.model_server.process is None and runtime.model_server.reused_running
    finally:
        await runtime.stop()
    assert not marker.exists(), "the launch command ran although the server was already up"
    # Jig did not start that server, so stopping Jig leaves it running.
    assert httpx.get(config.model.base_url.rstrip("/") + "/models", timeout=10).status_code == 200


async def test_port_held_by_something_else_fails_loudly(tmp_path):
    """A real HTTP server that is not a model server holds the port: Jig refuses and launches nothing."""
    port = free_port()
    other = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
                             cwd=tmp_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    marker = tmp_path / "launched.txt"
    client = ModelClient(_endpoint(port), label="agent model")
    try:
        deadline = time.monotonic() + 20
        while not _listening(port):
            assert time.monotonic() < deadline, "http.server did not start"
            await asyncio.sleep(0.2)
        sup = ModelServerSupervisor(_marker_command(marker), tmp_path / "logs")
        with pytest.raises(ModelServerNotReady, match=rf"127\.0\.0\.1:{port} is already in use by something that is "
                                                      r"not a working model server.*returned 404"):
            await sup.ensure_ready([client])
        assert sup.process is None
    finally:
        await client.aclose()
        other.kill()
        other.wait(10)
    assert not marker.exists()


# Launched by Jig -----------------------------------------------------------------------------------

@needs_llama
async def test_server_started_by_hand_is_not_launched_again(tmp_path):
    """A real llama-server started outside Jig, checked straight away (while it may still be loading and
    answering 503): Jig waits for it rather than launching a second one, and does not stop it."""
    port = free_port()
    by_hand = subprocess.Popen([str(LLAMA_SERVER), *_llama_args(port)], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    marker = tmp_path / "launched.txt"
    client = ModelClient(_endpoint(port), label="agent model")
    try:
        deadline = time.monotonic() + 30
        while not _listening(port):
            assert by_hand.poll() is None, f"llama-server exited with {by_hand.returncode}"
            assert time.monotonic() < deadline, "llama-server did not open its port"
            await asyncio.sleep(0.1)
        launch = replace(_marker_command(marker), readiness_timeout_s=180)
        sup = ModelServerSupervisor(launch, tmp_path / "logs")
        info = await sup.ensure_ready([client])
        assert info["already_running"] is True and info["launched"] is False
        await sup.stop()
        assert by_hand.poll() is None, "Jig stopped a server it did not start"
    finally:
        await client.aclose()
        by_hand.kill()
        by_hand.wait(30)
    assert not marker.exists()


@needs_llama
async def test_launched_server_is_supervised_restarted_and_stopped(tmp_path):
    """Jig launches a real llama-server on a free port, restarts it after a real crash (the process is
    killed), gives up after max_restarts, and stops the server it launched when Jig stops."""
    port = free_port()
    launch = ModelLaunchConfig(command=str(LLAMA_SERVER), args=_llama_args(port), readiness_timeout_s=180,
                               poll_interval_s=0.5, max_restarts=1, restart_delay_s=1.0)
    audit: list[tuple[str, str]] = []
    sup = ModelServerSupervisor(launch, tmp_path / "logs",
                                audit=lambda kind, summary, **_: audit.append((kind, summary)))
    client = ModelClient(_endpoint(port), label="agent model")
    try:
        info = await sup.ensure_ready([client])
        assert info["launched"] is True and info["already_running"] is False
        first = sup.process
        assert first is not None and first.poll() is None

        first.kill()  # a real crash of the server Jig launched
        deadline = time.monotonic() + 240
        while not audit or audit[-1][0] != "model_server.restarted":
            assert time.monotonic() < deadline, f"not restarted; audit: {audit}"
            await asyncio.sleep(0.5)
        second = sup.process
        assert second is not first and second.poll() is None and sup.restarts == 1
        assert (await client.health())["model"] == ALIAS
        assert [k for k, _ in audit] == ["model_server.exited", "model_server.restarted"]

        second.kill()  # a second crash in a row is more than max_restarts = 1
        deadline = time.monotonic() + 30
        while not audit or audit[-1][0] != "model_server.gave_up":
            assert time.monotonic() < deadline, f"did not give up; audit: {audit}"
            await asyncio.sleep(0.5)
        assert "more than max_restarts = 1" in audit[-1][1] and sup.process is None

        # Launch once more and check that stop() ends the server Jig started.
        sup = ModelServerSupervisor(launch, tmp_path / "logs")
        await sup.ensure_ready([client])
        third = sup.process
        await sup.stop()
        assert third.poll() is not None and not _listening(port)
    finally:
        await sup.stop()
        await client.aclose()
    log = (tmp_path / "logs" / "model-server.log").read_text(errors="replace")
    assert log.count("starting: ") == 3


def test_launch_restart_settings_are_validated(tmp_path):
    cfg = tmp_path / "bad.toml"
    cfg.write_text('[model]\nbase_url = "http://127.0.0.1:1/v1"\n[model.launch]\nmax_restarts = -1\n',
                   encoding="utf-8")
    from jig.errors import ConfigError

    with pytest.raises(ConfigError, match="max_restarts"):
        load_config(cfg)
