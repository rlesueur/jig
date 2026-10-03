"""Jig with the real Ollama and LM Studio on this computer: finding them, what they report, and set-up loading a
model with enough context. Each test skips, saying why, when the app or its server isn't there."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from jig.discovery import (NO_MODELS, _lms, _no_window, find_installed_apps, find_model_servers,
                           lmstudio_default_context, load_in_lmstudio, serving_context, start_app)
from jig.friendly import context_note, context_too_small

from .server_helpers import free_port, kill, start_jig, token, wait_health, wait_stopped

OLLAMA = "http://127.0.0.1:11434"
LMSTUDIO = "http://127.0.0.1:1234"


def _answers(url: str) -> dict | None:
    try:
        r = httpx.get(url, timeout=3)
    except httpx.HTTPError:
        return None
    try:
        return r.json() if r.status_code == 200 else None
    except ValueError:
        return None


@pytest.fixture
def ollama() -> str:
    if not isinstance(v := _answers(f"{OLLAMA}/api/version"), dict) or "version" not in v:
        pytest.skip(f"Ollama isn't running at {OLLAMA}: start the Ollama app to run this test")
    return OLLAMA


@pytest.fixture
def lmstudio() -> str:
    if not isinstance(m := _answers(f"{LMSTUDIO}/api/v0/models"), dict) or not isinstance(m.get("data"), list):
        pytest.skip(f"LM Studio's server isn't running at {LMSTUDIO}: turn it on (lms server start) to run this test")
    return LMSTUDIO


def _lmstudio_llm(root: str) -> dict:
    """The smallest chat model LM Studio has downloaded."""
    models = [m for m in httpx.get(f"{root}/api/v1/models", timeout=10).json()["models"] if m.get("type") == "llm"]
    if not models:
        pytest.skip("LM Studio has no chat model downloaded")
    return min(models, key=lambda m: m.get("size_bytes") or 0)


def _lmstudio_state(root: str, key: str) -> dict:
    return httpx.get(f"{root}/api/v0/models/{key}", timeout=10).json()


def _lmstudio_unload(root: str, key: str) -> None:
    for m in httpx.get(f"{root}/api/v1/models", timeout=10).json()["models"]:
        if m.get("key") == key:
            for instance in m.get("loaded_instances") or []:
                r = httpx.post(f"{root}/api/v1/models/unload", json={"instance_id": instance["id"]}, timeout=120)
                assert r.status_code == 200, r.text


@pytest.fixture
def lmstudio_model(lmstudio: str):
    """A downloaded LM Studio chat model, put back afterwards as it was (unloaded, or loaded at its context)."""
    model = _lmstudio_llm(lmstudio)
    before = _lmstudio_state(lmstudio, model["key"])
    yield model
    _lmstudio_unload(lmstudio, model["key"])
    if before.get("state") == "loaded":
        httpx.post(f"{lmstudio}/api/v1/models/load", timeout=600,
                   json={"model": model["key"], "context_length": before["loaded_context_length"]})


# Ollama --------------------------------------------------------------------------------------------

async def test_ollama_models_are_listed_with_what_ollama_says_they_can_do(ollama):
    found = await find_model_servers()
    server = next((s for s in found["servers"] if s["app"] == "ollama"), None)
    assert server is not None and server["base_url"] == f"{ollama}/v1", found["checked"]
    tags = httpx.get(f"{ollama}/api/tags", timeout=10).json()["models"]
    if not tags:
        assert server["models"] == [] and server["advice"] == NO_MODELS["ollama"]
        return
    listed = {m["id"]: m for m in server["models"]}
    for tag in tags:
        caps = httpx.post(f"{ollama}/api/show", json={"model": tag["name"]}, timeout=30).json().get("capabilities", [])
        name = tag["name"]
        if "embedding" in caps and "completion" not in caps:
            assert name not in listed, "an embedding model can't chat, so it isn't offered"
        else:
            assert listed[name]["tools"] == ("tools" in caps) and listed[name]["vision"] == ("vision" in caps)


async def test_ollama_reports_the_context_it_loaded_a_model_with(ollama):
    tags = [t["name"] for t in httpx.get(f"{ollama}/api/tags", timeout=10).json()["models"]
            if "completion" in httpx.post(f"{ollama}/api/show", json={"model": t["name"]}, timeout=30).json().get(
                "capabilities", [])]
    if not tags:
        pytest.skip("Ollama has no chat model downloaded")
    r = httpx.post(f"{ollama}/v1/chat/completions", timeout=600,
                   json={"model": tags[0], "messages": [{"role": "user", "content": "Say OK."}], "max_tokens": 5})
    assert r.status_code == 200, r.text
    ps = {m["name"]: m["context_length"] for m in httpx.get(f"{ollama}/api/ps", timeout=10).json()["models"]}
    found = await serving_context(f"{ollama}/v1")
    assert found["app"] == "ollama" and found["models"][tags[0]] == ps[tags[0]]


def _ollama_program() -> Path:
    found = [Path(a["path"]) for a in find_installed_apps() if a["app"] == "ollama"]
    program = next((p.with_name("ollama.exe" if sys.platform == "win32" else "ollama") for p in found), None)
    if program is None or not program.is_file():
        pytest.skip("Ollama isn't installed on this computer")
    return program


async def test_ollama_with_nothing_downloaded_is_found_and_says_what_to_do(tmp_path):
    """A second, real Ollama server on a spare port, with an empty models folder."""
    port = free_port()
    proc = subprocess.Popen([str(_ollama_program()), "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL, env={**os.environ, "OLLAMA_HOST": f"127.0.0.1:{port}",
                                                           "OLLAMA_MODELS": str(tmp_path)})
    try:
        deadline = time.monotonic() + 60
        while _answers(f"http://127.0.0.1:{port}/api/version") is None:
            assert proc.poll() is None and time.monotonic() < deadline, "ollama serve didn't start"
            time.sleep(0.5)
        assert httpx.get(f"http://127.0.0.1:{port}/v1/models", timeout=10).json()["data"] is None
        found = await find_model_servers(f"http://127.0.0.1:{port}")
        server = next(s for s in found["servers"] if s["base_url"] == f"http://127.0.0.1:{port}/v1")
        assert server["app"] == "ollama" and server["models"] == [] and server["advice"] == NO_MODELS["ollama"]
    finally:
        kill(proc)


# LM Studio -----------------------------------------------------------------------------------------

async def test_lmstudio_models_are_listed_with_what_lmstudio_says_they_can_do(lmstudio):
    found = await find_model_servers()
    server = next((s for s in found["servers"] if s["app"] == "lmstudio"), None)
    assert server is not None and server["base_url"] == f"{lmstudio}/v1", found["checked"]
    listed = {m["id"]: m for m in server["models"]}
    for m in httpx.get(f"{lmstudio}/api/v0/models", timeout=10).json()["data"]:
        if m["type"] == "embeddings":
            assert m["id"] not in listed, "an embedding model can't chat, so it isn't offered"
        else:
            e = listed[m["id"]]
            assert e["loaded"] == (m["state"] == "loaded") and e["tools"] == ("tool_use" in m.get("capabilities", []))
            assert e["context"] == (m.get("loaded_context_length") if m["state"] == "loaded" else None)


async def test_lmstudio_loads_a_model_with_the_context_asked_for(lmstudio, lmstudio_model):
    key = lmstudio_model["key"]
    _lmstudio_unload(lmstudio, key)
    loaded = await load_in_lmstudio(f"{lmstudio}/v1", key, 16384)
    assert loaded == {"loaded": True, "context": 16384}
    assert (await serving_context(f"{lmstudio}/v1"))["models"][key] == 16384
    assert await load_in_lmstudio(f"{lmstudio}/v1", key, 32768) == {"loaded": False, "context": 16384}
    assert (await load_in_lmstudio(f"{lmstudio}/v1", key, 32768, replace=True))["context"] == 32768
    state = _lmstudio_state(lmstudio, key)
    assert state["state"] == "loaded" and state["loaded_context_length"] == 32768


async def test_lmstudio_refuses_a_model_it_does_not_have(lmstudio):
    with pytest.raises(RuntimeError, match="LM Studio didn't load"):
        await load_in_lmstudio(f"{lmstudio}/v1", "no-such-model-jig-test", 16384)


def test_lmstudio_default_context_is_read_from_its_settings():
    home = Path.home() / ".lmstudio"
    pointer = Path.home() / ".lmstudio-home-pointer"
    if pointer.is_file():
        home = Path(pointer.read_text(encoding="utf-8").strip())
    if not (home / "settings.json").is_file():
        pytest.skip("LM Studio has never run on this computer (no settings.json)")
    setting = json.loads((home / "settings.json").read_text(encoding="utf-8")).get("defaultContextLength")
    value = lmstudio_default_context()
    if setting is None:
        assert value is None
    else:
        assert value == ("max" if setting["type"] == "max" else setting["value"])
    note = context_note(32768, "lmstudio", 32768, 8192)
    assert note and "Settings, then Model Defaults" in note and "32768" in note
    assert context_note(32768, "lmstudio", 32768, "max") is None


def test_context_problems_are_explained_for_each_app():
    ollama = context_too_small(4096, "ollama", 16384)
    assert "Ollama gave the model a 4K context" in ollama and "Context length to 32k" in ollama
    assert "Context Length set to 32768" in context_too_small(8192, "lmstudio", 16384)
    assert "-c 32768" in context_too_small(8192, "llamacpp", 16384)


def _post_stream(port: int, data: Path, body: dict) -> list[dict]:
    with httpx.stream("POST", f"http://127.0.0.1:{port}/setup/apply", headers=token(data), json=body,
                      timeout=900) as r:
        assert r.status_code == 200, r.read()
        return [json.loads(line) for line in r.iter_lines() if line.strip()]


def test_setup_loads_the_lmstudio_model_with_jigs_context_and_health_reloads_it(tmp_path, lmstudio, lmstudio_model):
    """The wizard loads a model LM Studio hasn't loaded at Jig's context; when LM Studio later loads it by itself
    at a smaller one, `jig health` loads it again at Jig's."""
    key = lmstudio_model["key"]
    _lmstudio_unload(lmstudio, key)
    cfg = tmp_path / "jig.toml"
    cfg.write_text('[model]\nbase_url = ""\nname = ""\n\n[vision]\nenabled = false\n\n'
                   '[paths]\ndata_dir = "data"\nsandbox_dir = "sandbox"\n', encoding="utf-8")
    data, port = tmp_path / "data", free_port()
    proc, out = start_jig(data, port, config=cfg)
    try:
        wait_health(port, proc=proc, log=out, timeout=60)
        found = httpx.get(f"http://127.0.0.1:{port}/setup/discover", headers=token(data), timeout=30).json()
        offered = next(s for s in found["servers"] if s["app"] == "lmstudio")
        assert any(m["id"] == key and m["loaded"] is False for m in offered["models"]), offered
        steps = _post_stream(port, data, {"kind": "local", "app": "lmstudio", "base_url": f"{lmstudio}/v1",
                                          "name": key})
        if not steps[-1].get("done"):
            pytest.fail(f"set-up didn't finish with {key}: {steps[-1]}")
        assert {"loading", "tools", "context", "starting"} <= {s.get("step") for s in steps}
        state = _lmstudio_state(lmstudio, key)
        assert state["state"] == "loaded" and state["loaded_context_length"] == 32768
        agent = steps[-1]["capabilities"]["agent"]
        assert agent["context_tokens"] == 32768 and agent["context_app"] == "lmstudio"
    finally:
        subprocess.run([sys.executable, "-m", "jig.cli", "--config", str(cfg), "stop"], capture_output=True,
                       timeout=120)
        wait_stopped(data)
        kill(proc)

    default = lmstudio_default_context()
    if not isinstance(default, int) or default >= 16384:
        return
    # As after LM Studio puts the model away: the next request loads it again at LM Studio's default.
    _lmstudio_unload(lmstudio, key)
    r = httpx.post(f"{lmstudio}/v1/chat/completions", timeout=600,
                   json={"model": key, "messages": [{"role": "user", "content": "Say OK."}], "max_tokens": 5})
    assert r.status_code == 200, r.text
    assert _lmstudio_state(lmstudio, key)["loaded_context_length"] == default
    health = subprocess.run([sys.executable, "-m", "jig.cli", "--config", str(cfg), "health"], capture_output=True,
                            text=True, encoding="utf-8", timeout=900, env={**os.environ, "JIG_DATA_DIR": str(data)})
    assert health.returncode == 0, health.stdout + health.stderr
    assert "Context: 32,768 tokens" in health.stdout and "Model Defaults" in health.stdout, health.stdout
    assert _lmstudio_state(lmstudio, key)["loaded_context_length"] == 32768


def test_jig_starts_lmstudios_server_without_opening_lmstudio():
    # An uninstall leaves ~/.lmstudio (and its lms) behind, so ask whether LM Studio itself is installed.
    if not any(a["app"] == "lmstudio" for a in find_installed_apps()):
        pytest.skip("LM Studio isn't installed on this computer")
    lms = _lms()
    if lms is None:
        pytest.skip("LM Studio is installed but its lms command isn't there")
    was_on = _answers(f"{LMSTUDIO}/api/v0/models") is not None
    subprocess.run([str(lms), "server", "stop"], capture_output=True, timeout=60, **_no_window())
    try:
        assert _answers(f"{LMSTUDIO}/api/v0/models") is None
        start_app("lmstudio")
        deadline = time.monotonic() + 60
        while _answers(f"{LMSTUDIO}/api/v0/models") is None:
            assert time.monotonic() < deadline, "LM Studio's server didn't answer after Jig started it"
            time.sleep(0.5)
    finally:
        if not was_on:
            subprocess.run([str(lms), "server", "stop"], capture_output=True, timeout=60, **_no_window())


# Both apps -----------------------------------------------------------------------------------------

async def test_installed_apps_are_named_whether_or_not_their_servers_run():
    installed = find_installed_apps()
    if not installed:
        pytest.skip("neither Ollama nor LM Studio is installed on this computer")
    for app in installed:
        assert Path(app["path"]).exists() and app["label"] in ("Ollama", "LM Studio")
    found = await find_model_servers()
    running = {s["app"] for s in found["servers"]}
    for app in found["installed"]:
        assert app["running"] == (app["app"] in running)
        assert (app["advice"] is None) == app["running"]
        if not app["running"]:
            assert app["label"] in app["advice"] and "Start it for me" in app["advice"]
