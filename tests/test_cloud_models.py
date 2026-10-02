"""Cloud models, safely: classification, consent, HTTPS, vault keys and redaction, a local Sentinel next to a cloud
agent, and the web UI's cloud indicator. Nothing is mocked and no cloud API is faked.

The "cloud" endpoint is a real llama.cpp server with real TLS and a real API key, reached through
``llama.localtest.me`` (a public DNS name that resolves to 127.0.0.1), which Jig classifies as cloud. See
tests/tls_llama.py. The same server is also reached over this computer's LAN address (a remote local server).
The live tests against the real providers are in tests/test_cloud_live.py.

Server tests need JIG_TEST_LLAMA_SERVER and JIG_TEST_SMALL_MODEL; without them they are skipped.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from jig.cloud import GIVEN, REVOKED, CloudConsentRequired, consent_state
from jig.config import load_config
from jig.endpoints import PROVIDERS, classify
from jig.errors import ConfigError, JigError
from jig.model import ModelClient
from jig.runtime import Jig

from . import tls_llama
from .sandbox_helpers import gated_call
from .server_helpers import free_port, kill, start_jig, token, wait_health

REPO = Path(__file__).resolve().parent.parent
needs_llama = pytest.mark.skipif(not tls_llama.AVAILABLE, reason="set JIG_TEST_LLAMA_SERVER and JIG_TEST_SMALL_MODEL")


def write_config(path: Path, model: str, sentinel: str = "", *, port: int = 8766) -> Path:
    path.write_text(f"[model]\n{model}\n\n[sentinel]\n{sentinel}\n\n[vision]\nenabled = false\n\n"
                    f"[paths]\ndata_dir = 'data'\nsandbox_dir = 'sandbox'\n\n[server]\nport = {port}\n",
                    encoding="utf-8")
    return path


def cli(cfg: Path, data_dir: Path, *args: str, stdin: str | None = None,
        env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "jig.cli", "--config", str(cfg), *args], input=stdin,
                          capture_output=True, text=True, timeout=300, cwd=str(REPO),
                          env={**os.environ, "JIG_DATA_DIR": str(data_dir), **(env or {})},
                          stdin=None if stdin is not None else subprocess.DEVNULL)


# Classification -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("url, kind, reason", [
    ("http://127.0.0.1:8080/v1", "local", "loopback"),
    ("http://localhost:11434/v1", "local", "loopback"),
    ("http://[::1]:8080/v1", "local", "loopback"),
    ("http://0.0.0.0:8080/v1", "local", "loopback"),
    ("http://192.168.1.20:8080/v1", "local", "private network"),
    ("http://10.0.0.5/v1", "local", "private network"),
    ("http://172.20.1.1/v1", "local", "private network"),
    ("http://[fd12:3456::1]/v1", "local", "private network"),
    ("http://169.254.10.10/v1", "local", "link-local"),
    ("http://100.101.102.103:8080/v1", "local", "tailnet"),
    ("http://[fd7a:115c:a1e0::5]/v1", "local", "tailnet"),
    ("https://gpu-box.tail1234.ts.net/v1", "local", "tailnet"),
    ("http://ollama:11434/v1", "local", "local name"),
    ("http://host.docker.internal:8080/v1", "local", "local name"),
    ("http://gpu.local:8080/v1", "local", "local name"),
    ("http://nas.home.arpa/v1", "local", "local name"),
    ("https://api.openai.com/v1", "cloud", "public host name"),
    ("https://openrouter.ai/api/v1", "cloud", "public host name"),
    ("https://api.anthropic.com/v1", "cloud", "public host name"),
    ("https://generativelanguage.googleapis.com/v1beta/openai", "cloud", "public host name"),
    ("https://llama.localtest.me:8181/v1", "cloud", "public host name"),  # resolves to 127.0.0.1: still cloud
    ("https://8.8.8.8/v1", "cloud", "public address"),
    ("https://100.128.0.1/v1", "cloud", "public address"),  # just outside 100.64.0.0/10
    ("https://172.32.0.1/v1", "cloud", "public address"),  # just outside 172.16.0.0/12
    ("http://[::ffff:8.8.8.8]/v1", "cloud", "public address"),
    ("http://[::ffff:192.168.0.1]/v1", "local", "private network"),
])
def test_classification(url, kind, reason):
    loc = classify(url)
    assert (loc.kind, loc.reason) == (kind, reason)


# Config: HTTPS, providers, inheritance ---------------------------------------------------------------------------

def test_cloud_endpoint_must_use_https(tmp_path):
    cfg = write_config(tmp_path / "jig.toml", 'base_url = "http://api.example.com/v1"\nallow_cloud = true')
    with pytest.raises(ConfigError, match=r"cloud endpoint \(api.example.com.*must use https://"):
        load_config(cfg)
    lan = write_config(tmp_path / "lan.toml", 'base_url = "http://192.168.1.20:8080/v1"')
    assert load_config(lan).model.location.kind == "local"  # plain HTTP is still fine on your own network


@pytest.mark.parametrize("model, message", [
    ('base_url = "https://api.openai.com/v1"\nprovider = "nope"', "provider must be one of"),
    ('base_url = "https://openrouter.ai/api/v1"\nprovider = "openai"', "OpenAI's API at api.openai.com"),
    ('base_url = "https://api.anthropic.com/v1"\nprovider = "anthropic"\n[model.sampling]\ntop_k = 20',
     r"\['top_k'\] are not supported by Anthropic"),
    ('base_url = "https://api.anthropic.com/v1"\nprovider = "anthropic"\n[model.sampling]\ntemperature = 1.5',
     "above Anthropic's maximum of 1.0"),
    ('base_url = "https://generativelanguage.googleapis.com/v1beta/openai"\nprovider = "gemini"\n'
     '[model.sampling]\nmin_p = 0.1', r"\['min_p'\] are not supported by Google Gemini"),
    ('base_url = "https://api.openai.com/v1"\n[model.sampling]\nmax_tokens = 100', r"must not set \['max_tokens'\]"),
    ('base_url = "https://api.openai.com/v1"\napi_key_env = "X"\napi_key_secret = "model-key.openai"', "not both"),
    ('base_url = "https://api.openai.com/v1"\napi_key_secret = "openai"', 'must name a model key, "model-key.'),
    ('base_url = "https://api.openai.com/v1"\n[model.headers]\nAuthorization = "Bearer x"', "must not carry the API key"),
    ('base_url = "https://api.openai.com/v1"\nstructured_output = "guess"', "structured_output must be"),
    ('base_url = "https://api.openai.com/v1"\nca_file = "missing.pem"', "ca_file .* does not exist"),
])
def test_provider_differences_are_errors_not_guesses(tmp_path, model, message):
    with pytest.raises(ConfigError, match=message):
        load_config(write_config(tmp_path / "jig.toml", model))


def test_model_launch_refused_for_a_cloud_endpoint(tmp_path):
    cfg = tmp_path / "jig.toml"
    cfg.write_text('[model]\nbase_url = "https://api.openai.com/v1"\n[model.launch]\ncommand = "llama-server"\n',
                   encoding="utf-8")
    with pytest.raises(ConfigError, match=r"\[model.launch\] starts a local model server"):
        load_config(cfg)


def test_sentinel_never_inherits_cloud_consent_key_or_provider_settings(tmp_path):
    agent = ('base_url = "https://api.anthropic.com/v1"\nprovider = "anthropic"\nname = "claude-sonnet-5-5"\n'
             'api_key_secret = "model-key.anthropic"\nallow_cloud = true\n[model.headers]\n'
             'anthropic-workspace-id = "wrkspc_1"\n[model.sampling]\ntemperature = 0.5')
    local = load_config(write_config(tmp_path / "a.toml", agent, 'base_url = "http://127.0.0.1:8080/v1"'))
    assert local.model.location.is_cloud and local.model.allow_cloud
    s = local.sentinel
    assert (s.location.kind, s.provider, s.api_key_secret, s.api_key_env, s.allow_cloud, s.headers, s.sampling,
            s.name) == ("local", "", "", "", False, {}, {}, "")
    # Inheriting the cloud endpoint inherits its key and provider, but never the consent flag.
    same = load_config(write_config(tmp_path / "b.toml", agent))
    assert same.sentinel.location.is_cloud and same.sentinel.provider == "anthropic"
    assert same.sentinel.api_key_secret == "model-key.anthropic" and same.sentinel.allow_cloud is False


@pytest.mark.parametrize("profile", ["openai", "openrouter", "anthropic", "gemini"])
def test_cloud_profiles(profile, tmp_path):
    cfg = load_config(REPO / "profiles" / f"{profile}.toml", data_dir=tmp_path / "d", sandbox_dir=tmp_path / "s")
    provider = PROVIDERS[profile]
    m = cfg.model
    assert (m.provider, m.base_url, m.api_key_secret, m.allow_cloud) == (
        profile, provider.base_url, f"model-key.{profile}", True)
    assert m.location.is_cloud and m.base_url.startswith("https://") and m.name
    # Default: the safety checker is the agent's own model (no second model), acknowledged for the cloud explicitly.
    s = cfg.sentinel
    assert (s.base_url, s.name, s.provider, s.api_key_secret, s.allow_cloud) == (
        m.base_url, m.name, m.provider, m.api_key_secret, True)
    text = (REPO / "profiles" / f"{profile}.toml").read_text(encoding="utf-8")
    assert "# [sentinel]\n# base_url = \"http://127.0.0.1:8080/v1\"" in text  # the optional local safety checker
    assert "never leave this computer" in text
    if profile == "anthropic":
        assert m.structured_output_mode == "tool_call"
    if profile == "openrouter":
        assert m.sampling["provider"]["require_parameters"] is True


def test_masked_key_echoes_are_redacted():
    client = ModelClient(load_config().model, api_key="sk-abcdefgh1234567890wxyz")
    # OpenAI's real 401 shows a masked key ("Incorrect API key provided: sk-inval************0000").
    text = 'Incorrect API key provided: sk-abcdef************wxyz. Full: sk-abcdefgh1234567890wxyz'
    assert "1234567890" not in client.redact(text) and "sk-abcdef" not in client.redact(text)


# Against a real server: consent, keys, local Sentinel, LAN -------------------------------------------------------

@pytest.fixture(scope="module")
def tls(tmp_path_factory):
    if not tls_llama.AVAILABLE:
        pytest.skip("set JIG_TEST_LLAMA_SERVER and JIG_TEST_SMALL_MODEL")
    server = tls_llama.start(tmp_path_factory.mktemp("tls-llama"))
    try:
        yield server
    finally:
        tls_llama.stop(server)


def cloud_config(tmp_path: Path, tls, *, allow: bool = True, port: int = 8766, key_name: str = "tlstest") -> Path:
    """Agent on the cloud-classified name with a vault key; safety checker on loopback with a key from the env."""
    ca = str(tls.ca_file)
    model = (f"base_url = '{tls.url(tls_llama.CLOUD_NAME)}'\nname = '{tls_llama.ALIAS}'\nca_file = '{ca}'\n"
             f"api_key_secret = 'model-key.{key_name}'\nmax_tokens = 4096" + ("\nallow_cloud = true" if allow else ""))
    sentinel = (f"base_url = '{tls.url('127.0.0.1')}'\nname = '{tls_llama.ALIAS}'\nca_file = '{ca}'\n"
                "api_key_env = 'JIG_TEST_SENTINEL_KEY'")
    return write_config(tmp_path / "jig.toml", model, sentinel, port=port)


def raw_bytes_contain(folder: Path, needle: str) -> list[str]:
    hits = []
    for f in folder.rglob("*"):
        if f.is_file() and needle.encode() in f.read_bytes():
            hits.append(str(f))
    return hits


@needs_llama
async def test_refuses_to_start_without_consent(tls, tmp_path, monkeypatch):
    monkeypatch.setenv("JIG_TEST_SENTINEL_KEY", tls.api_key)
    data = tmp_path / "data"
    cfg = load_config(cloud_config(tmp_path, tls, allow=False), data_dir=data, sandbox_dir=tmp_path / "sb")
    with pytest.raises(CloudConsentRequired) as no_flag:
        Jig(cfg)
    text = str(no_flag.value)
    assert "llama.localtest.me" in text and "your conversation" in text and "tool results" in text
    assert "images you share" in text and "allow_cloud = true under [model]" in text
    assert "safety checker" not in text.split("Add allow_cloud")[0].lower()  # the Sentinel is local: not listed

    cfg = load_config(cloud_config(tmp_path, tls, allow=True), data_dir=data, sandbox_dir=tmp_path / "sb")
    with pytest.raises(CloudConsentRequired, match="jig model cloud confirm"):
        Jig(cfg)

    # The real CLI refuses too, before the server starts, with the same explanation.
    out = cli(tmp_path / "jig.toml", data, "serve", "--port", str(free_port()),
              env={"JIG_TEST_SENTINEL_KEY": tls.api_key})
    assert out.returncode == 1 and "CloudConsentRequired" in out.stderr and "jig model cloud confirm" in out.stderr
    # Confirming without a terminal (and without --yes) changes nothing.
    out = cli(tmp_path / "jig.toml", data, "model", "cloud", "confirm")
    assert out.returncode == 1 and "Re-run with --yes" in out.stderr


@needs_llama
async def test_cloud_agent_with_local_sentinel_end_to_end(tls, tmp_path, monkeypatch):
    monkeypatch.setenv("JIG_TEST_SENTINEL_KEY", tls.api_key)
    data = tmp_path / "data"
    cfg_path = cloud_config(tmp_path, tls)
    stored = cli(cfg_path, data, "model", "key", "set", "tlstest", "--stdin", stdin=tls.api_key + "\n")
    assert stored.returncode == 0, stored.stderr
    assert "Stored the API key as 'model-key.tlstest'" in stored.stdout and tls.api_key not in stored.stdout
    status = cli(cfg_path, data, "model", "key", "status")
    assert "in the vault as 'model-key.tlstest'" in status.stdout and tls.api_key not in status.stdout
    confirmed = cli(cfg_path, data, "model", "cloud", "confirm", "--yes")
    assert confirmed.returncode == 0, confirmed.stderr
    assert "llama.localtest.me" in confirmed.stdout and "recorded in the audit log" in confirmed.stdout

    cfg = load_config(cfg_path, data_dir=data, sandbox_dir=tmp_path / "sb")
    runtime = Jig(cfg)
    await runtime.start(run_scheduler=False)  # the real capability probes, over TLS, with the vault key
    try:
        conn = runtime.connection()
        assert conn["agent"]["kind"] == "cloud" and conn["agent"]["host"] == "llama.localtest.me"
        assert conn["agent"]["key_source"] == "vault" and conn["agent"]["confirmed_at"]
        assert conn["sentinel"]["kind"] == "local" and conn["sentinel"]["reason"] == "loopback"
        assert runtime.capabilities["agent"]["tool_calling"] and runtime.capabilities["sentinel"]["structured_output"]
        events = [e async for e in runtime.chat("Reply with the single word: ready")]
        assert events[-1]["type"] == "done", events[-1]
        given = consent_state(runtime.audit, "agent", f"https://{tls_llama.CLOUD_NAME}:{tls.port}")
        assert given["kind"] == GIVEN and "your conversation: what you type and Jig's replies" in given["sends"]
        start = runtime.audit.query(kind="runtime.start")[-1]
        assert json.loads(start["data_json"])["connection"]["agent"]["kind"] == "cloud"
    finally:
        await runtime.stop()
    # The key is never written in plain text: not in the database (the vault keeps ciphertext), the audit log or logs.
    assert raw_bytes_contain(data, tls.api_key) == []

    revoked = cli(cfg_path, data, "model", "cloud", "revoke")
    assert revoked.returncode == 0 and "Withdrawn" in revoked.stdout
    with pytest.raises(CloudConsentRequired):
        Jig(cfg)
    rows = load_rows(data, "model.cloud_consent")
    assert [k for k, _ in rows] == [GIVEN, REVOKED]


@needs_llama
async def test_safety_checker_on_the_agents_cloud_model_needs_one_confirmation(tls, tmp_path):
    """The default (safety checker = agent's model) on a cloud endpoint: the Sentinel still needs its own
    allow_cloud, but a single 'jig model cloud confirm' covers both roles and is recorded once, as shared."""
    data = tmp_path / "data"
    model = (f"base_url = '{tls.url(tls_llama.CLOUD_NAME)}'\nname = '{tls_llama.ALIAS}'\nca_file = '{tls.ca_file}'\n"
             "api_key_secret = 'model-key.tlstest'\nmax_tokens = 4096\nallow_cloud = true")
    no_ack = write_config(tmp_path / "no-ack.toml", model)
    with pytest.raises(CloudConsentRequired) as refused:
        Jig(load_config(no_ack, data_dir=data, sandbox_dir=tmp_path / "sb"))
    assert "uses the same model as the agent (the default)" in str(refused.value)
    assert "allow_cloud = true under [sentinel]" in str(refused.value)
    out = cli(no_ack, data, "model", "cloud", "confirm", "--yes")
    assert out.returncode == 1 and "[sentinel] allow_cloud is not set" in out.stderr

    cfg_path = write_config(tmp_path / "jig.toml", model, "allow_cloud = true")
    assert cli(cfg_path, data, "model", "key", "set", "tlstest", "--stdin", stdin=tls.api_key).returncode == 0
    confirmed = cli(cfg_path, data, "model", "cloud", "confirm", "--yes")
    assert confirmed.returncode == 0, confirmed.stderr
    assert confirmed.stdout.count("would use") == 1
    assert ("The agent and the safety checker (Sentinel) would use the same cloud model" in confirmed.stdout)
    assert "each action Jig wants to take" in confirmed.stdout and "your conversation" in confirmed.stdout
    rows = load_rows(data, "model.cloud_consent")
    assert [k for k, _ in rows] == [GIVEN]
    given = rows[0][1]
    assert given["roles"] == ["agent", "sentinel"] and given["same_endpoint"] is True
    assert given["origin"] == f"https://{tls_llama.CLOUD_NAME}:{tls.port}"

    cfg = load_config(cfg_path, data_dir=data, sandbox_dir=tmp_path / "sb")
    runtime = Jig(cfg)
    await runtime.start(run_scheduler=False)  # real capability probes over TLS, once for the shared model
    try:
        conn = runtime.connection()
        assert conn["agent"]["kind"] == conn["sentinel"]["kind"] == "cloud"
        assert conn["sentinel"]["same_endpoint_as_agent"]
        assert conn["agent"]["confirmed_at"] and conn["sentinel"]["confirmed_at"] == conn["agent"]["confirmed_at"]
        assert runtime.capabilities["sentinel"] == {"same_as_agent": True}
    finally:
        await runtime.stop()

    revoked = cli(cfg_path, data, "model", "cloud", "revoke")
    assert revoked.returncode == 0 and revoked.stdout.count("Withdrawn") == 1
    assert "the agent and the safety checker (Sentinel)" in revoked.stdout
    rows = load_rows(data, "model.cloud_consent")
    assert [k for k, _ in rows] == [GIVEN, REVOKED] and rows[1][1]["roles"] == ["agent", "sentinel"]
    with pytest.raises(CloudConsentRequired):
        Jig(cfg)


def load_rows(data: Path, kind: str) -> list[tuple[str, dict]]:
    import sqlite3

    con = sqlite3.connect(data / "jig.db")
    try:
        return [(k, json.loads(d)) for k, d in con.execute(
            "SELECT kind, data_json FROM audit WHERE kind LIKE ? ORDER BY id", (f"{kind}%",))]
    finally:
        con.close()


@needs_llama
async def test_a_refused_key_is_redacted_everywhere(tls, tmp_path, monkeypatch):
    monkeypatch.setenv("JIG_TEST_SENTINEL_KEY", tls.api_key)
    data = tmp_path / "data"
    cfg_path = cloud_config(tmp_path, tls, key_name="wrong")
    wrong = "sk-wrong-" + "x1y2z3" * 6
    assert cli(cfg_path, data, "model", "key", "set", "wrong", "--stdin", stdin=wrong).returncode == 0
    assert cli(cfg_path, data, "model", "cloud", "confirm", "--yes").returncode == 0
    runtime = Jig(load_config(cfg_path, data_dir=data, sandbox_dir=tmp_path / "sb"))
    with pytest.raises(JigError) as refused:
        await runtime.start(run_scheduler=False)
    message = str(refused.value)
    assert "401" in message and "API key was refused" in message and wrong not in message
    assert raw_bytes_contain(data, wrong) == []


@needs_llama
async def test_lan_server_is_local_and_needs_no_consent(tls, tmp_path, monkeypatch):
    if not tls.lan_ip:
        pytest.skip("this computer has no private LAN address")
    monkeypatch.setenv("JIG_TEST_SENTINEL_KEY", tls.api_key)
    model = (f"base_url = '{tls.url(tls.lan_ip)}'\nname = '{tls_llama.ALIAS}'\nca_file = '{tls.ca_file}'\n"
             "api_key_env = 'JIG_TEST_SENTINEL_KEY'")
    cfg = load_config(write_config(tmp_path / "jig.toml", model), data_dir=tmp_path / "d", sandbox_dir=tmp_path / "s")
    assert cfg.model.location.reason == "private network"
    runtime = Jig(cfg)
    await runtime.start(run_scheduler=False, check_capabilities=False)
    try:
        conn = runtime.connection()
        assert conn["agent"]["kind"] == conn["sentinel"]["kind"] == "local"
        assert conn["sentinel"]["same_endpoint_as_agent"]
        assert runtime.audit.query(kind="model.cloud_consent") == []
    finally:
        await runtime.stop()


@needs_llama
async def test_structured_output_as_a_tool_call_on_a_real_model(tls, tmp_path):
    """The mode Anthropic needs (its OpenAI layer ignores response_format), run for real on a local model."""
    model = (f"base_url = '{tls.url('127.0.0.1')}'\nname = '{tls_llama.ALIAS}'\nca_file = '{tls.ca_file}'\n"
             "structured_output = 'tool_call'\nmax_tokens = 4096")
    cfg = load_config(write_config(tmp_path / "jig.toml", model), data_dir=tmp_path / "d")
    client = ModelClient(cfg.model, label="agent model", api_key=tls.api_key)
    try:
        await client.health()
        assert (await client.probe_structured_output())["structured_output"] is True
        from jig.policy.sentinel import VERDICT_SCHEMA

        result = await client.chat([{"role": "user", "content": "Classify this action: reading a public web page. "
                                     "verdict allow, risk low, with a short reason."}], response_schema=VERDICT_SCHEMA)
        assert set(json.loads(result.content)) >= {"verdict", "risk", "reason"} and not result.tool_calls
    finally:
        await client.aclose()


@needs_llama
async def test_model_keys_can_never_be_used_by_tools(tls, tmp_path, monkeypatch):
    from jig.policy.core import evaluate_core

    monkeypatch.setenv("JIG_TEST_SENTINEL_KEY", tls.api_key)
    data = tmp_path / "data"
    cfg_path = cloud_config(tmp_path, tls)
    assert cli(cfg_path, data, "model", "key", "set", "tlstest", "--stdin", stdin=tls.api_key).returncode == 0
    assert cli(cfg_path, data, "model", "cloud", "confirm", "--yes").returncode == 0
    runtime = Jig(load_config(cfg_path, data_dir=data, sandbox_dir=tmp_path / "sb"))
    await runtime.start(run_scheduler=False, check_capabilities=False)
    try:
        spec = runtime.registry.get("web_fetch")
        findings = await evaluate_core(spec, {"url": "https://example.com/",
                                              "headers": {"Authorization": "Bearer {{secret:model-key.tlstest}}"}},
                                       runtime.vault)
        assert any(f.rule_id == "secret-allowlist" and f.decision.value == "block" and "model API key" in f.reason
                   for f in findings)
        # A file that happens to contain the key: the real read_file tool's result is redacted before the model
        # sees it.
        (runtime.sandbox.root / "notes.txt").write_text(f"my key is {tls.api_key}\n", encoding="utf-8")
        outcome, _ = await gated_call(runtime, "read_file", {"path": "notes.txt"}, intent="read my notes")
        assert outcome.ok and tls.api_key not in outcome.message_content()
        assert "[secret:model API key]" in outcome.message_content()
    finally:
        await runtime.stop()


# The web UI, in a real browser, against a real `jig serve` ------------------------------------------------------

def _contrast(fg: str, bg: str) -> float:
    def lum(rgb: str) -> float:
        parts = [float(x) for x in rgb[rgb.index("(") + 1:rgb.index(")")].replace(",", " ").split()[:3]]

        def ch(c: float) -> float:
            c /= 255
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        r, g, b = (ch(c) for c in parts)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    a, b = sorted((lum(fg), lum(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def _signed_in_page(browser, port: int, data: Path, scheme: str):
    code = httpx.post(f"http://127.0.0.1:{port}/auth/login-code", headers=token(data), timeout=10).json()["code"]
    context = browser.new_context(color_scheme=scheme)
    page = context.new_page()
    page.goto(f"http://127.0.0.1:{port}/#code={code}")
    page.wait_for_selector("#app:not([hidden])", timeout=60_000)
    return context, page


@needs_llama
def test_ui_shows_where_the_models_run(tls, tmp_path, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api", reason="pip install playwright")
    monkeypatch.setenv("JIG_TEST_SENTINEL_KEY", tls.api_key)
    cloud_dir, cloud_port = tmp_path / "cloud", free_port()
    cloud_dir.mkdir()
    cfg_path = cloud_config(cloud_dir, tls, port=cloud_port)
    data = cloud_dir / "data"
    assert cli(cfg_path, data, "model", "key", "set", "tlstest", "--stdin", stdin=tls.api_key).returncode == 0
    assert cli(cfg_path, data, "model", "cloud", "confirm", "--yes").returncode == 0
    local_dir, local_port = tmp_path / "local", free_port()
    local_dir.mkdir()
    local_cfg = write_config(local_dir / "jig.toml", f"base_url = '{tls.url('127.0.0.1')}'\nname = "
                             f"'{tls_llama.ALIAS}'\nca_file = '{tls.ca_file}'\napi_key_env = 'JIG_TEST_SENTINEL_KEY'",
                             port=local_port)
    env = {"JIG_TEST_SENTINEL_KEY": tls.api_key}
    cloud_proc, cloud_log = start_jig(data, cloud_port, config=cfg_path, env=env)
    local_proc, local_log = start_jig(local_dir / "data", local_port, config=local_cfg, env=env)
    try:
        wait_health(cloud_port, proc=cloud_proc, log=cloud_log)
        wait_health(local_port, proc=local_proc, log=local_log)
        status = httpx.get(f"http://127.0.0.1:{cloud_port}/status", headers=token(data), timeout=60).json()
        assert status["connection"]["agent"]["kind"] == "cloud" and status["connection"]["sentinel"]["kind"] == "local"
        put = httpx.put(f"http://127.0.0.1:{cloud_port}/vault/model-key.other", headers=token(data), timeout=10,
                        json={"value": "abc", "allowed_tools": ["web_fetch"]})
        assert put.status_code == 400 and "only for Jig's connection to the model" in put.json()["error"]

        with playwright.sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                for scheme in ("light", "dark"):
                    context, page = _signed_in_page(browser, cloud_port, data, scheme)
                    chip = page.get_by_test_id("cloud-indicator")
                    chip.wait_for(state="visible", timeout=60_000)
                    assert chip.inner_text() == "Cloud model"
                    assert "llama.localtest.me" in chip.get_attribute("title")
                    fg, bg = chip.evaluate("e => [getComputedStyle(e).color, getComputedStyle(e).backgroundColor]")
                    assert _contrast(fg, bg) >= 4.5, (scheme, fg, bg)
                    chip.click()
                    page.get_by_test_id("settings-model").wait_for(state="visible")
                    page.wait_for_function("document.getElementById('st-agent-where').textContent !== '\u2014'")
                    assert page.get_by_test_id("agent-where").inner_text() == "Cloud: llama.localtest.me"
                    assert page.get_by_test_id("sentinel-where").inner_text() == "Local"
                    note = page.get_by_test_id("cloud-note")
                    assert note.is_visible()
                    assert ("your conversation, the memory and tool results it works with, and any images you share "
                            "are sent to llama.localtest.me") in note.inner_text()
                    assert "The safety checker runs locally" in note.inner_text()
                    fg, bg = note.evaluate("e => [getComputedStyle(e).color, getComputedStyle(e).backgroundColor]")
                    assert _contrast(fg, bg) >= 4.5, (scheme, fg, bg)
                    hint = page.locator("#set-model .hint").first.inner_text()
                    assert hint.startswith("Jig is built for local models")
                    context.close()

                context, page = _signed_in_page(browser, local_port, local_dir / "data", "light")
                page.goto(f"http://127.0.0.1:{local_port}/#settings/model")
                page.wait_for_function("document.getElementById('st-agent-where').textContent === 'Local'",
                                       timeout=60_000)
                assert page.get_by_test_id("sentinel-where").inner_text() == "Local (same model as the agent)"
                assert not page.get_by_test_id("cloud-indicator").is_visible()
                assert not page.get_by_test_id("cloud-note").is_visible()
                context.close()
            finally:
                browser.close()
    finally:
        kill(cloud_proc)
        kill(local_proc)
