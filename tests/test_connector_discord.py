"""Discord, tested without a Discord bot: the real gate and Sentinel, and real requests to Discord's HTTP API,
which answers a made-up bot token with its real error (HTTP 401). The live tests are in
test_connector_discord_live.py."""

from __future__ import annotations

import json

import pytest

from jig.config import ConnectorLimits
from jig.connectors import PROVIDERS, connect, discord
from jig.connectors.base import STATUS_NEEDS_RECONNECT, Connectors, Grant, grant_secret
from jig.constants import Mode
from jig.errors import ConnectorAuthError, ConnectorError, ToolArgumentError
from jig.model import ToolCall
from jig.policy.gate import CallContext
from jig.tools.builtin import http_client

from .conftest import audit_kinds
from .test_connectors import store  # noqa: F401  (fixture)

TOOLS = {"discord_list_channels", "discord_read_channel", "discord_post_message"}
READS = {"discord_list_channels", "discord_read_channel"}
FAKE_TOKEN = "MTAwMDAwMDAwMDAwMDAwMDAw.jigtst.not-a-real-discord-bot-token"
TEST_CHANNEL = "123456789012345678"
LIMITS = type("C", (), {"connectors": {"discord": ConnectorLimits(allowed_targets=[TEST_CHANNEL],
                                                                  required_prefix="[Jig test]")}})()


def _save(store, scopes=(discord.P_READ, discord.P_POST)):  # noqa: F811
    store.save(discord.NAME, Grant(access_token=FAKE_TOKEN, scopes=list(scopes), token_type="Bot"),
               account="jig-bot", access="write", via="test")


def _ctx(run_id: str, mode: Mode = Mode.ACTION, intent: str = "post a test message in Discord") -> CallContext:
    return CallContext(run_id=run_id, task_id=None, mode=mode, intent=intent)


def test_tools_are_declared_safely(jig):
    specs = {t.name: t for t in jig.registry.all() if t.name.startswith("discord_")}
    assert set(specs) == TOOLS
    for name, spec in specs.items():
        assert spec.category.value == "messages", name
        if name in READS:
            assert spec.effect.value == "read" and not spec.outbound and not spec.human_only, name
        else:
            assert spec.effect.value == "side_effect" and spec.outbound and spec.human_only, name
            assert spec.resolve is discord.resolve_target and spec.precheck is discord.limits_problem
    p = PROVIDERS[discord.NAME]
    assert p.kind == "token" and not p.needs_client and p.revoke is None
    assert p.api_hosts == frozenset({"discord.com"}) and [(i.name, i.secret) for i in p.inputs] == [("token", True)]


def test_tools_are_offered_only_with_the_matching_access(jig):
    def offered(mode: Mode) -> set[str]:
        return {s["function"]["name"] for s in jig.registry.schemas_for_mode(mode)} & TOOLS

    assert offered(Mode.ACTION) == set()
    _save(jig.connections, (discord.P_READ,))
    assert offered(Mode.ACTION) == READS
    _save(jig.connections)
    assert offered(Mode.ACTION) == TOOLS and offered(Mode.RESEARCH) == READS


async def test_the_real_api_rejects_a_made_up_bot_token(store):  # noqa: F811
    async with http_client() as http:
        with pytest.raises(ConnectorAuthError, match=r"Discord rejected the bot token \(HTTP 401\)") as info:
            await discord._connect(http, store, PROVIDERS[discord.NAME].access_levels["write"],
                                   {"token": f"Bot {FAKE_TOKEN}"})
    assert FAKE_TOKEN not in str(info.value) and "Nothing was connected" in str(info.value)


async def test_connecting_with_a_bad_token_stores_nothing(store):  # noqa: F811
    async with http_client() as http:
        with pytest.raises(ConnectorAuthError, match="HTTP 401"):
            await connect(discord.NAME, access="write", store=store, http=http, values={"token": FAKE_TOKEN},
                          via="test")
        with pytest.raises(ConnectorError, match="not a bot token"):
            await connect(discord.NAME, access="read", store=store, http=http, values={"token": "two words"},
                          via="test")
    assert store.get(discord.NAME) is None
    assert all(s["name"] != grant_secret(discord.NAME) for s in store.vault.list())


async def test_requests_use_the_bot_scheme_and_a_401_asks_to_reconnect(store):  # noqa: F811
    _save(store)
    redactions: dict[str, str] = {}
    async with http_client() as http:
        with pytest.raises(ConnectorAuthError, match="HTTP 401: 401: Unauthorized") as info:
            await Connectors(store, http, redactions).request(discord.NAME, "GET", f"{discord.API}/users/@me")
    assert FAKE_TOKEN not in str(info.value) and FAKE_TOKEN in redactions.values()
    assert store.get(discord.NAME)["status"] == STATUS_NEEDS_RECONNECT


async def test_a_read_with_a_rejected_token_fails_clearly(jig):
    _save(jig.connections)
    outcome = await jig.executor.execute(
        ToolCall(id="d1", name="discord_read_channel", arguments_raw=json.dumps({"channel_id": TEST_CHANNEL})),
        _ctx("r_d1", intent="read my Discord test channel"))
    assert outcome.error_type == "ConnectorAuthError" and "HTTP 401" in outcome.error
    assert "jig connect discord" in outcome.error and FAKE_TOKEN not in outcome.error
    assert "sentinel.verdict" not in audit_kinds(jig, run_id="r_d1")


async def test_requests_only_go_to_discord_over_https(store):  # noqa: F811
    _save(store)
    async with http_client() as http:
        c = Connectors(store, http, {})
        for url in ("https://example.com/api/v10/users/@me", "http://discord.com/api/v10/users/@me",
                    "https://cdn.discordapp.com/x"):
            with pytest.raises(ConnectorError, match="may only go to"):
                await c.request(discord.NAME, "GET", url)


async def test_disconnect_says_to_reset_the_token_in_the_portal(store):  # noqa: F811
    _save(store)
    async with http_client() as http:
        result = await Connectors(store, http, {}).disconnect(discord.NAME, via="test")
    assert result["removed"] and "no way for Jig to revoke" in result["at_provider"]
    assert result["manage_url"] == "https://discord.com/developers/applications"
    assert store.get(discord.NAME) is None


async def test_read_only_mode_refuses_posting(jig):
    call = ToolCall(id="d2", name="discord_post_message",
                    arguments_raw=json.dumps({"channel_id": TEST_CHANNEL, "content": "[Jig test] hi"}))
    outcome = await jig.executor.execute(call, _ctx("r_d2", Mode.RESEARCH, "look only"))
    assert outcome.error_type == "ModeViolation"
    assert "sentinel.verdict" not in audit_kinds(jig, run_id="r_d2")


async def test_a_post_without_a_connection_fails_clearly_before_review(jig):
    call = ToolCall(id="d3", name="discord_post_message",
                    arguments_raw=json.dumps({"channel_id": TEST_CHANNEL, "content": "[Jig test] hello"}))
    outcome = await jig.executor.execute(call, _ctx("r_d3"))
    assert outcome.error_type == "ToolError" and "not connected" in outcome.error
    kinds = audit_kinds(jig, run_id="r_d3")
    assert "sentinel.verdict" not in kinds and "approval.requested" not in kinds


@pytest.mark.parametrize("args, resolved, why", [
    ({"channel_id": "876543210987654321", "content": "[Jig test] x"}, {"channel_id": "876543210987654321"},
     "allowed_targets"),
    ({"channel_id": TEST_CHANNEL, "content": "Hello all"}, {"channel_id": TEST_CHANNEL}, "required_prefix"),
    ({"channel_id": TEST_CHANNEL, "content": "[Jig test] " + "x" * discord.MAX_CONTENT}, {"channel_id": TEST_CHANNEL},
     "too long"),
])
def test_limits_keep_posts_to_the_test_channel_and_test_messages(args, resolved, why):
    assert why in discord.limits_problem(LIMITS, args, resolved)


def test_limits_go_by_channel_id_not_name():
    assert discord.limits_problem(LIMITS, {"channel_id": TEST_CHANNEL, "content": "[Jig test] hi"},
                                  {"channel_id": TEST_CHANNEL, "channel": "jig-test"}) is None
    named = type("C", (), {"connectors": {"discord": ConnectorLimits(allowed_targets=["jig-test"])}})()
    assert "allowed_targets" in discord.limits_problem(named, {"channel_id": TEST_CHANNEL, "content": "x"},
                                                       {"channel_id": TEST_CHANNEL, "channel": "jig-test"})


def test_the_post_body_never_pings_anyone():
    text = "[Jig test] @everyone @here <@123456789012345678> <@&123456789012345678>"
    assert discord.message_body(text) == {"content": text, "allowed_mentions": {"parse": []}}
    for bad in ("", "  ", "x" * (discord.MAX_CONTENT + 1)):
        with pytest.raises(ToolArgumentError):
            discord.message_body(bad)
    assert discord.message_body("x" * discord.MAX_CONTENT)["content"]


@pytest.mark.parametrize("bad", ["", "general", "1234567890123456", "123456789012345678901", "12345678901234567a",
                                 "123456789012345678/../x", " 123456789012345678", None])
def test_snowflake_ids_are_checked(bad):
    with pytest.raises(ToolArgumentError):
        discord.snowflake(bad, "channel_id")


def test_valid_snowflakes_pass():
    for ok in ("12345678901234567", TEST_CHANNEL, "12345678901234567890"):
        assert discord.snowflake(ok, "channel_id") == ok


async def test_an_allowed_post_still_needs_a_human(jig):
    """Even with a rule saying 'allow', posting is human-only, through the real core rules."""
    from jig.policy.core import evaluate_core

    jig.rules.create(tool="discord_*", decision="allow", note="test: try to make posting automatic")
    findings = await evaluate_core(jig.registry.get("discord_post_message"),
                                   {"channel_id": TEST_CHANNEL, "content": "x"}, jig.vault)
    assert any(f.rule_id == "human-only-actions" and f.decision.value == "ask" for f in findings)
