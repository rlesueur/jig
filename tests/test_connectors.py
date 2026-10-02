"""The connector framework and the Gmail connector, tested without the user's Google credentials.

Everything here is real: a real vault and SQLite database, real HTTP to a real loopback listener, real
requests to Google's OAuth and Gmail endpoints (which answer a made-up client or token with their real
errors), and the real model for the Sentinel. The tests that need a connected Gmail account are in
test_connector_gmail_live.py and are skipped until it exists.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import email
import json
from email import policy as email_policy

import httpx
import pytest
from fastapi.testclient import TestClient

from jig.config import ConfigError, ConnectorLimits, load_config
from jig.connectors import PROVIDERS, gmail, google, oauth
from jig.connectors.base import STATUS_NEEDS_RECONNECT, ConnectionStore, Connectors, Grant, grant_secret
from jig.constants import ApprovalStatus, Mode
from jig.errors import ConnectorAuthError, ConnectorError, ToolArgumentError
from jig.model import ToolCall
from jig.policy.core import evaluate_core
from jig.policy.gate import CallContext
from jig.runtime import Jig
from jig.tools.builtin import http_client

from .conftest import audit_kinds, wait_for

GMAIL_TOOLS = {"gmail_search", "gmail_read_thread", "gmail_list_labels", "gmail_create_draft", "gmail_send",
               "gmail_reply", "gmail_modify_labels", "gmail_archive"}
FAKE_CLIENT = {"client_id": "000000000000-jigtestnotreal.apps.googleusercontent.com",
               "client_secret": "GOCSPX-jig-test-not-a-real-secret"}


# PKCE and the loopback listener ----------------------------------------------------------------------
def test_pkce_matches_rfc7636_example():
    # RFC 7636, appendix B.
    assert oauth.pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == \
        "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    verifier, challenge = oauth.pkce_pair()
    assert 43 <= len(verifier) <= 128 and oauth.pkce_challenge(verifier) == challenge


async def test_loopback_listener_checks_state_and_returns_the_code():
    receiver = oauth.LoopbackReceiver(label="Google")
    await receiver.start()
    assert receiver.redirect_uri.startswith("http://127.0.0.1:")
    waiter = asyncio.create_task(receiver.wait(timeout=20))
    async with httpx.AsyncClient(trust_env=False) as client:
        base = f"http://127.0.0.1:{receiver.port}"
        assert (await client.get(f"{base}/favicon.ico")).status_code == 404
        wrong = await client.get(f"{base}/", params={"state": "guessed", "code": "evil"})
        assert wrong.status_code == 400 and not waiter.done(), "a wrong state must not finish the sign-in"
        assert (await client.post(f"{base}/", content=b"x")).status_code == 405
        ok = await client.get(f"{base}/", params={"state": receiver.state, "code": "4/real-looking-code"})
        assert ok.status_code == 200 and "Jig is connected" in ok.text
        assert ok.headers["cache-control"] == "no-store"
    assert await waiter == "4/real-looking-code"


async def test_loopback_listener_survives_junk_and_still_takes_the_real_answer():
    receiver = oauth.LoopbackReceiver(label="Google")
    await receiver.start()
    waiter = asyncio.create_task(receiver.wait(timeout=20))
    reader, writer = await asyncio.open_connection("127.0.0.1", receiver.port)
    writer.write(b"GET /" + b"A" * 70_000 + b" HTTP/1.1\r\n\r\n")
    await writer.drain()
    await reader.read()
    writer.close()
    async with httpx.AsyncClient(trust_env=False) as client:
        ok = await client.get(f"http://127.0.0.1:{receiver.port}/", params={"state": receiver.state, "code": "c"})
    assert ok.status_code == 200 and await waiter == "c"


async def test_loopback_listener_reports_a_refusal_and_escapes_it():
    receiver = oauth.LoopbackReceiver(label="Google")
    await receiver.start()
    waiter = asyncio.create_task(receiver.wait(timeout=20))
    async with httpx.AsyncClient(trust_env=False) as client:
        r = await client.get(f"http://127.0.0.1:{receiver.port}/",
                             params={"state": receiver.state, "error": "access_denied",
                                     "error_description": "<script>x</script>"})
        assert "<script>" not in r.text and "&lt;script&gt;" in r.text
    with pytest.raises(ConnectorError, match="did not grant access"):
        await waiter


async def test_loopback_listener_times_out_clearly():
    receiver = oauth.LoopbackReceiver(label="Google")
    await receiver.start()
    with pytest.raises(ConnectorError, match="no answer from Google"):
        await receiver.wait(timeout=0.5)


# Real Google endpoints, answering a made-up client ---------------------------------------------------------
async def test_google_token_exchange_fails_clearly_for_an_unknown_client():
    async with http_client() as http:
        with pytest.raises(ConnectorError, match=r"Google refused the sign-in \(HTTP 401, invalid_client"):
            await google.exchange_code(http, FAKE_CLIENT, code="4/not-a-code", redirect_uri="http://127.0.0.1:1",
                                       verifier=oauth.pkce_pair()[0])


async def test_google_authorisation_page_rejects_an_unknown_client():
    """The real consent URL Jig builds; Google answers an unknown client ID with its own error page."""
    seen: list[str] = []
    task = asyncio.create_task(oauth.authorise(
        authorize_url=google.AUTHORIZE_URL, client_id=FAKE_CLIENT["client_id"],
        scopes=list(PROVIDERS["gmail"].access_levels["send"].scopes), extra=google.authorise_params(),
        open_browser=seen.append, label="Google", timeout=5))
    await wait_for(lambda: seen, timeout=5, what="the sign-in link")
    url = httpx.URL(seen[0])
    params = dict(url.params)
    assert url.host == "accounts.google.com"
    assert params["code_challenge_method"] == "S256" and params["access_type"] == "offline"
    assert params["scope"] == f"{gmail.S_READONLY} {gmail.S_COMPOSE}"
    assert params["redirect_uri"].startswith("http://127.0.0.1:")
    async with httpx.AsyncClient(trust_env=False, follow_redirects=True) as client:
        page = await client.get(seen[0])
    assert "invalid_client" in page.text or "OAuth client was not found" in page.text
    with pytest.raises(ConnectorError, match="no answer"):
        await task


# The connection store, the vault and authorised requests -------------------------------------------------
@pytest.fixture
def store(config):
    from jig.audit import AuditLog
    from jig.db import Database
    from jig.vault import Vault

    db = Database(config.db_path)
    yield ConnectionStore(db, Vault(db, config.vault), AuditLog(db))
    db.close()


def _fake_grant() -> Grant:
    return Grant(access_token="ya29.jig-test-not-a-real-access-token", refresh_token="1//jig-test-not-real",
                 expires_at=None, scopes=[gmail.S_READONLY, gmail.S_COMPOSE])


async def test_tokens_live_only_in_the_vault_and_gmail_rejects_a_bad_one(store):
    store.save("gmail", dataclasses.replace(_fake_grant(), refresh_token=None), account="test@example.com",
               access="send", via="test")
    row = store.db.one("SELECT * FROM connections WHERE provider = 'gmail'")
    assert "jig-test-not" not in json.dumps(row), "no token in the connections table"
    secret_row = store.db.one("SELECT ciphertext FROM secrets WHERE name = ?", (grant_secret("gmail"),))
    assert b"jig-test-not" not in bytes(secret_row["ciphertext"] or b""), "the grant is encrypted at rest"
    assert store.vault.describe(grant_secret("gmail"))["allowed_tools"] == []

    redactions: dict[str, str] = {}
    async with http_client() as http:
        connectors = Connectors(store, http, redactions)
        with pytest.raises(ConnectorAuthError) as info:
            await connectors.request("gmail", "GET", f"{gmail.API}/profile")
    message = str(info.value)
    assert "HTTP 401" in message and "jig connect gmail" in message
    assert "jig-test-not" not in message
    assert _fake_grant().access_token in redactions.values(), "every token used is redacted from results"
    assert store.get("gmail")["status"] == STATUS_NEEDS_RECONNECT
    assert "jig-test-not" not in json.dumps(store.audit.query(limit=50))
    with pytest.raises(ConnectorAuthError, match="needs reconnecting"):
        connectors.require_scope("gmail", gmail.READ, "read mail")
    assert "connector.needs_reconnect" in [r["kind"] for r in store.audit.query(limit=50)]


async def test_requests_only_go_to_the_providers_own_hosts(store):
    store.save("gmail", _fake_grant(), account="test@example.com", access="send", via="test")
    async with http_client() as http:
        with pytest.raises(ConnectorError, match="may only go to"):
            await Connectors(store, http, {}).request("gmail", "GET", "https://example.com/steal")


async def test_refresh_with_a_revoked_grant_asks_to_reconnect(store):
    store.set_client("google", FAKE_CLIENT, via="test")
    expired = dataclasses.replace(_fake_grant(), expires_at=1.0)
    store.save("gmail", expired, account="test@example.com", access="send", via="test")
    redactions: dict[str, str] = {}
    async with http_client() as http:
        with pytest.raises(ConnectorAuthError, match="reconnect with 'jig connect gmail'"):
            await Connectors(store, http, redactions).request("gmail", "GET", f"{gmail.API}/profile")
    assert FAKE_CLIENT["client_secret"] in redactions.values()
    assert store.get("gmail")["status"] == STATUS_NEEDS_RECONNECT


async def test_a_missing_app_client_asks_to_reconnect(store):
    store.save("gmail", _fake_grant(), account="test@example.com", access="send", via="test")
    async with http_client() as http:
        with pytest.raises(ConnectorAuthError, match="app client is no longer in the vault"):
            await Connectors(store, http, {}).request("gmail", "GET", f"{gmail.API}/profile")


async def test_disconnect_reports_what_google_said_and_deletes_the_tokens(store):
    store.save("gmail", _fake_grant(), account="test@example.com", access="send", via="test")
    async with http_client() as http:
        result = await Connectors(store, http, {}).disconnect("gmail", via="test")
    assert result["removed"] and "already revoked or expired" in result["at_provider"]
    assert store.get("gmail") is None
    assert all(s["name"] != grant_secret("gmail") for s in store.vault.list())
    kinds = [r["kind"] for r in store.audit.query(limit=50)]
    assert "connector.disconnected" in kinds


def test_scope_checks_explain_how_to_get_more_access(store):
    read_only = Grant(access_token="ya29.x", scopes=[gmail.S_READONLY])
    store.save("gmail", read_only, account="test@example.com", access="read", via="test")
    connectors = Connectors(store, None, {})  # type: ignore[arg-type]
    assert connectors.has_any_scope("gmail", gmail.READ)
    assert not connectors.has_any_scope("gmail", gmail.COMPOSE)
    with pytest.raises(ConnectorError, match="'read' access, which can't send mail"):
        connectors.require_scope("gmail", gmail.COMPOSE, "send mail")


# Core rule: connector secrets can't be named by any tool ---------------------------------------------------
async def test_core_rule_blocks_tools_from_naming_connector_secrets(jig):
    jig.connections.save("gmail", _fake_grant(), account="test@example.com", access="send", via="test")
    spec = jig.registry.get("web_fetch")
    findings = await evaluate_core(spec, {"url": "https://example.com",
                                          "headers": {"Authorization": "Bearer {{secret:connector.gmail.grant}}"}},
                                   jig.vault)
    assert any(f.rule_id == "secret-allowlist" and f.decision.value == "block" for f in findings)
    call = ToolCall(id="k1", name="web_fetch", arguments_raw=json.dumps(
        {"url": "https://example.com", "headers": {"X": "{{secret:connector.gmail.grant}}"}}))
    outcome = await jig.executor.execute(call, CallContext(run_id="r_k1", task_id=None, mode=Mode.ACTION,
                                                           intent="fetch example.com"))
    assert outcome.error_type == "PolicyBlocked" and "connected account" in outcome.error
    assert "sentinel.verdict" not in audit_kinds(jig, run_id="r_k1")


# The gate: Gmail tools through the real executor --------------------------------------------------------------
def test_gmail_tools_are_declared_with_the_right_effects(jig):
    specs = {t.name: t for t in jig.registry.all() if t.name.startswith("gmail_")}
    assert set(specs) == GMAIL_TOOLS
    for name in ("gmail_search", "gmail_read_thread", "gmail_list_labels"):
        assert specs[name].effect.value == "read" and not specs[name].outbound
    for name in ("gmail_create_draft", "gmail_send", "gmail_reply", "gmail_modify_labels", "gmail_archive"):
        assert specs[name].effect.value == "side_effect" and specs[name].outbound
        assert specs[name].human_only or specs[name].default_decision.value == "ask", name
    assert specs["gmail_send"].human_only and specs["gmail_reply"].human_only
    assert "delete" not in " ".join(specs), "there is no delete tool"


def test_gmail_tools_are_offered_only_while_connected(jig):
    def offered(mode: Mode) -> set[str]:
        return {s["function"]["name"] for s in jig.registry.schemas_for_mode(mode)} & GMAIL_TOOLS

    assert offered(Mode.ACTION) == set()
    jig.connections.save("gmail", Grant(access_token="ya29.x", scopes=[gmail.S_READONLY]), account="t@example.com",
                         access="read", via="test")
    assert offered(Mode.ACTION) == {"gmail_search", "gmail_read_thread", "gmail_list_labels"}
    jig.connections.save("gmail", Grant(access_token="ya29.x", scopes=[gmail.S_MODIFY]), account="t@example.com",
                         access="manage", via="test")
    assert offered(Mode.ACTION) == GMAIL_TOOLS
    assert offered(Mode.RESEARCH) == {"gmail_search", "gmail_read_thread", "gmail_list_labels"}


async def test_read_only_mode_refuses_sending_before_anything_else(jig):
    call = ToolCall(id="g1", name="gmail_send", arguments_raw=json.dumps(
        {"to": ["someone@example.com"], "subject": "Hi", "body": "Hello"}))
    outcome = await jig.executor.execute(call, CallContext(run_id="r_g1", task_id=None, mode=Mode.RESEARCH,
                                                           intent="research only"))
    assert outcome.error_type == "ModeViolation"
    assert "sentinel.verdict" not in audit_kinds(jig, run_id="r_g1")


@pytest.fixture
async def limited_jig(config, capabilities):
    limited = dataclasses.replace(config, connectors={"gmail": ConnectorLimits(
        allowed_recipients=["robyn-test@example.com"], required_prefix="[Jig test]")})
    runtime = Jig(limited)
    await runtime.start(run_scheduler=False, check_capabilities=False)
    try:
        yield runtime
    finally:
        await runtime.stop()


@pytest.mark.parametrize("args, why", [
    ({"to": ["stranger@example.com"], "subject": "[Jig test] hi", "body": "x"}, "allowed_recipients"),
    ({"to": ["robyn-test@example.com"], "cc": ["stranger@example.com"], "subject": "[Jig test] hi", "body": "x"},
     "allowed_recipients"),
    ({"to": ["robyn-test@example.com"], "subject": "Lunch?", "body": "x"}, "required_prefix"),
])
async def test_send_limits_block_before_the_sentinel_or_an_approval(limited_jig, args, why):
    call = ToolCall(id="g2", name="gmail_send", arguments_raw=json.dumps(args))
    outcome = await limited_jig.executor.execute(call, CallContext(run_id="r_g2", task_id=None, mode=Mode.ACTION,
                                                                   intent="send a test email"))
    assert outcome.error_type == "PolicyBlocked" and why in outcome.error
    kinds = audit_kinds(limited_jig, run_id="r_g2")
    assert "policy.connector_limit" in kinds
    assert "sentinel.verdict" not in kinds and "approval.requested" not in kinds
    assert limited_jig.approvals.list(status="pending") == []


async def test_an_allowed_send_still_needs_approval_and_denial_stops_it(limited_jig):
    """With the recipient and prefix allowed, the Sentinel reviews it and a human must approve; no rule or
    verdict can skip that. Denying it means nothing is sent."""
    limited_jig.rules.create(tool="gmail_send", decision="allow", note="test: try to make sending automatic")
    call = ToolCall(id="g3", name="gmail_send", arguments_raw=json.dumps(
        {"to": ["robyn-test@example.com"], "subject": "[Jig test] approval check", "body": "Hello from the tests"}))
    run = asyncio.create_task(limited_jig.executor.execute(
        call, CallContext(run_id="r_g3", task_id=None, mode=Mode.ACTION,
                          intent="Send a test email to robyn-test@example.com with subject '[Jig test] approval "
                                 "check'")))
    pending = await wait_for(lambda: limited_jig.approvals.list(status="pending"), what="the approval")
    approval = pending[0]
    assert approval["tool"] == "gmail_send"
    assert any(r["rule"] == "human-only-actions" for r in approval["reasons"])
    assert approval["sentinel"] is not None
    limited_jig.approvals.respond(approval["id"], approve=False, note="test")
    outcome = await run
    assert outcome.error_type == "ApprovalDenied"
    assert "tool.result" not in audit_kinds(limited_jig, run_id="r_g3")


async def test_an_approved_send_without_a_connection_fails_clearly(limited_jig):
    call = ToolCall(id="g4", name="gmail_send", arguments_raw=json.dumps(
        {"to": ["robyn-test@example.com"], "subject": "[Jig test] not connected", "body": "x"}))
    run = asyncio.create_task(limited_jig.executor.execute(
        call, CallContext(run_id="r_g4", task_id=None, mode=Mode.ACTION,
                          intent="Send a test email to robyn-test@example.com with subject '[Jig test] not "
                                 "connected'")))
    pending = await wait_for(lambda: limited_jig.approvals.list(status="pending"), what="the approval")
    limited_jig.approvals.respond(pending[0]["id"], approve=True)
    outcome = await run
    assert outcome.error_type == "ConnectorNotConnected" and "jig connect gmail" in outcome.error


async def test_reply_resolution_failure_is_reported_before_review(jig):
    """A reply looks its thread up first; without a connection that fails clearly, before the Sentinel."""
    call = ToolCall(id="g5", name="gmail_reply", arguments_raw=json.dumps(
        {"thread_id": "18c0ffee12345678", "to": ["someone@example.com"], "body": "Thanks"}))
    outcome = await jig.executor.execute(call, CallContext(run_id="r_g5", task_id=None, mode=Mode.ACTION,
                                                           intent="reply"))
    assert outcome.error_type == "ToolError" and "not connected" in outcome.error
    assert "sentinel.verdict" not in audit_kinds(jig, run_id="r_g5")


# Message building and parsing --------------------------------------------------------------------------------
def test_messages_are_built_with_exact_recipients_and_threading():
    raw = gmail._raw(to=["a@example.com"], cc=["b@example.com"], subject="Re: [Jig test] hello",
                     body="Line one\nLine two", in_reply_to="<m1@example.com>", references="<m0@example.com>")
    msg = email.message_from_bytes(base64.urlsafe_b64decode(raw), policy=email_policy.default)
    assert msg["To"] == "a@example.com" and msg["Cc"] == "b@example.com"
    assert msg["In-Reply-To"] == "<m1@example.com>" and msg["References"] == "<m0@example.com> <m1@example.com>"
    assert msg.get_content().strip() == "Line one\nLine two"


@pytest.mark.parametrize("args", [
    {"to": ["a@example.com"], "subject": "Hi\r\nBcc: evil@example.com", "body": "x"},
    {"to": ["Alice <a@example.com>"], "subject": "Hi", "body": "x"},
    {"to": ["a@example.com,evil@example.com"], "subject": "Hi", "body": "x"},
    {"to": [], "subject": "Hi", "body": "x"},
    {"to": ["a@example.com"], "subject": "", "body": "x"},
])
def test_header_injection_and_ambiguous_recipients_are_refused(args):
    with pytest.raises(ToolArgumentError):
        gmail._check_message(args)


def test_bodies_prefer_plain_text_and_list_attachments():
    def part(mime, text, **extra):
        return {"mimeType": mime, "body": {"data": base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")},
                "headers": [{"name": "Content-Type", "value": f"{mime}; charset=utf-8"}], **extra}

    payload = {"mimeType": "multipart/mixed", "headers": [], "parts": [
        {"mimeType": "multipart/alternative", "headers": [], "parts": [
            part("text/plain", "Café at 3pm?"), part("text/html", "<p>Caf&eacute; at <b>3pm</b>?</p>")]},
        {"mimeType": "application/pdf", "filename": "menu.pdf", "body": {"attachmentId": "a1", "size": 1234},
         "headers": []}]}
    text, attachments = gmail._body(payload)
    assert text == "Café at 3pm?"
    assert attachments == [{"filename": "menu.pdf", "mime_type": "application/pdf", "bytes": 1234}]
    html_only = {"mimeType": "text/html", "headers": [], "body": part("text/html", "<p>Hello <b>there</b></p>")["body"]}
    assert "Hello" in gmail._body(html_only)[0] and "<b>" not in gmail._body(html_only)[0]


def test_subject_prefix_allows_reply_markers():
    limits = type("C", (), {"connectors": {"gmail": ConnectorLimits(required_prefix="[Jig test]")}})()
    assert gmail.limits_problem(limits, {"subject": "Re: RE: [Jig test] x"}, None) is None
    assert gmail.limits_problem(limits, {}, {"thread_subject": "[Jig test] x"}) is None
    assert "required_prefix" in gmail.limits_problem(limits, {}, {"thread_subject": "Your invoice"})


# Config, API and CLI ---------------------------------------------------------------------------------------
def test_connector_limits_config(tmp_path):
    base = (load_config().source).read_text(encoding="utf-8")
    good = tmp_path / "good.toml"
    good.write_text(base + '\n[connectors.gmail]\nallowed_recipients = ["Me@Example.com"]\nrequired_prefix = "[Jig test]"\n',
                    encoding="utf-8")
    cfg = load_config(good, data_dir=tmp_path / "d")
    assert cfg.connectors["gmail"].allowed_recipients == ["me@example.com"]
    for bad in ('[connectors.gmail]\nallowed_recipients = ["*@example.com"]\n',
                '[connectors.nope]\nrequired_prefix = "x"\n',
                '[connectors.gmail]\nallow = []\n'):
        path = tmp_path / "bad.toml"
        path.write_text(base + "\n" + bad, encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(path, data_dir=tmp_path / "d")


def test_google_client_file_must_be_a_desktop_client(tmp_path):
    good = tmp_path / "client.json"
    good.write_text(json.dumps({"installed": {**FAKE_CLIENT, "redirect_uris": ["http://localhost"]}}), encoding="utf-8")
    assert google.client_from_json(good) == FAKE_CLIENT
    web = tmp_path / "web.json"
    web.write_text(json.dumps({"web": FAKE_CLIENT}), encoding="utf-8")
    with pytest.raises(ConnectorError, match="not a Desktop app client"):
        google.client_from_json(web)


def test_connections_api(config):
    from jig.api import create_app

    app = create_app(config)
    token = app.state.auth.tokens.get()
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        rows = {r["provider"]: r for r in client.get("/connections").json()}
        assert rows["gmail"]["connected"] is False and rows["gmail"]["client_configured"] is False
        assert set(rows["gmail"]["access_levels"]) == {"read", "send", "manage"}
        assert client.post("/connections/gmail/connect", json={"confirm": True}).status_code == 409
        assert client.post("/connections/gmail/connect", json={}).status_code == 400
        assert client.post("/connections/nope/connect", json={"confirm": True}).status_code == 404
        assert client.put("/vault/connector.gmail.grant", json={"value": "x"}).status_code == 400
        assert client.delete("/vault/connector.gmail.grant").status_code == 400
        tools = {t["name"]: t for t in client.get("/tools").json()}
        assert tools["gmail_send"]["available"] is False and tools["gmail_send"]["human_only"] is True


def test_cli_lists_connections(config, capsys):
    from jig.cli import main

    cfg = config.source
    assert main(["--config", str(cfg), "connections", "--data-dir", str(config.data_dir)]) == 0
    out = capsys.readouterr().out
    assert "Gmail (gmail): not connected (no google app client stored yet)" in out
    assert main(["--config", str(cfg), "disconnect", "gmail", "--yes", "--data-dir", str(config.data_dir)]) == 0


def test_approval_records_what_the_call_refers_to(jig):
    approval = jig.approvals.request(run_id="r_ap", task_id=None, tool_call_id="c1", tool="gmail_reply",
                                     args={"thread_id": "abc123456"}, reasons=[], sentinel=None,
                                     resolved={"thread_subject": "[Jig test] x"})
    assert jig.approvals.get(approval["id"])["resolved"] == {"thread_subject": "[Jig test] x"}
    assert approval["status"] == ApprovalStatus.PENDING
