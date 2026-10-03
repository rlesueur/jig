"""WhatsApp Business Cloud API, tested without a Meta app: the real gate and Sentinel, and a real request
to graph.facebook.com, which answers a made-up token with its real error (HTTP 401). The live test is in
test_connector_whatsapp_live.py."""

from __future__ import annotations

import json

import pytest

from jig.config import ConnectorLimits
from jig.connectors import PROVIDERS, connect, whatsapp
from jig.connectors.base import STATUS_NEEDS_RECONNECT, Connectors, Grant, grant_secret
from jig.constants import Mode
from jig.errors import ConnectorAuthError, ConnectorError, ToolArgumentError
from jig.model import ToolCall
from jig.policy.gate import CallContext
from jig.tools.builtin import http_client

from .conftest import audit_kinds
from .test_connectors import store  # noqa: F401  (fixture)

TOOLS = {"whatsapp_account", "whatsapp_send_message"}
FAKE_TOKEN = "EAAJigUnitTestTokenNotReal000000"
PHONE_ID = "100000000000000"
WABA_ID = "100000000000001"
RECIPIENT = "+447700900123"
LIMITS = type("C", (), {"connectors": {"whatsapp": ConnectorLimits(allowed_targets=[RECIPIENT],
                                                                   required_prefix="[Jig test]")}})()
DOCUMENTED_SEND = {
    "messaging_product": "whatsapp",
    "contacts": [{"input": "+16505551234", "wa_id": "16505551234"}],
    "messages": [{"id": "wamid.HBgLMTY0NjcwNDM1OTUVAgARGBI1RjQyNUE3NEYxMzAzMzQ5MkEA"}],
}


def _save(store, scopes=(whatsapp.P_READ, whatsapp.P_SEND)):  # noqa: F811
    store.save(whatsapp.NAME, Grant(access_token=FAKE_TOKEN, scopes=list(scopes),
                                    extra={"phone_number_id": PHONE_ID, "waba_id": WABA_ID}),
               account="Jig Test (+1 555)", access="send", via="test")


def _ctx(run_id: str, mode: Mode = Mode.ACTION, intent: str = "send a test WhatsApp message") -> CallContext:
    return CallContext(run_id=run_id, task_id=None, mode=mode, intent=intent)


def test_tools_are_declared_safely(jig):
    specs = {t.name: t for t in jig.registry.all() if t.name.startswith("whatsapp_")}
    assert set(specs) == TOOLS
    account, send = specs["whatsapp_account"], specs["whatsapp_send_message"]
    assert account.effect.value == "read" and not account.outbound and not account.human_only
    assert send.effect.value == "side_effect" and send.outbound and send.human_only
    assert send.resolve is whatsapp.resolve_recipient and send.precheck is whatsapp.limits_problem
    assert account.category.value == "messages" and send.category.value == "messages"
    p = PROVIDERS[whatsapp.NAME]
    assert p.kind == "token" and not p.needs_client and p.revoke is None
    assert p.api_hosts == frozenset({"graph.facebook.com"})
    assert [(i.name, i.secret) for i in p.inputs] == [("token", True), ("phone_number_id", True), ("waba_id", True)]
    assert p.default_access == "read"


def test_tools_are_offered_only_with_the_matching_access(jig):
    def offered(mode: Mode) -> set[str]:
        return {s["function"]["name"] for s in jig.registry.schemas_for_mode(mode)} & TOOLS

    assert offered(Mode.ACTION) == set()
    _save(jig.connections, (whatsapp.P_READ,))
    assert offered(Mode.ACTION) == {"whatsapp_account"} and offered(Mode.RESEARCH) == {"whatsapp_account"}
    _save(jig.connections)
    assert offered(Mode.ACTION) == TOOLS and offered(Mode.RESEARCH) == {"whatsapp_account"}


async def test_the_real_api_rejects_a_made_up_token(store):  # noqa: F811
    async with http_client() as http:
        with pytest.raises(ConnectorAuthError, match="HTTP 401") as info:
            await whatsapp._connect(http, store, PROVIDERS[whatsapp.NAME].access_levels["send"],
                                    {"token": FAKE_TOKEN, "phone_number_id": PHONE_ID, "waba_id": WABA_ID})
    text = str(info.value)
    assert "Nothing was connected" in text
    assert FAKE_TOKEN not in text and PHONE_ID not in text and WABA_ID not in text


async def test_connecting_with_a_bad_token_or_id_stores_nothing(store):  # noqa: F811
    values = {"token": FAKE_TOKEN, "phone_number_id": PHONE_ID, "waba_id": WABA_ID}
    async with http_client() as http:
        with pytest.raises(ConnectorAuthError, match="HTTP 401"):
            await connect(whatsapp.NAME, access="send", store=store, http=http, values=dict(values), via="test")
        with pytest.raises(ConnectorError, match="Nothing was connected"):
            await connect(whatsapp.NAME, access="read", store=store, http=http,
                          values={**values, "phone_number_id": "not-an-id"}, via="test")
        with pytest.raises(ConnectorError, match="must be given"):
            await connect(whatsapp.NAME, access="read", store=store, http=http, values={"token": FAKE_TOKEN},
                          via="test")
    assert store.get(whatsapp.NAME) is None
    assert all(s["name"] != grant_secret(whatsapp.NAME) for s in store.vault.list())


async def test_a_rejected_token_asks_to_reconnect_and_stays_out_of_the_audit(jig):
    _save(jig.connections)
    outcome = await jig.executor.execute(ToolCall(id="w1", name="whatsapp_account", arguments_raw="{}"),
                                         _ctx("r_w1", intent="see the WhatsApp business number"))
    assert outcome.error_type == "ConnectorAuthError" and "HTTP 401" in outcome.error
    assert "jig connect whatsapp" in outcome.error
    assert FAKE_TOKEN not in outcome.error and PHONE_ID not in outcome.error and WABA_ID not in outcome.error
    blob = json.dumps(jig.audit.query(limit=30))
    assert FAKE_TOKEN not in blob and PHONE_ID not in blob and WABA_ID not in blob
    assert "sentinel.verdict" not in audit_kinds(jig, run_id="r_w1")
    assert jig.connections.get(whatsapp.NAME)["status"] == STATUS_NEEDS_RECONNECT


async def test_requests_only_go_to_graph_facebook_over_https(store):  # noqa: F811
    _save(store)
    async with http_client() as http:
        c = Connectors(store, http, {})
        for url in ("https://example.com/v26.0/me", "http://graph.facebook.com/v26.0/me",
                    "https://facebook.com/v26.0/me", "https://graph.whatsapp.com/v26.0/me"):
            with pytest.raises(ConnectorError, match="may only go to"):
                await c.request(whatsapp.NAME, "GET", url)


async def test_disconnect_says_to_remove_the_token_in_meta(store):  # noqa: F811
    _save(store)
    async with http_client() as http:
        result = await Connectors(store, http, {}).disconnect(whatsapp.NAME, via="test")
    assert result["removed"] and "no way for Jig to revoke" in result["at_provider"]
    assert result["manage_url"] == whatsapp.MANAGE_URL
    assert store.get(whatsapp.NAME) is None
    assert all(s["name"] != grant_secret(whatsapp.NAME) for s in store.vault.list())


async def test_read_only_mode_refuses_sending(jig):
    call = ToolCall(id="w2", name="whatsapp_send_message",
                    arguments_raw=json.dumps({"recipient": RECIPIENT, "text": "[Jig test] hi"}))
    outcome = await jig.executor.execute(call, _ctx("r_w2", Mode.RESEARCH, "look only"))
    assert outcome.error_type == "ModeViolation"
    assert "sentinel.verdict" not in audit_kinds(jig, run_id="r_w2")


async def test_a_send_without_a_connection_fails_clearly_before_review(jig):
    call = ToolCall(id="w3", name="whatsapp_send_message",
                    arguments_raw=json.dumps({"recipient": RECIPIENT, "text": "[Jig test] hello"}))
    outcome = await jig.executor.execute(call, _ctx("r_w3"))
    assert outcome.error_type == "ToolError" and "not connected" in outcome.error
    kinds = audit_kinds(jig, run_id="r_w3")
    assert "sentinel.verdict" not in kinds and "approval.requested" not in kinds


@pytest.mark.parametrize("args, why", [
    ({"recipient": "+447700900456", "text": "[Jig test] x"}, "allowed_targets"),
    ({"recipient": RECIPIENT, "text": "Hello"}, "required_prefix"),
    ({"recipient": RECIPIENT, "text": "[Jig test] " + "x" * whatsapp.MAX_TEXT}, "too long"),
])
def test_limits_keep_sends_to_the_allowed_number_and_test_messages(args, why):
    assert why in whatsapp.limits_problem(LIMITS, args, {"recipient": args["recipient"]})


def test_the_send_body_is_a_cloud_api_text_message_and_holds_no_secret():
    text = "[Jig test] hello"
    assert whatsapp.message_body(RECIPIENT, text) == {
        "messaging_product": "whatsapp", "recipient_type": "individual", "to": RECIPIENT, "type": "text",
        "text": {"preview_url": False, "body": text},
    }
    assert FAKE_TOKEN not in json.dumps(whatsapp.message_body(RECIPIENT, text))
    for bad in ("", "  ", "x" * (whatsapp.MAX_TEXT + 1)):
        with pytest.raises(ToolArgumentError):
            whatsapp.message_body(RECIPIENT, bad)
    with pytest.raises(ToolArgumentError):
        whatsapp.message_body("07700900123", text)
    assert whatsapp.accepted_message_id(DOCUMENTED_SEND) == DOCUMENTED_SEND["messages"][0]["id"]
    for bad in ({}, {"messaging_product": "whatsapp"}, {"messaging_product": "whatsapp", "messages": []},
                {"messaging_product": "whatsapp", "messages": [{}]},
                {"error": {"message": "no"}, "messaging_product": "whatsapp",
                 "messages": [{"id": "wamid.x"}]}):
        with pytest.raises(ConnectorError, match="not sent"):
            whatsapp.accepted_message_id(bad)


def test_a_looked_up_number_does_not_include_the_ids():
    shown = whatsapp._public_number({"id": PHONE_ID, "display_phone_number": "+1 555 0100", "verified_name": "Jig",
                                     "quality_rating": "GREEN"})
    assert shown == {"verified_name": "Jig", "display_phone_number": "+1 555 0100", "quality_rating": "GREEN"}
    assert PHONE_ID not in json.dumps(shown)


@pytest.mark.parametrize("bad", ["", "123", "123456789", "123456789012345678901", "100000000000000a", "+100000000000000",
                                 None])
def test_meta_ids_are_digits_only(bad):
    with pytest.raises(ToolArgumentError):
        whatsapp.meta_id(bad, "Phone number ID")


async def test_an_allowed_send_still_needs_a_human(jig):
    """Even with a rule saying 'allow', sending is human-only, through the real core rules."""
    from jig.policy.core import evaluate_core

    jig.rules.create(tool="whatsapp_*", decision="allow", note="test: try to make sending automatic")
    findings = await evaluate_core(jig.registry.get("whatsapp_send_message"),
                                   {"recipient": RECIPIENT, "text": "x"}, jig.vault)
    assert any(f.rule_id == "human-only-actions" and f.decision.value == "ask" for f in findings)
