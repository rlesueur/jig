"""Live tests against the real cloud providers. Opt-in: each runs only when its key is set, and is skipped with
the reason otherwise. They send test prompts (no personal data) to the provider and cost a few requests.

    JIG_LIVE_OPENAI_KEY       profiles/openai.toml
    JIG_LIVE_OPENROUTER_KEY   profiles/openrouter.toml
    JIG_LIVE_ANTHROPIC_KEY    profiles/anthropic.toml
    JIG_LIVE_GEMINI_KEY       profiles/gemini.toml

JIG_LIVE_<PROVIDER>_MODEL overrides the profile's model. Each test stores the key in a fresh vault with the
real CLI, confirms consent with the real CLI, then starts Jig with the profile: the real capability checks (tool
calling and structured output) for the agent and, here, a Sentinel on the same provider, followed by a chat turn
that calls a tool and continues after the result (which is where Gemini's thought signatures must round-trip).
The profiles keep the Sentinel local; these tests point it at the provider so only the key is needed.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from jig.config import load_config
from jig.runtime import Jig

from .test_cloud_models import cli

REPO = Path(__file__).resolve().parent.parent
PROVIDERS = ["openai", "openrouter", "anthropic", "gemini"]


def _profile_with_cloud_sentinel(tmp_path: Path, provider: str) -> Path:
    text = (REPO / "profiles" / f"{provider}.toml").read_text(encoding="utf-8")
    head, _, rest = text.partition("[sentinel]\n")
    rest = rest.replace('base_url = "http://127.0.0.1:8080/v1"\nname = ""\n', "allow_cloud = true\n", 1)
    if model := os.environ.get(f"JIG_LIVE_{provider.upper()}_MODEL"):
        head = "\n".join(f'name = "{model}"' if line.startswith("name = ") else line for line in head.splitlines())
        head += "\n"
    cfg = tmp_path / f"{provider}.toml"
    cfg.write_text(head + "[sentinel]\n" + rest, encoding="utf-8")
    return cfg


@pytest.mark.network
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_live_provider(provider, tmp_path):
    key = os.environ.get(f"JIG_LIVE_{provider.upper()}_KEY")
    if not key:
        pytest.skip(f"live {provider} test: set JIG_LIVE_{provider.upper()}_KEY to run it (sends test prompts to "
                    f"{provider} and costs a few requests)")
    cfg_path = _profile_with_cloud_sentinel(tmp_path, provider)
    data = tmp_path / "data"
    stored = cli(cfg_path, data, "model", "key", "set", provider, "--stdin", stdin=key + "\n")
    assert stored.returncode == 0, stored.stderr
    confirmed = cli(cfg_path, data, "model", "cloud", "confirm", "--yes")
    assert confirmed.returncode == 0, confirmed.stderr
    cfg = load_config(cfg_path, data_dir=data, sandbox_dir=tmp_path / "sandbox")
    assert cfg.model.location.is_cloud and cfg.sentinel.location.is_cloud
    runtime = Jig(cfg)
    await runtime.start(run_scheduler=False)
    try:
        caps = runtime.capabilities
        assert caps["agent"]["tool_calling"] and caps["agent"]["structured_output"]
        assert caps["sentinel"].get("same_as_agent") or caps["sentinel"]["structured_output"]
        (runtime.sandbox.root / "hello.txt").write_text("hello from Jig\n", encoding="utf-8")
        events = [e async for e in runtime.chat(
            "Use the list_files tool to list your workspace, then tell me the name of the file you found.")]
        assert events[-1]["type"] == "done", events[-1]
        assert any(e["type"] == "event" and e["event"]["type"] == "tool.start" for e in events)
        assert "hello" in events[-1]["final"].lower()
    finally:
        await runtime.stop()
    assert not [f for f in data.rglob("*") if f.is_file() and key.encode() in f.read_bytes()]
