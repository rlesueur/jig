"""What people say to Jig never reaches the log files Jig writes. A real ``jig serve`` (with the developer's
model server on 8080, used read-only) is given a memory, a memory search, a chat and requests with words in
their query strings, both with ``--log-file`` (jig.log, as when Jig starts with Windows) and logging to the
console (as a service does); neither log may contain those words. The rules themselves are checked with real
exceptions, a real HTTP server and real llama.cpp launch settings."""

from __future__ import annotations

import http.server
import json
import logging
import threading
from pathlib import Path

import httpx
import pytest
import websockets.sync.client

from jig.config import load_config
from jig.errors import ConfigError
from jig.logs import (HIDDEN_QUERY, content_free, format_exception, hide_query, log_uncaught_exceptions,
                      protect_logging, verbose_model_server)

from .server_helpers import config_path, free_port, kill, start_jig, token, wait_health, wait_stopped

WORDS = {
    "memory": "Wombatlantern",
    "search": "Okapiribbon",
    "old_search": "Gerbilcanoe",
    "query": "Tapirsaucer",
    "socket": "Marmosetkettle",
    "chat": "Lemurtrombone",
}


@pytest.mark.parametrize("to_file", [True, False], ids=["jig.log", "console"])
def test_serve_logs_hold_no_user_words(tmp_path, to_file):
    data = tmp_path / "data"
    port = free_port()
    proc, console = start_jig(data, port, extra=["--log-file"] if to_file else [])
    base = f"http://127.0.0.1:{port}"
    try:
        wait_health(port, proc=proc, log=console)
        h = token(data)
        with httpx.Client(base_url=base, headers=h, timeout=300) as c:
            m = c.post("/memory", json={"content": f"My tortoise is called {WORDS['memory']}"})
            assert m.status_code == 201, m.text
            found = c.post("/memory/search", json={"q": WORDS["memory"]})
            assert found.status_code == 200 and [r["id"] for r in found.json()] == [m.json()["id"]]
            assert c.post("/memory/search", json={"q": WORDS["search"]}).json() == []
            old = c.get("/memory", params={"q": WORDS["old_search"]})
            assert old.status_code == 400 and "POST /memory/search" in old.text
            assert c.get("/tasks", params={"status": WORDS["query"]}).status_code in (200, 400, 422)
            lines = []
            with c.stream("POST", "/chat", json={"message": f"Reply with one word: the name {WORDS['chat']}."}) as r:
                assert r.status_code == 200
                lines = [json.loads(line) for line in r.iter_lines() if line]
            assert lines[-1]["type"] == "done", lines[-1]
        uri = f"ws://127.0.0.1:{port}/events?probe={WORDS['socket']}"
        with websockets.sync.client.connect(uri, additional_headers=h) as ws:
            assert json.loads(ws.recv())["snapshot"] is True
        assert httpx.post(f"{base}/power/stop", headers=h, json={"scope": "jig", "confirm": True}).status_code == 202
        wait_stopped(data)
        proc.wait(60)
    finally:
        kill(proc)
    logs = {p.name: p.read_text(encoding="utf-8", errors="replace") for p in (data / "logs").glob("*.log*")}
    logs["console"] = console.read_text(encoding="utf-8", errors="replace")
    written = "\n".join(logs.values())
    assert ("jig.log" in logs) == to_file
    for what, word in WORDS.items():
        assert word.lower() not in written.lower(), f"the {what} word {word!r} is in {[n for n, t in logs.items() if word in t]}"
    access = [line for line in written.splitlines() if '"GET /memory' in line or '"GET /tasks' in line]
    assert access and all(HIDDEN_QUERY in line for line in access), access
    assert f"WebSocket /events{HIDDEN_QUERY}" in written
    assert "HTTP Request:" not in written  # httpx's line for each request to the model


def test_outgoing_requests_are_not_logged(tmp_path):
    """httpx logs each request's full URL (a web search's words included) at INFO; Jig's logs keep warnings."""
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *_):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    root = logging.getLogger()
    handler = logging.FileHandler(tmp_path / "out.log", encoding="utf-8")
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        protect_logging()
        httpx.get(f"http://127.0.0.1:{server.server_address[1]}/search?q=Quollhammock", timeout=10)
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
        handler.close()
        server.shutdown()
    assert "Quollhammock" not in (tmp_path / "out.log").read_text(encoding="utf-8")


def _raise_chained() -> None:
    """The words come from data, as in Jig: a traceback shows source lines, which hold no conversation."""
    key, said = "Dugong" + "spatula", "Binturong" + "ladle"
    try:
        {}[key]
    except KeyError as exc:
        raise ValueError(f"could not use the tool result: {said}") from exc


def test_tracebacks_keep_types_and_frames_but_not_messages(tmp_path):
    try:
        _raise_chained()
    except ValueError as exc:
        text = format_exception(type(exc), exc, exc.__traceback__)
    assert "Dugongspatula" not in text and "Binturongladle" not in text
    assert "KeyError (15 characters)" in text and "ValueError (45 characters)" in text
    assert "direct cause" in text and "_raise_chained" in text and text.index("KeyError") < text.index("ValueError")

    log = logging.getLogger("jig.test.crash")
    handler = content_free(logging.FileHandler(tmp_path / "jig.log", encoding="utf-8"))
    log.addHandler(handler)
    try:
        try:
            _raise_chained()
        except ValueError:
            log.exception("task t1 crashed")
    finally:
        log.removeHandler(handler)
        handler.close()
    written = (tmp_path / "jig.log").read_text(encoding="utf-8")
    assert "task t1 crashed" in written and "ValueError (45 characters)" in written
    assert "Binturongladle" not in written and "Dugongspatula" not in written


def test_uncaught_thread_exceptions_are_logged_without_their_message(tmp_path):
    root = logging.getLogger()
    handler = content_free(logging.FileHandler(tmp_path / "jig.log", encoding="utf-8"))
    root.addHandler(handler)
    saved = threading.excepthook
    try:
        log_uncaught_exceptions()

        said = "Pademelon" + "teapot"

        def boom() -> None:
            raise RuntimeError(f"the reply said {said}")

        t = threading.Thread(target=boom, name="worker-1")
        t.start()
        t.join()
    finally:
        threading.excepthook = saved
        import sys
        sys.excepthook = sys.__excepthook__
        root.removeHandler(handler)
        handler.close()
    written = (tmp_path / "jig.log").read_text(encoding="utf-8")
    assert "uncaught exception in worker-1" in written and "RuntimeError (30 characters)" in written
    assert "Pademelonteapot" not in written


def test_hide_query():
    assert hide_query("/memory?q=walking") == f"/memory{HIDDEN_QUERY}"
    assert hide_query("/health") == "/health"


@pytest.mark.parametrize("args, env, found", [
    (["-v"], {}, "-v"),
    (["--verbose"], {}, "--verbose"),
    (["--log-verbose"], {}, "--log-verbose"),
    (["-lv", "4"], {}, "-lv 4"),
    (["--log-verbosity=5"], {}, "--log-verbosity 5"),
    (["-m", "x.gguf"], {"LLAMA_LOG_VERBOSITY": "4"}, "LLAMA_LOG_VERBOSITY=4"),
    (["-lv", "3", "-m", "x.gguf"], {}, None),
    (["-m", "x.gguf", "-c", "4096"], {"LLAMA_LOG_VERBOSITY": "2"}, None),
])
def test_verbose_llama_settings_are_found(args, env, found):
    assert verbose_model_server(args, env) == found


def test_config_refuses_a_model_server_that_logs_conversations(tmp_path):
    """Checked with the real llama-server: at -lv 4 or -v it writes prompts and replies to its output, which
    Jig saves as model-server.log; at the default (3) it writes none."""
    base = config_path().read_text(encoding="utf-8")
    bad = tmp_path / "verbose.toml"
    bad.write_text(base + '\n[model.launch]\ncommand = "llama-server"\nargs = ["-m", "x.gguf", "--verbose"]\n',
                   encoding="utf-8")
    with pytest.raises(ConfigError, match=r"\[model.launch\] --verbose makes llama.cpp's server write every prompt"):
        load_config(bad)
    ok = tmp_path / "quiet.toml"
    ok.write_text(base + '\n[model.launch]\ncommand = "llama-server"\nargs = ["-m", "x.gguf", "-lv", "3"]\n',
                  encoding="utf-8")
    assert load_config(ok).model_launch.args[-1] == "3"
