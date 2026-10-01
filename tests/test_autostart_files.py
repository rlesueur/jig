"""The files the macOS and Linux backends would write. These backends are UNTESTED on real systems; these
tests only check the generated content (no launchctl or systemctl is run)."""

from __future__ import annotations

import plistlib
from pathlib import Path, PurePosixPath

import pytest

from jig.autostart import LaunchSpec, backend_for
from jig.config import load_config
from jig.errors import ConfigError
from jig.autostart.linux import SystemdUserUnit
from jig.autostart.macos import LaunchdAgent


def spec(tmp_path: Path) -> LaunchSpec:
    # POSIX paths, so the generated content is the same whichever OS runs the tests.
    p = PurePosixPath
    return LaunchSpec(config_path=p("/home/user/My Jig/jig.toml"), data_dir=p("/home/user/.local/share/jig"),
                      host="127.0.0.1", port=8766, python=p("/home/user/My Jig/.venv/bin/python"))


def test_launchd_plist(tmp_path):
    agent = LaunchdAgent(spec(tmp_path), home=tmp_path)
    plist = plistlib.loads(agent.plist_bytes())
    assert plist["Label"] == "io.github.rlesueur.jig"
    assert plist["ProgramArguments"] == ["/home/user/My Jig/.venv/bin/python", "-m", "jig.cli", "--config",
                                         "/home/user/My Jig/jig.toml", "serve", "--port", "8766",
                                         "--start-reason", "autostart", "--log-file"]
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] == {"SuccessfulExit": False}  # restart on failure; a clean 'jig stop' stays stopped
    assert plist["EnvironmentVariables"] == {"JIG_DATA_DIR": "/home/user/.local/share/jig"}
    assert plist["WorkingDirectory"] == "/home/user/My Jig"
    assert agent.plist_path == tmp_path / "Library" / "LaunchAgents" / "io.github.rlesueur.jig.plist"
    plan = agent.plan()
    assert plan.tested is False and "UNTESTED" in " ".join(plan.notes)
    assert "WARNING: this backend has not been tested" in plan.disclosure()


def test_systemd_user_unit(tmp_path):
    unit = SystemdUserUnit(spec(tmp_path), config_home=tmp_path)
    text = unit.unit_text()
    lines = text.splitlines()
    assert "[Service]" in lines and "[Install]" in lines
    assert "Restart=on-failure" in lines
    assert "WantedBy=default.target" in lines
    assert "Environment=JIG_DATA_DIR=/home/user/.local/share/jig" in lines
    assert ('ExecStart="/home/user/My Jig/.venv/bin/python" -m jig.cli --config "/home/user/My Jig/jig.toml" '
            "serve --port 8766 --start-reason autostart --log-file") in lines
    assert 'WorkingDirectory="/home/user/My Jig"' in lines
    assert unit.unit_path == tmp_path / "systemd" / "user" / "jig.service"
    plan = unit.plan()
    assert plan.tested is False and any("enable-linger" in n for n in plan.notes)


def test_llamacpp_profile_launch_example_parses(tmp_path):
    """The commented [model.launch] example in the llama.cpp profile is valid config once uncommented."""
    profile = Path(__file__).resolve().parents[1] / "profiles" / "llamacpp-bonsai.toml"
    out, in_block = [], False
    for line in profile.read_text(encoding="utf-8").splitlines():
        in_block = in_block or line.startswith("# [model.launch]")
        if in_block and line.startswith("# "):
            line = line[2:]
        elif in_block:
            in_block = False
        out.append(line)
    (tmp_path / "profiles").mkdir()
    path = tmp_path / "profiles" / "p.toml"
    path.write_text("\n".join(out), encoding="utf-8")
    launch = load_config(path).model_launch
    assert launch.command.endswith("llama-server.exe") and "--jinja" in launch.args
    assert launch.readiness_timeout_s == 300 and launch.working_dir == str((tmp_path / "profiles").resolve())
    assert "robyn" not in profile.read_text(encoding="utf-8")


def test_unknown_launch_key_fails_loudly(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text('[model]\nbase_url = "http://127.0.0.1:1/v1"\n[model.launch]\ncomand = "x"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match=r"Unknown keys in \[model.launch\]"):
        load_config(path)


def test_backend_selection(tmp_path):
    s = spec(tmp_path)
    assert type(backend_for(s, platform="darwin")).__name__ == "LaunchdAgent"
    assert type(backend_for(s, platform="linux")).__name__ == "SystemdUserUnit"
    assert type(backend_for(s, platform="win32")).__name__ == "WindowsTaskScheduler"
