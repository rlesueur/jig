"""No default output limit, a required one only where the provider needs it, and honest cut-off and context errors.

Real config files and the real model server from jig.toml."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from jig.config import load_config
from jig.errors import ConfigError, ModelError
from jig.model import ModelClient

REPO = Path(__file__).resolve().parent.parent
LOCAL_PROFILES = ["llamacpp-bonsai", "lmstudio", "ollama", "vllm"]


def write_config(path: Path, model: str, sentinel: str = "") -> Path:
    path.write_text(f"[model]\n{model}\n[sentinel]\n{sentinel}\n", encoding="utf-8")
    return path


def body(config):
    return ModelClient(config, api_key="test-key")._body([{"role": "user", "content": "hi"}], tools=None, stream=False,
                                                         model="m", max_tokens=None, response_schema=None)


@pytest.mark.parametrize("path", ["jig.toml", "deploy/jig.toml", *(f"profiles/{p}.toml" for p in LOCAL_PROFILES),
                                  "profiles/openai.toml", "profiles/openrouter.toml", "profiles/gemini.toml"])
def test_no_output_limit_unless_set(path, tmp_path):
    cfg = load_config(REPO / path, data_dir=tmp_path / "d", sandbox_dir=tmp_path / "s")
    assert cfg.model.max_tokens is None and cfg.sentinel.max_tokens is None
    sent = body(cfg.model)
    assert "max_tokens" not in sent and "max_completion_tokens" not in sent


def test_a_limit_you_set_is_sent_in_the_providers_field(tmp_path):
    local = load_config(write_config(tmp_path / "a.toml", 'base_url = "http://127.0.0.1:8080/v1"\nmax_tokens = 500'))
    assert body(local.model)["max_tokens"] == 500
    openai = load_config(write_config(tmp_path / "b.toml", 'base_url = "https://api.openai.com/v1"\nprovider = "openai"\n'
                                                           "max_tokens = 500"))
    sent = body(openai.model)
    assert sent["max_completion_tokens"] == 500 and "max_tokens" not in sent


def test_anthropic_requires_an_output_limit_and_the_profile_sets_the_documented_maximum(tmp_path):
    with pytest.raises(ConfigError, match=r"\[model\] max_tokens is required: Anthropic's API rejects a request"):
        load_config(write_config(tmp_path / "a.toml", 'base_url = "https://api.anthropic.com/v1"\nprovider = "anthropic"'))
    profile = load_config(REPO / "profiles" / "anthropic.toml", data_dir=tmp_path / "d", sandbox_dir=tmp_path / "s")
    assert profile.model.name == "claude-sonnet-5-5" and profile.model.max_tokens == 128000
    assert body(profile.model)["max_tokens"] == 128000
    assert profile.sentinel.max_tokens == 128000  # the same endpoint, so inherited


def test_an_anthropic_sentinel_needs_its_own_limit_when_the_agent_is_local(tmp_path):
    agent = 'base_url = "http://127.0.0.1:8080/v1"\nmax_tokens = 2048'
    sentinel = ('base_url = "https://api.anthropic.com/v1"\nprovider = "anthropic"\n'
                'api_key_secret = "model-key.anthropic"\nallow_cloud = true')
    with pytest.raises(ConfigError, match=r"\[sentinel\] max_tokens is required"):
        load_config(write_config(tmp_path / "a.toml", agent, sentinel))
    cfg = load_config(write_config(tmp_path / "b.toml", agent, sentinel + "\nmax_tokens = 64000"))
    assert (cfg.model.max_tokens, cfg.sentinel.max_tokens) == (2048, 64000)


def test_a_sentinel_on_another_server_does_not_inherit_the_agents_limit(tmp_path):
    cfg = load_config(write_config(tmp_path / "a.toml", 'base_url = "http://127.0.0.1:8080/v1"\nmax_tokens = 2048',
                                   'base_url = "http://127.0.0.1:11434/v1"'))
    assert (cfg.model.max_tokens, cfg.sentinel.max_tokens) == (2048, None)


@pytest.mark.parametrize("value", ["0", "-5", '"8192"', "true", "1.5"])
def test_invalid_limits_are_refused(value, tmp_path):
    with pytest.raises(ConfigError, match="max_tokens must be a whole number of at least 1"):
        load_config(write_config(tmp_path / "a.toml", f'base_url = "http://127.0.0.1:8080/v1"\nmax_tokens = {value}'))


# Against the real model server ---------------------------------------------------------------------------------

async def test_a_limit_you_set_that_cuts_the_reply_off_says_so(config):
    client = ModelClient(replace(config.model, max_tokens=16), label="agent model")
    try:
        await client.health()
        with pytest.raises(ModelError) as cut:
            await client.chat([{"role": "user", "content": "Explain in detail how a rainbow forms."}])
    finally:
        await client.aclose()
    text = str(cut.value)
    assert "cut off at the output limit set in the config (max_tokens = 16" in text
    assert "raise or remove max_tokens" in text and "output 16 tokens" in text


async def test_a_conversation_too_big_for_the_context_is_explained(config):
    client = ModelClient(config.model, label="agent model")
    try:
        info = await client.health()
        context = info["context_tokens"]
        if not context:
            pytest.skip("the model server does not report its context size, so an overflow can't be sized")
        with pytest.raises(ModelError) as refused:
            await client.chat([{"role": "user", "content": "hello " * (context + 4000)}])
    finally:
        await client.aclose()
    text = str(refused.value)
    assert f"no longer fits the model's context window ({context} tokens)" in text
    assert "HTTP 400" not in text
