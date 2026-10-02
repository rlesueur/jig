"""Easy set-up, for real: the measured model suggestion, the settings file the set-up page writes, the
plain-English problems, finding the model apps and graphics card on this computer, a real ``jig serve`` in
set-up mode that only starts its agent once a model passes the real checks, and the Windows tray icon."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import httpx
import pytest

from jig import settings_file
from jig.cloud import CloudConsentRequired
from jig.config import load_config
from jig.discovery import find_gpu, find_model_servers
from jig.errors import ConfigError, ModelKeyMissing, ModelServerUnavailable
from jig.friendly import explain
from jig.recommend import ADVISED_CONTEXT, HEADROOM_GB, MEASUREMENTS, recommend

from .server_helpers import free_port, kill, start_jig, token, wait_health, wait_stopped

REAL_MODEL = load_config().model.base_url


# The measured suggestion ---------------------------------------------------------------------------

def test_recommendation_uses_only_measured_figures():
    big, small = MEASUREMENTS
    assert (big.context, big.gpu_gb) == (32768, 10.6) and (small.context, small.gpu_gb) == (16384, 8.1)

    roomy = recommend(32.0)
    assert roomy["measured"] and roomy["fits"]["context"] == ADVISED_CONTEXT
    assert "-c 32768" in roomy["get_it"]["llamacpp"]["command"]
    assert "Context length to 32k" in roomy["get_it"]["ollama"]["steps"][0]

    just = recommend(small.gpu_gb + HEADROOM_GB)
    assert just["fits"]["context"] == 16384 and "Jig works best with 32K" in just["text"]

    too_small = recommend(6.0)
    assert too_small["fits"] is None and not too_small["measured"]
    assert "haven't been measured" in too_small["text"]

    none = recommend(None)
    assert none["fits"] is None and "didn't find a graphics card" in none["text"]


# The settings file -----------------------------------------------------------------------------------

def test_settings_for_each_kind_of_choice():
    local = settings_file.sections_for({"kind": "local", "app": "llamacpp", "base_url": "http://127.0.0.1:8080/v1/",
                                        "name": "granite"})
    assert local["model"]["base_url"] == "http://127.0.0.1:8080/v1" and local["model"]["name"] == "granite"
    assert "launch" not in local["model"] and local["vision"] == {"enabled": False}

    ollama = settings_file.sections_for({"kind": "local", "app": "ollama", "base_url": "http://127.0.0.1:11434/v1"})
    shipped = tomllib.loads((settings_file.PROFILES_DIR / "ollama.toml").read_text(encoding="utf-8"))["model"]
    for key, value in shipped.items():
        if key not in ("launch", "base_url", "name"):
            assert ollama["model"][key] == value, key

    cloud = settings_file.sections_for({"kind": "cloud", "provider": "anthropic"})
    assert cloud["model"]["provider"] == "anthropic" and cloud["vision"] == {"enabled": False}

    for bad in ({"kind": "local", "app": "llamacpp"}, {"kind": "cloud", "provider": "nobody"}, {"kind": "magic"},
                {"kind": "local", "app": "unheard-of", "base_url": "http://127.0.0.1:1/v1"}):
        with pytest.raises(ConfigError):
            settings_file.sections_for(bad)


def test_settings_file_round_trip_and_overlay(tmp_path):
    cfg = tmp_path / "jig.toml"
    cfg.write_text('[model]\nbase_url = ""\nname = ""\n\n[paths]\ndata_dir = "data"\nsandbox_dir = "sandbox"\n',
                   encoding="utf-8")
    data = tmp_path / "data"
    assert load_config(cfg).model.base_url == ""

    sections = settings_file.sections_for({"kind": "local", "app": "llamacpp", "base_url": "http://127.0.0.1:8123/v1",
                                           "name": 'odd "name"'})
    settings_file.save(data, sections)
    settings_file.save(data, {"sandbox": {"backend": "container"}})
    saved = settings_file.load(data)
    assert saved["model"]["name"] == 'odd "name"' and saved["sandbox"] == {"backend": "container"}

    config = load_config(cfg)
    assert config.model.base_url == "http://127.0.0.1:8123/v1" and config.model.name == 'odd "name"'
    assert config.sandbox.backend == "container" and config.settings_file is not None
    assert '[model]\nbase_url = ""' in cfg.read_text(encoding="utf-8")  # jig.toml itself is never rewritten

    with pytest.raises(ConfigError, match="doesn't hold"):
        settings_file.save(data, {"server": {"port": 1}})


# Plain-English problems ----------------------------------------------------------------------------

def test_problems_are_explained_plainly():
    llama = explain(ModelServerUnavailable("unreachable", reason="unreachable"),
                    load_config())
    assert llama.kind == "model" and "llama.cpp" in llama.text and "Traceback" not in llama.text

    lms = explain(ModelServerUnavailable("http://127.0.0.1:1234/v1 unreachable", reason="unreachable"))
    assert "LM Studio" in lms.text

    missing = explain(ModelServerUnavailable("x", reason="not_served", available=["a", "b"]))
    assert missing.kind == "model" and "It has: a, b" in missing.text

    key = explain(ModelKeyMissing("no key", secret="model-key.anthropic"))
    assert key.kind == "key" and "Anthropic" in key.text

    consent = explain(CloudConsentRequired("needs consent"))
    assert consent.kind == "consent" and "won't send anything" in consent.text


# Finding what's on this computer -----------------------------------------------------------------

async def test_finds_the_running_model_server_and_nothing_where_nothing_runs():
    real = httpx.URL(REAL_MODEL)
    dead = free_port()
    found = await find_model_servers(f"http://127.0.0.1:{dead}", skip_port=None)
    by_port = {s["port"]: s for s in found["servers"]}
    assert real.port in by_port, found["checked"]
    server = by_port[real.port]
    assert server["base_url"] == f"http://127.0.0.1:{real.port}/v1" and server["models"]
    if real.port == 8080:
        assert server["app"] == "llamacpp" and all(m["loaded"] for m in server["models"])
    assert dead not in by_port and any(c["port"] == dead and not c["found"] for c in found["checked"])

    skipped = await find_model_servers(skip_port=real.port)
    assert all(c["port"] != real.port for c in skipped["checked"])


def test_graphics_card_is_read_from_this_computer():
    gpu = find_gpu()
    if not gpu["found"]:
        pytest.skip(f"no graphics card memory readable here ({gpu['note']})")
    assert gpu["total_gb"] > 0 and gpu["name"] and gpu["source"]


# A real server in set-up mode ----------------------------------------------------------------------

def _post_stream(port: int, path: str, data: Path, body: dict) -> list[dict]:
    with httpx.stream("POST", f"http://127.0.0.1:{port}{path}", headers=token(data), json=body,
                      timeout=600) as r:
        assert r.status_code == 200, r.read()
        return [json.loads(line) for line in r.iter_lines() if line.strip()]


def test_setup_mode_keeps_the_agent_off_until_a_model_passes(tmp_path):
    cfg = tmp_path / "jig.toml"
    cfg.write_text('[model]\nbase_url = ""\nname = ""\n\n[vision]\nenabled = false\n\n'
                   '[paths]\ndata_dir = "data"\nsandbox_dir = "sandbox"\n', encoding="utf-8")
    data, port = tmp_path / "data", free_port()
    base = f"http://127.0.0.1:{port}"
    proc, out = start_jig(data, port, config=cfg)
    try:
        wait_health(port, proc=proc, log=out, timeout=60)
        auth = token(data)
        status = httpx.get(f"{base}/status", headers=auth, timeout=10).json()
        assert status["status"] == "setup" and status["setup"]["configured"] is False
        assert status["setup"]["problem"]["kind"] == "model" and status["setup"]["recheck"] is False
        assert "set-up mode" in out.read_text(errors="replace")
        # Nothing of the agent runs: no tasks, no chat.
        assert httpx.get(f"{base}/tasks", headers=auth, timeout=10).status_code == 503

        found = httpx.get(f"{base}/setup/discover", headers=auth, timeout=30).json()
        assert any(s["base_url"].rstrip("/") == REAL_MODEL.rstrip("/") for s in found["servers"]), found
        gpu = httpx.get(f"{base}/setup/gpu", headers=auth, timeout=30).json()
        assert "recommendation" in gpu and gpu["recommendation"]["text"]

        # A model app that isn't there fails the check, and nothing is saved.
        dead = free_port()
        lines = _post_stream(port, "/setup/apply", data, {"kind": "local", "app": "other",
                                                         "base_url": f"http://127.0.0.1:{dead}/v1"})
        assert lines[-1]["error"]["kind"] == "model" and f"127.0.0.1:{dead}" in lines[-1]["error"]["text"]
        assert not (data / "settings.toml").exists()
        assert httpx.get(f"{base}/status", headers=auth, timeout=10).json()["status"] == "setup"

        # Cloud: the key goes into the vault; choosing it needs the person's OK first.
        put = httpx.put(f"{base}/setup/cloud/key", headers=auth, timeout=10,
                        json={"provider": "anthropic", "key": "sk-ant-not-a-real-key"})
        assert put.status_code == 200 and put.json()["secret"] == "model-key.anthropic"
        providers = {p["id"]: p for p in httpx.get(f"{base}/setup/providers", headers=auth, timeout=10).json()}
        assert providers["anthropic"]["key_stored"] is True
        no_ok = httpx.post(f"{base}/setup/apply", headers=auth, timeout=30,
                           json={"kind": "cloud", "provider": "anthropic"})
        assert no_ok.status_code == 400 and "needs your OK" in no_ok.text, no_ok.text
        assert httpx.put(f"{base}/setup/cloud/key", headers=auth, timeout=10,
                         json={"provider": "anthropic", "key": "has spaces"}).status_code == 400
        assert httpx.delete(f"{base}/setup/cloud/key/anthropic", headers=auth, timeout=10).json() == {"deleted": True}

        # The real model passes every check, is saved, and the agent starts with it.
        steps = _post_stream(port, "/setup/apply", data, {"kind": "local", "app": "llamacpp", "base_url": REAL_MODEL})
        assert steps[-1].get("done"), steps[-1]
        assert {"reach", "tools", "saving", "starting"} <= {s.get("step") for s in steps}
        assert settings_file.load(data)["model"]["base_url"] == REAL_MODEL.rstrip("/")
        running = httpx.get(f"{base}/status", headers=auth, timeout=10).json()
        assert running["status"] != "setup"
        assert httpx.get(f"{base}/tasks", headers=auth, timeout=10).status_code == 200

        # `jig ui` finds this Jig on its own port, signed in.
        ui = subprocess.run([sys.executable, "-m", "jig.cli", "--config", str(cfg), "ui", "--print-url"],
                            capture_output=True, text=True, timeout=60)
        assert ui.returncode == 0 and ui.stdout.strip().startswith(f"{base}/#code="), ui.stdout + ui.stderr
    finally:
        subprocess.run([sys.executable, "-m", "jig.cli", "--config", str(cfg), "stop"], capture_output=True,
                       timeout=120)
        wait_stopped(data)
        kill(proc)

    # The choice is kept: next time Jig starts straight away with it.
    proc, out = start_jig(data, port, config=cfg)
    try:
        wait_health(port, proc=proc, log=out, timeout=240)
        assert httpx.get(f"{base}/status", headers=token(data), timeout=10).json()["status"] != "setup"
    finally:
        subprocess.run([sys.executable, "-m", "jig.cli", "--config", str(cfg), "stop"], capture_output=True,
                       timeout=120)
        wait_stopped(data)
        kill(proc)


# The Windows tray icon -----------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="the tray icon is for Windows")
def test_tray_starts_once_per_data_folder_and_quits(tmp_path):
    import ctypes

    from jig.tray import instance_name

    cfg = tmp_path / "jig.toml"
    cfg.write_text('[model]\nbase_url = ""\nname = ""\n\n[paths]\ndata_dir = "data"\nsandbox_dir = "sandbox"\n',
                   encoding="utf-8")
    data = tmp_path / "data"
    assert instance_name(data) == instance_name(Path(str(data).upper()))
    assert instance_name(data) != instance_name(tmp_path / "other")

    find = ctypes.windll.user32.FindWindowW
    find.restype = ctypes.c_void_p
    tray_cmd = [sys.executable, "-m", "jig.tray", "--config", str(cfg), "--port", str(free_port())]
    proc = subprocess.Popen([*tray_cmd, "--no-start"])
    try:
        deadline = time.monotonic() + 30
        while not find(instance_name(data), None) and time.monotonic() < deadline:
            assert proc.poll() is None, "the tray exited"
            time.sleep(0.2)
        assert find(instance_name(data), None), "the tray's window never appeared"
        # A second tray for the same data folder hands over to the first and exits.
        second = subprocess.run([*tray_cmd, "--no-start"], timeout=30)
        assert second.returncode == 0 and proc.poll() is None
        quit_ = subprocess.run([*tray_cmd, "--quit"], timeout=200)
        assert quit_.returncode == 0
        assert proc.wait(30) == 0
        assert not find(instance_name(data), None)
        assert "tray ready" in (data / "logs" / "tray.log").read_text(encoding="utf-8")
    finally:
        kill(proc)
