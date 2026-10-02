"""Matrix, tested without a Matrix account: the real gate and Sentinel, and real requests to matrix.org's
Client-Server API, which answers a made-up access token or password with its real errors. The live tests
are in test_connector_matrix_live.py."""

from __future__ import annotations

import json

import pytest

from jig.config import ConnectorLimits
from jig.connectors import PROVIDERS, connect, matrix
from jig.connectors.base import STATUS_NEEDS_RECONNECT, Connectors, Grant, grant_secret
from jig.constants import Mode
from jig.errors import ConnectorAuthError, ConnectorError, ToolArgumentError
from jig.model import ToolCall
from jig.policy.core import _CREDENTIAL_NAME, evaluate_core
from jig.policy.gate import CallContext
from jig.tools.builtin import http_client

from .conftest import audit_kinds
from .test_connectors import store  # noqa: F401  (fixture)

TOOLS = {"matrix_list_rooms", "matrix_read_room", "matrix_send_message"}
READS = {"matrix_list_rooms", "matrix_read_room"}
HS = "https://matrix-client.matrix.org"
FAKE_TOKEN = "syt_amlnLXRlc3Q_jigTestNotARealToken_000000"
ROOM = "!jigtestroom:matrix.org"
LIMITS = type("C", (), {"connectors": {"matrix": ConnectorLimits(allowed_targets=[ROOM],
                                                                 required_prefix="[Jig test]")}})()


def _grant(scopes=(matrix.S_READ, matrix.S_SEND)) -> Grant:
    return Grant(access_token=FAKE_TOKEN, scopes=list(scopes),
                 extra={"homeserver": HS, "login": "token", "user_id": "@jig-test:matrix.org"})


def test_posts_mention_nobody():
    assert matrix.MESSAGE_CONTENT == {"msgtype": "m.text", "m.mentions": {}}


def test_tools_are_declared_safely(jig):
    specs = {t.name: t for t in jig.registry.all() if t.name.startswith("matrix_")}
    assert set(specs) == TOOLS
    for name, spec in specs.items():
        assert spec.category.value == "messages" and not _CREDENTIAL_NAME.search(name), name
        if name in READS:
            assert spec.effect.value == "read" and not spec.outbound and not spec.human_only
        else:
            assert spec.effect.value == "side_effect" and spec.outbound and spec.human_only
            assert spec.resolve is matrix.resolve_room and spec.precheck is matrix.limits_problem
    spec = PROVIDERS["matrix"]
    assert spec.kind == "token" and not spec.needs_client and spec.api_hosts == frozenset()
    assert [(i.name, i.secret) for i in spec.inputs] == [("homeserver", False), ("login", False),
                                                         ("access_token", True)]


def test_tools_are_offered_only_with_the_matching_access(jig):
    def offered(mode: Mode) -> set[str]:
        return {s["function"]["name"] for s in jig.registry.schemas_for_mode(mode)} & TOOLS

    assert offered(Mode.ACTION) == set()
    jig.connections.save(matrix.NAME, _grant((matrix.S_READ,)), account="@jig-test:matrix.org", access="read",
                         via="test")
    assert offered(Mode.ACTION) == READS
    jig.connections.save(matrix.NAME, _grant(), account="@jig-test:matrix.org", access="send", via="test")
    assert offered(Mode.ACTION) == TOOLS and offered(Mode.RESEARCH) == READS


# The real homeserver -------------------------------------------------------------------------------------------
async def test_matrix_org_is_found_through_well_known_and_answers_versions():
    async with http_client() as http:
        base = await matrix.check_homeserver(http, "https://matrix.org/")
        assert base == HS, "matrix.org's .well-known delegates to matrix-client.matrix.org"
        versions = (await http.get(f"{base}/_matrix/client/versions")).json()["versions"]
    assert any(v.startswith("v1.") for v in versions)


async def test_the_real_whoami_rejects_a_made_up_token():
    async with http_client() as http:
        with pytest.raises(ConnectorAuthError, match=r"HTTP 401 M_UNKNOWN_TOKEN") as info:
            await matrix.whoami(http, HS, FAKE_TOKEN)
    assert FAKE_TOKEN not in str(info.value) and "nothing was connected" in str(info.value)


async def test_connecting_with_a_bad_token_stores_nothing(store):  # noqa: F811
    async with http_client() as http:
        with pytest.raises(ConnectorAuthError, match="M_UNKNOWN_TOKEN"):
            await connect(matrix.NAME, access=None, store=store, http=http, via="test",
                          values={"homeserver": "https://matrix.org", "login": "token", "access_token": FAKE_TOKEN})
    assert store.get(matrix.NAME) is None
    assert all(s["name"] != grant_secret(matrix.NAME) for s in store.vault.list())


async def test_a_wrong_password_is_refused_by_the_real_homeserver_and_stores_nothing(store):  # noqa: F811
    async with http_client() as http:
        with pytest.raises(ConnectorAuthError, match="refused the password login .* M_FORBIDDEN") as info:
            await connect(matrix.NAME, access="read", store=store, http=http, via="test",
                          values={"homeserver": "https://matrix.org",
                                  "login": "@jig-test-not-a-real-user-7f3a:matrix.org",
                                  "access_token": "jig-test-not-a-real-password"})
    assert "jig-test-not-a-real-password" not in str(info.value)
    assert store.get(matrix.NAME) is None


async def test_login_must_be_an_explicit_choice(store):  # noqa: F811
    async with http_client() as http:
        for login in ("password", "", "alice"):
            with pytest.raises(ConnectorError, match="login must be 'token'|must be given"):
                await connect(matrix.NAME, access=None, store=store, http=http, via="test",
                              values={"homeserver": "https://matrix.org", "login": login, "access_token": "x"})
    assert store.get(matrix.NAME) is None


@pytest.mark.parametrize("homeserver, why", [
    ("http://matrix.org", "https://"),
    ("https://localhost:8448", "no-local-network"),
    ("https://127.0.0.1", "no-local-network"),
    ("https://10.0.0.5", "no-local-network"),
    ("https://192.168.1.10:8008", "no-local-network"),
    ("https://[::1]", "no-local-network"),
    ("https://169.254.169.254", "no-local-network"),
    ("https://user:pw@matrix.org", "plain https://host"),
    ("https://matrix.org/?next=http://127.0.0.1", "plain https://host"),
])
async def test_local_private_and_plain_http_homeservers_are_refused(store, homeserver, why):  # noqa: F811
    async with http_client() as http:
        with pytest.raises(ConnectorError, match=why) as info:
            await connect(matrix.NAME, access=None, store=store, http=http, via="test",
                          values={"homeserver": homeserver, "login": "token", "access_token": FAKE_TOKEN})
    assert "nothing was connected" in str(info.value)
    assert store.get(matrix.NAME) is None


async def test_a_public_site_that_is_not_a_homeserver_is_refused():
    async with http_client() as http:
        with pytest.raises(ConnectorError, match="is not a Matrix homeserver"):
            await matrix.check_homeserver(http, "https://example.com")


async def test_requests_only_go_to_the_grants_homeserver_over_https(store):  # noqa: F811
    store.save(matrix.NAME, _grant(), account="@jig-test:matrix.org", access="send", via="test")
    assert PROVIDERS["matrix"].hosts(store.grant(matrix.NAME)) == frozenset({"matrix-client.matrix.org"})
    redactions: dict[str, str] = {}
    async with http_client() as http:
        c = Connectors(store, http, redactions)
        for url in ("https://matrix.org/_matrix/client/v3/account/whoami", "https://example.com/steal",
                    f"http://matrix-client.matrix.org{matrix.CLIENT}/account/whoami"):
            with pytest.raises(ConnectorError, match="may only go to"):
                await c.request(matrix.NAME, "GET", url)
        with pytest.raises(ConnectorAuthError, match="HTTP 401") as info:
            await c.request(matrix.NAME, "GET", f"{HS}{matrix.CLIENT}/account/whoami")
    assert FAKE_TOKEN not in str(info.value) and FAKE_TOKEN in redactions.values()
    assert store.get(matrix.NAME)["status"] == STATUS_NEEDS_RECONNECT


async def test_disconnect_logs_out_at_the_homeserver_and_reports_it(store):  # noqa: F811
    store.save(matrix.NAME, _grant(), account="@jig-test:matrix.org", access="send", via="test")
    async with http_client() as http:
        result = await Connectors(store, http, {}).disconnect(matrix.NAME, via="test")
    assert result["removed"] and "already invalid" in result["at_provider"]
    assert "M_UNKNOWN_TOKEN" in result["at_provider"] and FAKE_TOKEN not in result["at_provider"]
    assert store.get(matrix.NAME) is None


# The gate ----------------------------------------------------------------------------------------------------
async def test_read_only_mode_refuses_posting(jig):
    call = ToolCall(id="m1", name="matrix_send_message", arguments_raw=json.dumps({"room_id": ROOM, "text": "hi"}))
    outcome = await jig.executor.execute(call, CallContext(run_id="r_m1", task_id=None, mode=Mode.RESEARCH,
                                                           intent="look only"))
    assert outcome.error_type == "ModeViolation"
    assert "sentinel.verdict" not in audit_kinds(jig, run_id="r_m1")


async def test_a_post_without_a_connection_fails_clearly_before_review(jig):
    call = ToolCall(id="m2", name="matrix_send_message", arguments_raw=json.dumps(
        {"room_id": ROOM, "text": "[Jig test] hello"}))
    outcome = await jig.executor.execute(call, CallContext(run_id="r_m2", task_id=None, mode=Mode.ACTION,
                                                           intent="post a test message"))
    assert outcome.error_type == "ToolError" and "not connected" in outcome.error
    kinds = audit_kinds(jig, run_id="r_m2")
    assert "sentinel.verdict" not in kinds and "approval.requested" not in kinds


async def test_a_bad_room_id_is_refused_before_anything_else(jig):
    call = ToolCall(id="m3", name="matrix_read_room", arguments_raw=json.dumps({"room_id": "../../admin"}))
    jig.connections.save(matrix.NAME, _grant(), account="@jig-test:matrix.org", access="send", via="test")
    outcome = await jig.executor.execute(call, CallContext(run_id="r_m3", task_id=None, mode=Mode.ACTION,
                                                           intent="read a room"))
    assert outcome.error_type == "ToolArgumentError" and "not a Matrix room id" in outcome.error


async def test_an_allowed_post_still_needs_a_human(jig):
    jig.rules.create(tool="matrix_*", decision="allow", note="test: try to make posting automatic")
    findings = await evaluate_core(jig.registry.get("matrix_send_message"), {"room_id": ROOM, "text": "x"}, jig.vault)
    assert any(f.rule_id == "human-only-actions" and f.decision.value == "ask" for f in findings)


# Limits, ids and messages ----------------------------------------------------------------------------------
@pytest.mark.parametrize("args, resolved, why", [
    ({"room_id": "!other:matrix.org", "text": "[Jig test] x"}, {"encrypted": False}, "allowed_targets"),
    ({"room_id": ROOM, "text": "hello"}, {"encrypted": False}, "required_prefix"),
    ({"room_id": ROOM, "text": "[Jig test] x"}, {"encrypted": True}, "end-to-end encrypted"),
])
def test_limits_keep_posts_to_the_test_room_and_out_of_encrypted_rooms(args, resolved, why):
    assert why in matrix.limits_problem(LIMITS, args, resolved)


def test_limits_allow_a_test_message_in_the_test_room():
    assert matrix.limits_problem(LIMITS, {"room_id": ROOM, "text": "[Jig test] x"}, {"encrypted": False}) is None
    assert "encrypted" in matrix.limits_problem(None, {"room_id": ROOM, "text": "x"}, {"encrypted": True})


@pytest.mark.parametrize("value", ["!abc123:matrix.org", "!AbC-_.=+/x:example.org:8448",
                                   "!31hneApxJ_1o-63DmFrpeqnkFfWppnzWso1JvH3ogLM"])
def test_room_ids_are_checked_and_escaped(value):
    path = matrix.room_path(value)
    assert path.startswith("/rooms/%21") and "/" not in path[len("/rooms/"):] and ":" not in path


@pytest.mark.parametrize("bad", ["", "abc", "#alias:matrix.org", "!a b:matrix.org", "!abc:matrix.org/../x",
                                 "!abc:matrix.org?x=1", "!" + "a" * 300, "../../admin"])
def test_bad_room_ids_are_refused(bad):
    with pytest.raises(ToolArgumentError):
        matrix.room_id(bad)


@pytest.mark.parametrize("bad", ["", "   ", "x" * (matrix.MAX_TEXT + 1)])
def test_message_text_is_checked(bad):
    with pytest.raises(ToolArgumentError):
        matrix.check_text(bad)


def test_encrypted_and_non_text_events_are_reported_honestly():
    base = {"event_id": "$e", "sender": "@a:matrix.org", "origin_server_ts": 1_790_000_000_000}
    enc = matrix.message({**base, "type": "m.room.encrypted", "content": {"algorithm": "m.megolm.v1.aes-sha2"}}, 100)
    assert enc["encrypted"] and enc["text"] is None and "cannot read" in enc["note"]
    text = matrix.message({**base, "type": "m.room.message", "content": {"msgtype": "m.text", "body": "abcdef"}}, 3)
    assert text["text"] == "abc" and text["truncated"] and text["time"].endswith("+00:00")
    image = matrix.message({**base, "type": "m.room.message", "content": {"msgtype": "m.image", "body": "a.png"}}, 100)
    assert image["text"] is None and "text messages only" in image["note"]
    assert "redacted" in matrix.message({**base, "type": "m.room.message", "content": {}}, 100)["note"]
    assert matrix.message({**base, "type": "m.room.member", "content": {"membership": "join"}}, 100) is None
