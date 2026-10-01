"""Container mode switches autostart off: the explicit ``deployment`` flag, the CLI, the API of a real
``jig serve``, and ``jig autostart enable`` inside a real container. Nothing is mocked."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from jig.autostart import CONTAINER_MODE_REASON
from jig.config import load_config
from jig.errors import ConfigError

from .server_helpers import config_path, free_port, kill, start_jig, token, wait_health

ROOT = Path(__file__).resolve().parent.parent
DEPLOY_CONFIG = ROOT / "deploy" / "jig.toml"
IMAGE = os.environ.get("JIG_TEST_IMAGE", "ghcr.io/rlesueur/jig:0.1.0")


def _container_config(tmp_path: Path) -> Path:
    """The developer's host config, marked as a container deployment (directory sandbox, real model)."""
    path = tmp_path / "container.toml"
    path.write_text('deployment = "container"\n' + config_path().read_text(encoding="utf-8"), encoding="utf-8")
    return path


def _cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "jig.cli", *args], capture_output=True, text=True, timeout=120,
                          env={**os.environ, **(env or {})})


def test_deployment_flag_is_explicit(tmp_path, monkeypatch):
    monkeypatch.delenv("JIG_DEPLOYMENT", raising=False)
    assert load_config(DEPLOY_CONFIG, data_dir=tmp_path).deployment == "container"
    assert load_config(config_path(), data_dir=tmp_path).deployment == "host"
    monkeypatch.setenv("JIG_DEPLOYMENT", "container")
    assert load_config(config_path(), data_dir=tmp_path).deployment == "container"
    monkeypatch.setenv("JIG_DEPLOYMENT", "docker")
    with pytest.raises(ConfigError, match="'host' or 'container'"):
        load_config(config_path(), data_dir=tmp_path)
    assert "JIG_DEPLOYMENT=container" in (ROOT / "Dockerfile").read_text(encoding="utf-8")


def test_cli_refuses_enable_in_container_mode(tmp_path):
    entry = r"\JigTest\Container Mode"
    for action in ("enable", "disable"):
        r = _cli("--config", str(DEPLOY_CONFIG), "autostart", action, "--yes" if action == "enable" else "--json",
                 "--data-dir", str(tmp_path / "data"), "--entry", entry)
        assert r.returncode == 1, r.stdout + r.stderr
        assert "not applicable in container mode" in r.stdout + r.stderr
    for action in ("show", "status"):
        r = _cli("--config", str(DEPLOY_CONFIG), "autostart", action, "--data-dir", str(tmp_path / "data"))
        assert r.returncode == 0 and r.stdout.strip() == CONTAINER_MODE_REASON
    if sys.platform == "win32":
        q = subprocess.run(["schtasks", "/Query", "/TN", entry], capture_output=True, text=True)
        assert q.returncode != 0, "a task was registered in container mode"
    assert not (tmp_path / "data" / "jig.db").exists()  # nothing was audited as enabled, since nothing was


def test_api_reports_not_applicable_in_container_mode(tmp_path):
    data_dir, port = tmp_path / "data", free_port()
    proc, log = start_jig(data_dir, port, config=_container_config(tmp_path),
                          env={"JIG_AUTOSTART_ENTRY": r"\JigTest\Container API"})
    try:
        wait_health(port, proc=proc, log=log)
        base, headers = f"http://127.0.0.1:{port}", token(data_dir)
        assert httpx.get(f"{base}/autostart").status_code == 401  # still behind the same authentication
        info = httpx.get(f"{base}/autostart", headers=headers, timeout=30).json()
        assert info == {"applicable": False, "reason": CONTAINER_MODE_REASON, "deployment": "container",
                        "hint": "Docker keeps Jig running (restart: unless-stopped). Make sure Docker Desktop "
                                "starts when you log in."}
        for path in ("enable", "disable"):
            r = httpx.post(f"{base}/autostart/{path}", headers=headers, json={"confirm": True}, timeout=30)
            assert r.status_code == 409 and r.json()["error"] == CONTAINER_MODE_REASON
    finally:
        kill(proc)


def _docker_image_available() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode == 0


@pytest.mark.skipif(not _docker_image_available(), reason=f"needs Docker and the {IMAGE} image (JIG_TEST_IMAGE)")
def test_enable_refused_inside_a_real_container():
    """The real Jig image, with this checkout's code and deploy/jig.toml mounted, as compose runs it."""
    r = subprocess.run(["docker", "run", "--rm", "--read-only", "--cap-drop", "ALL",
                        "-v", f"{ROOT / 'jig'}:/opt/jig/jig:ro", "-v", f"{DEPLOY_CONFIG}:/etc/jig/jig.toml:ro",
                        IMAGE, "jig", "autostart", "enable", "--yes"],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 1, r.stdout + r.stderr
    assert CONTAINER_MODE_REASON in r.stderr
