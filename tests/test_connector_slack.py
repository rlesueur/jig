"""Slack, tested without a Slack workspace: the real gate and Sentinel, and real requests to Slack's Web API,
which answers a made-up bot token with its real error (HTTP 200 and ``ok: false, invalid_auth``). The live
tests are in test_connector_slack_live.py."""

from __future__ import annotations

import json

import pytest

from jig.config import ConnectorLimits
from jig.connectors import PROVIDERS, connect, slack
from jig.connectors.base import STATUS_NEEDS_RECONNECT, Connectors, Grant, grant_secret
from jig.constants import Mode
from jig.errors import ConnectorAuthError, ConnectorError, ToolArgumentError
from jig.model import ToolCall
from jig.policy.gate import CallContext
from jig.tools.builtin import http_client

from .conftest import audit_kinds
from .test_connectors import store  # noqa: F401  (fixture)

TOOLS = {"slack_list_channels", "slack_read_channel", "slack_post_message", "slack_reply_in_thread"}
READS = {"slack_list_channels", "slack_read_channel"}
FAKE_TOKEN = "xoxb-made-up-for-jig-tests"
READ_SCOPES = [slack.S_CHANNELS_READ, slack.S_HISTORY]
WRITE_SCOPES = [*READ_SCOPES, slack.S_WRITE]
TEST_CHANNEL = "C0JIGTEST01"
LIMITS = type("C", (), {"connectors": {"slack": ConnectorLimits(allowed_targets=[TEST_CHANNEL, "#jig-test"],
                                                                required_prefix="[Jig test]")}})()


def _save(store, scopes=WRITE_SCOPES):  # noqa: F811
    store.save(slack.NAME, Grant(access_token=FAKE_TOKEN, scopes=list(scopes)), account="Test team/jig",
               access="write", via="test")


def _ctx(run_id: str, mode: Mode = Mode.ACTION, intent: str = "post a test message in Slack") -> CallContext:
    return CallContext(run_id=run_id, task_id=None, mode=mode, intent=intent)


def test_tools_are_declared_safely(jig):
    specs = {t.name: t for t in jig.registry.all() if t.name.startswith("slack_")}
    assert set(specs) == TOOLS
    for name, spec in specs.items():
        assert spec.category.value == "messages", name
        if name in READS:
            assert spec.effect.value == "read" and not spec.outbound and not spec.human_only, name
        else:
            assert spec.effect.value == "side_effect" and spec.outbound and spec.human_only, name
            assert spec.resolve is slack.resolve_target and spec.precheck is slack.limits_problem
    p = PROVIDERS[slack.NAME]
    assert p.kind == "token" and not p.needs_client and p.api_hosts == frozenset({"slack.com"})
    assert [(i.name, i.secret) for i in p.inputs] == [("token", True)]


def test_tools_are_offered_only_with_the_matching_access(jig):
    def offered(mode: Mode) -> set[str]:
        return {s["function"]["name"] for s in jig.registry.schemas_for_mode(mode)} & TOOLS

    assert offered(Mode.ACTION) == set()
    _save(jig.connections, READ_SCOPES)
    assert offered(Mode.ACTION) == READS
    _save(jig.connections, WRITE_SCOPES)
    assert offered(Mode.ACTION) == TOOLS and offered(Mode.RESEARCH) == READS


async def test_the_real_auth_test_rejects_a_made_up_token(store):  # noqa: F811
    async with http_client() as http:
        with pytest.raises(ConnectorAuthError, match=r"Slack rejected the bot token \(invalid_auth\)") as info:
            await slack._connect(http, store, PROVIDERS[slack.NAME].access_levels["write"], {"token": FAKE_TOKEN})
    assert FAKE_TOKEN not in str(info.value) and "Nothing was connected" in str(info.value)


async def test_connecting_with_a_bad_token_stores_nothing(store):  # noqa: F811
    async with http_client() as http:
        with pytest.raises(ConnectorAuthError, match="invalid_auth"):
            await connect(slack.NAME, access="write", store=store, http=http, values={"token": FAKE_TOKEN}, via="test")
        with pytest.raises(ConnectorError, match="must start with 'xoxb-'"):
            await connect(slack.NAME, access="read", store=store, http=http,
                          values={"token": "xoxp-0000-user-token"}, via="test")
        with pytest.raises(ConnectorError, match=r"Slack bot token \(xoxb-\.\.\.\) must be given; nothing was connected"):
            await connect(slack.NAME, access="read", store=store, http=http, values={}, via="test")
    assert store.get(slack.NAME) is None
    assert all(s["name"] != grant_secret(slack.NAME) for s in store.vault.list())


async def test_a_read_with_a_rejected_token_asks_to_reconnect(jig):
    """Slack answers HTTP 200 with ok:false; the connector still turns that into a reconnect."""
    _save(jig.connections)
    outcome = await jig.executor.execute(ToolCall(id="s1", name="slack_list_channels", arguments_raw="{}"),
                                         _ctx("r_s1", intent="list my Slack channels"))
    assert outcome.error_type == "ConnectorAuthError" and "invalid_auth" in outcome.error
    assert "jig connect slack" in outcome.error and FAKE_TOKEN not in outcome.error
    row = jig.connections.get(slack.NAME)
    assert row["status"] == STATUS_NEEDS_RECONNECT and "invalid_auth" in row["last_error"]
    assert "sentinel.verdict" not in audit_kinds(jig, run_id="r_s1")


async def test_requests_only_go_to_slack_over_https(store):  # noqa: F811
    _save(store)
    async with http_client() as http:
        c = Connectors(store, http, {})
        for url in ("https://example.com/api/auth.test", "http://slack.com/api/auth.test",
                    "https://files.slack.com/x"):
            with pytest.raises(ConnectorError, match="may only go to"):
                await c.request(slack.NAME, "GET", url)


async def test_disconnect_revokes_at_slack_and_reports_what_slack_said(store):  # noqa: F811
    _save(store)
    async with http_client() as http:
        result = await Connectors(store, http, {}).disconnect(slack.NAME, via="test")
    assert result["removed"] and "already revoked or not valid at Slack (invalid_auth)" in result["at_provider"]
    assert store.get(slack.NAME) is None


async def test_read_only_mode_refuses_posting(jig):
    for i, (name, args) in enumerate([
        ("slack_post_message", {"channel_id": TEST_CHANNEL, "text": "[Jig test] hi"}),
        ("slack_reply_in_thread", {"channel_id": TEST_CHANNEL, "thread_ts": "1712345678.123456", "text": "x"}),
    ]):
        outcome = await jig.executor.execute(ToolCall(id=f"s2{i}", name=name, arguments_raw=json.dumps(args)),
                                             _ctx(f"r_s2{i}", Mode.RESEARCH, "look only"))
        assert outcome.error_type == "ModeViolation", name
        assert "sentinel.verdict" not in audit_kinds(jig, run_id=f"r_s2{i}")


async def test_a_post_without_a_connection_fails_clearly_before_review(jig):
    call = ToolCall(id="s3", name="slack_post_message",
                    arguments_raw=json.dumps({"channel_id": TEST_CHANNEL, "text": "[Jig test] hello"}))
    outcome = await jig.executor.execute(call, _ctx("r_s3"))
    assert outcome.error_type == "ToolError" and "not connected" in outcome.error
    kinds = audit_kinds(jig, run_id="r_s3")
    assert "sentinel.verdict" not in kinds and "approval.requested" not in kinds


@pytest.mark.parametrize("args, resolved, why", [
    ({"channel_id": "C0SOMEWHERE", "text": "[Jig test] x"}, {"channel_id": "C0SOMEWHERE", "channel": "general"},
     "allowed_targets"),
    ({"channel_id": TEST_CHANNEL, "text": "Hello everyone"}, {"channel_id": TEST_CHANNEL, "channel": "jig-test"},
     "required_prefix"),
    ({"channel_id": TEST_CHANNEL, "text": "[Jig test] <!here> look"}, {"channel_id": TEST_CHANNEL}, "notify"),
    ({"channel_id": TEST_CHANNEL, "text": "[Jig test] " + "x" * slack.MAX_TEXT}, {"channel_id": TEST_CHANNEL},
     "too long"),
])
def test_limits_keep_posts_to_the_test_channel_and_test_messages(args, resolved, why):
    assert why in slack.limits_problem(LIMITS, args, resolved)


def test_limits_allow_a_test_message_in_the_test_channel_by_id_or_name():
    assert slack.limits_problem(LIMITS, {"channel_id": TEST_CHANNEL, "text": "[Jig test] hi"},
                                {"channel_id": TEST_CHANNEL, "channel": "jig-test"}) is None
    assert slack.limits_problem(LIMITS, {"channel_id": "C0OTHERIDX1", "text": "[Jig test] hi"},
                                {"channel_id": "C0OTHERIDX1", "channel": "jig-test"}) is None
    assert slack.limits_problem(None, {"channel_id": "C0OTHERIDX1", "text": "anything"}, None) is None


@pytest.mark.parametrize("text", ["<!here> hi", "<!channel>", "<!everyone|everyone>", "hi <!subteam^S0123|@team>",
                                  "@here look", "ping @channel", "@Everyone"])
def test_whole_channel_mentions_are_refused(text):
    assert "may not notify" in slack.mention_problem(text)
    with pytest.raises(ToolArgumentError, match="may not notify"):
        slack.message_body(TEST_CHANNEL, text)


@pytest.mark.parametrize("text", ["email me at robyn@here.com", "here and there", "<@U0123456789> thanks",
                                  "the channel is quiet"])
def test_ordinary_text_is_not_taken_for_a_whole_channel_mention(text):
    assert slack.mention_problem(text) is None


def test_the_post_body_is_plain_text_without_previews_or_name_linking():
    assert slack.message_body(TEST_CHANNEL, "[Jig test] hi", "1712345678.123456") == {
        "channel": TEST_CHANNEL, "text": "[Jig test] hi", "mrkdwn": False, "link_names": False,
        "unfurl_links": False, "unfurl_media": False, "thread_ts": "1712345678.123456"}
    for bad in ("", "   ", "x" * (slack.MAX_TEXT + 1)):
        with pytest.raises(ToolArgumentError):
            slack.message_body(TEST_CHANNEL, bad)


@pytest.mark.parametrize("bad", ["", "general", "#general", "c0123456789", "U0123456789", "C0123", "C0123/../x",
                                 "C01234567890123456789012"])
def test_channel_ids_are_checked(bad):
    with pytest.raises(ToolArgumentError):
        slack._channel_id(bad)


def test_valid_ids_pass():
    assert slack._channel_id("C0123456789") == "C0123456789" and slack._channel_id("G01ABCDEF23")
    assert slack._thread_ts("1712345678.123456")
    for bad in ("1712345678", "abc.def", "1712345678.123456&x=1"):
        with pytest.raises(ToolArgumentError):
            slack._thread_ts(bad)


async def test_an_allowed_post_still_needs_a_human(jig):
    """Even with a rule saying 'allow', posting is human-only, through the real core rules."""
    from jig.policy.core import evaluate_core

    jig.rules.create(tool="slack_*", decision="allow", note="test: try to make posting automatic")
    for name in ("slack_post_message", "slack_reply_in_thread"):
        findings = await evaluate_core(jig.registry.get(name), {"channel_id": TEST_CHANNEL, "text": "x"}, jig.vault)
        assert any(f.rule_id == "human-only-actions" and f.decision.value == "ask" for f in findings), name
