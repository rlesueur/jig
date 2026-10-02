"""The overnight queue must not let one entry that cannot start (e.g. a model server refused for lack of VRAM)
block the entries after it. Real runner, real configs, a real local HTTP server occupying the port."""

from __future__ import annotations

import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from jigbench import overnight as overnight_mod
from jigbench.compute_policy import NoPolicy
from jigbench.paths import RESULTS
from jigbench.provenance import model_catalogue

PORT = 8097


class _Health(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a) -> None:
        pass


def _cfg(name: str, attacks: list[str]) -> dict:
    return {"name": name, "experiment": "d1", "suites": ["banking"], "attacks": attacks,
            "user_tasks_per_suite": 1, "injection_tasks_per_suite": 1, "seeds": [0],
            "endpoints": {"guard": {"server": {"model": "granite42-8b", "port": PORT, "ctx": 2048, "parallel": 1}},
                          "zagent": {"base_url": "http://127.0.0.1:9/v1"}},
            "agent": {"endpoint": "zagent", "name": "none"},
            "conditions": [{"id": "c", "reviewer": "guardian",
                            "sentinel": {"endpoint": "guard", "name": "granite42-8b"}, "mode": "action"}]}


def test_queue_moves_on_when_an_entry_cannot_start(tmp_path: Path, monkeypatch, caplog) -> None:
    if not (model_catalogue().get("granite42-8b", {}).get("lock") or {}).get("verified"):
        pytest.skip("needs the verified granite42-8b download to construct the server")
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), _Health)
    except OSError:
        pytest.skip(f"port {PORT} is in use")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    names = ["unit-overnight-blocked", "unit-overnight-empty"]
    paths = []
    for name, attacks in zip(names, (["none"], [])):
        p = tmp_path / f"{name}.yaml"
        p.write_text(yaml.safe_dump(_cfg(name, attacks)), encoding="utf-8")
        paths.append(str(p))
    queue = tmp_path / "queue.yaml"
    queue.write_text(yaml.safe_dump({"queue": paths}), encoding="utf-8")
    monkeypatch.setattr(overnight_mod, "LOCK", tmp_path / "overnight.lock")
    try:
        with caplog.at_level("INFO", logger="jigbench"):
            code = overnight_mod.overnight(queue, policy=NoPolicy())
    finally:
        srv.shutdown()
        for name in names:
            shutil.rmtree(RESULTS / "d1" / name, ignore_errors=True)
    assert code == 1
    text = caplog.text
    assert "unit-overnight-blocked.yaml could not run now (ServerError" in text
    assert "unit-overnight-empty: 0 trials to run" in text
    assert "queue incomplete: unit-overnight-blocked.yaml" in text
