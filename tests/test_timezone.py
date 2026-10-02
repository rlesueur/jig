"""Jig's timezone: this computer's by default (read from the system), or one set in jig.toml or Settings, and
schedules follow it. The server test runs a real `jig serve` with the real model on a free port."""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import tzlocal

from jig.config import load_config
from jig.errors import ConfigError

from .server_helpers import free_port, kill, start_jig, token, wait_health

REPO = Path(__file__).resolve().parent.parent


def write_config(tmp_path: Path, runtime: str = "") -> Path:
    path = tmp_path / "jig.toml"
    path.write_text(f'[model]\nbase_url = "http://127.0.0.1:8080/v1"\n\n[runtime]\n{runtime}\n', encoding="utf-8")
    return path


def test_the_shipped_configs_use_this_computers_timezone():
    for name in ("jig.toml", "installer/jig.toml", "deploy/jig.toml"):
        text = (REPO / name).read_text(encoding="utf-8").replace("@PORT@", "8766")  # the installer fills it in
        raw = tomllib.loads(text)
        assert raw["runtime"]["timezone"] == "", name


def test_an_empty_timezone_is_this_computers(tmp_path):
    config = load_config(write_config(tmp_path, 'timezone = ""'), data_dir=tmp_path / "data")
    assert config.runtime.timezone == tzlocal.get_localzone_name()
    assert config.timezone_from == "system"
    ZoneInfo(config.runtime.timezone)
    assert load_config(write_config(tmp_path), data_dir=tmp_path / "data").timezone_from == "system"


def test_the_system_timezone_is_read_fresh_in_each_process(tmp_path):
    """TZ is how Linux, macOS and Windows (via tzlocal) let a process be told its zone; with it set to New York,
    Jig's detected timezone is New York, on a computer set to London."""
    code = ("import sys; from jig.config import load_config; c = load_config(sys.argv[1], data_dir=sys.argv[2]); "
            "print(c.runtime.timezone, c.timezone_from)")
    out = subprocess.run([sys.executable, "-c", code, str(write_config(tmp_path)), str(tmp_path / "data")],
                         capture_output=True, text=True, env={**os.environ, "TZ": "America/New_York"}, check=True)
    assert out.stdout.split() == ["America/New_York", "system"]


def test_an_explicit_timezone_in_jig_toml(tmp_path):
    config = load_config(write_config(tmp_path, 'timezone = "America/New_York"'), data_dir=tmp_path / "data")
    assert (config.runtime.timezone, config.timezone_from) == ("America/New_York", "config")


def test_settings_toml_overrides_jig_toml(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "settings.toml").write_text('[runtime]\ntimezone = "Asia/Tokyo"\n', encoding="utf-8")
    config = load_config(write_config(tmp_path, 'timezone = "America/New_York"'), data_dir=data)
    assert (config.runtime.timezone, config.timezone_from) == ("Asia/Tokyo", "settings")
    (data / "settings.toml").write_text('[runtime]\ntimezone = ""\n', encoding="utf-8")
    config = load_config(write_config(tmp_path, 'timezone = "America/New_York"'), data_dir=data)
    assert (config.runtime.timezone, config.timezone_from) == (tzlocal.get_localzone_name(), "system")
    (data / "settings.toml").write_text('[runtime]\nmax_steps = 99\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="keys Jig doesn't save there"):
        load_config(write_config(tmp_path), data_dir=data)


def test_an_unknown_timezone_is_refused_with_where_it_came_from(tmp_path):
    with pytest.raises(ConfigError, match=r"'Mars/Olympus' \(from \[runtime\] in jig.toml\) isn't one Jig knows"):
        load_config(write_config(tmp_path, 'timezone = "Mars/Olympus"'), data_dir=tmp_path / "data")


def test_schedules_follow_jigs_timezone(tmp_path):
    """A non-London timezone set explicitly (in Settings' settings.toml): schedules made without a timezone use
    it, /status reports it, and changing it in Settings moves the schedules that were on it."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "settings.toml").write_text('[runtime]\ntimezone = "America/New_York"\n', encoding="utf-8")
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    proc, log = start_jig(data, port, env={"JIG_SANDBOX_DIR": str(tmp_path / "sandbox")})
    try:
        wait_health(port, proc=proc, log=log)
        h = token(data)
        status = httpx.get(f"{base}/status", headers=h, timeout=60).json()
        assert status["timezone"] == {"name": "America/New_York", "from": "settings"}

        s = httpx.post(f"{base}/schedules", headers=h, timeout=30, json={
            "name": "Morning", "prompt": "Say good morning", "repeat": {"kind": "daily", "at": "08:00"}}).json()
        assert s["timezone"] == "America/New_York"
        local = datetime.fromisoformat(s["next_run_at"]).astimezone(ZoneInfo("America/New_York"))
        assert local.strftime("%H:%M") == "08:00"
        other = httpx.post(f"{base}/schedules", headers=h, timeout=30, json={
            "name": "Paris", "prompt": "p", "repeat": {"kind": "daily", "at": "09:00"},
            "timezone": "Europe/Paris"}).json()

        bad = httpx.put(f"{base}/setup/timezone", headers=h, timeout=30, json={"timezone": "Mars/Olympus"})
        assert bad.status_code == 400 and "isn't one Jig knows" in bad.json()["error"]

        out = httpx.put(f"{base}/setup/timezone", headers=h, timeout=30, json={"timezone": "Asia/Tokyo"}).json()
        assert out == {"name": "Asia/Tokyo", "from": "settings", "moved_schedules": 1}
        rows = {r["id"]: r for r in httpx.get(f"{base}/schedules", headers=h, timeout=30).json()}
        assert rows[s["id"]]["timezone"] == "Asia/Tokyo"
        local = datetime.fromisoformat(rows[s["id"]]["next_run_at"]).astimezone(ZoneInfo("Asia/Tokyo"))
        assert local.strftime("%H:%M") == "08:00"
        assert rows[other["id"]]["timezone"] == "Europe/Paris", "a schedule on its own timezone stays on it"
        assert tomllib.loads((data / "settings.toml").read_text(encoding="utf-8"))["runtime"] == {
            "timezone": "Asia/Tokyo"}
        entry = httpx.get(f"{base}/audit?kind=settings.timezone", headers=h, timeout=30).json()[-1]
        assert entry["data"]["timezone"] == "Asia/Tokyo" and entry["data"]["previous"] == "America/New_York"
        assert entry["data"]["moved_schedules"] == [s["id"]]
        assert httpx.get(f"{base}/status", headers=h, timeout=60).json()["timezone"]["name"] == "Asia/Tokyo"

        back = httpx.put(f"{base}/setup/timezone", headers=h, timeout=30, json={"timezone": ""}).json()
        assert back == {"name": tzlocal.get_localzone_name(), "from": "system", "moved_schedules": 1}
    finally:
        kill(proc)
