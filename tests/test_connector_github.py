"""GitHub, tested without the user's GitHub account: the real gate and Sentinel, and real requests to
api.github.com, which answers a made-up token with its real errors. The live tests are in
test_connector_github_live.py."""

from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace

import httpx
import pytest

from jig.config import ConnectorLimits
from jig.connectors import connect, github as gh
from jig.connectors.base import STATUS_NEEDS_RECONNECT, Connectors, Grant
from jig.constants import EventType, Mode
from jig.errors import ConnectorAuthError, ConnectorError, ConnectorNotConnected, ToolArgumentError
from jig.model import ToolCall
from jig.policy.gate import CallContext
from jig.tools.builtin import http_client

from .conftest import audit_kinds, wait_for
from .test_connectors import store  # noqa: F401  (fixture)

APP_ID = "Iv23liJigTestNotReal0"
MADE_UP = "github_pat_11JIGTEST0000000000000_notarealtokenjustfortestingjigsconnector00000000000"
TOOLS = {"github_list_repos", "github_list_issues", "github_read_issue", "github_read_file", "github_comment",
         "github_create_issue"}
READS = {"github_list_repos", "github_list_issues", "github_read_issue", "github_read_file"}
WRITES = TOOLS - READS
LIMITS = type("C", (), {"connectors": {"github": ConnectorLimits(
    allowed_targets=["Robyn/jig-connector-test"], allowed_recipients=["@robyn"], required_prefix="[Jig test]")}})()


def _save(store, scopes: list[str], access: str = "write") -> None:  # noqa: F811
    store.save(gh.NAME, Grant(access_token=MADE_UP, scopes=scopes), account="robyn", access=access, via="test")


async def _run(jig, name: str, args: dict, *, run_id: str, mode: Mode = Mode.ACTION):
    return await jig.executor.execute(ToolCall(id=run_id, name=name, arguments_raw=json.dumps(args)),
                                      CallContext(run_id=run_id, task_id=None, mode=mode, intent="test GitHub"))


# The real GitHub API ------------------------------------------------------------------------------------------
async def test_the_real_github_api_rejects_a_made_up_token(store):  # noqa: F811
    _save(store, [gh.S_READ])
    async with http_client() as http:
        with pytest.raises(ConnectorAuthError, match="HTTP 401: Bad credentials") as info:
            await Connectors(store, http, {}).request(gh.NAME, "GET", f"{gh.API}/user", headers=dict(gh.HEADERS))
    assert MADE_UP not in str(info.value)
    assert store.get(gh.NAME)["status"] == STATUS_NEEDS_RECONNECT


async def test_connecting_with_a_made_up_token_fails_at_github_and_stores_nothing(store):  # noqa: F811
    async with http_client() as http:
        with pytest.raises(ConnectorError, match="GitHub rejected the token .*Bad credentials.*nothing was connected") \
                as info:
            await connect(gh.NAME, access="write", store=store, http=http, values={"token": MADE_UP}, method="token",
                          via="test")
    assert MADE_UP not in str(info.value)
    assert store.get(gh.NAME) is None
    with pytest.raises(ConnectorNotConnected):
        store.grant(gh.NAME)
    assert "connector.connected" not in [r["kind"] for r in store.audit.query(limit=100)]


@pytest.mark.parametrize("token, why", [
    ("", "must be given"),
    ("ghp_" + "a" * 36, "fine-grained personal access token"),
    ("github_pat_11JIGTEST0000 0000000000_pasted with a space", "characters a GitHub token never has"),
    ("github_pat_11JIGTEST0000000000000\nsecond line", "characters a GitHub token never has"),
])
async def test_classic_missing_or_damaged_tokens_are_refused_before_github(store, token, why):  # noqa: F811
    async with http_client() as http:
        with pytest.raises(ConnectorError, match=why):
            await connect(gh.NAME, access="read", store=store, http=http, values={"token": token}, method="token", via="test")
    assert store.get(gh.NAME) is None


async def test_requests_only_go_to_githubs_api_over_https(store):  # noqa: F811
    _save(store, [gh.S_READ])
    async with http_client() as http:
        c = Connectors(store, http, {})
        for url in ("https://github.com/settings", "http://api.github.com/user", "https://api.github.com.evil.example/user",
                    "https://uploads.github.com/repos/a/b/releases"):
            with pytest.raises(ConnectorError, match="may only go to"):
                await c.request(gh.NAME, "GET", url)


async def test_disconnecting_deletes_the_token_and_points_to_github_to_revoke_it(store):  # noqa: F811
    _save(store, [gh.S_READ])
    async with http_client() as http:
        result = await Connectors(store, http, {}).disconnect(gh.NAME, via="test")
    assert result["removed"] and gh.MANAGE_URL in result["at_provider"]
    assert store.get(gh.NAME) is None
    with pytest.raises(ConnectorNotConnected):
        store.grant(gh.NAME)


async def test_rate_limit_notes_come_from_githubs_own_rate_limit_status():
    async with httpx.AsyncClient(trust_env=False, headers={"User-Agent": "Jig tests"}) as http:
        r = await http.get(f"{gh.API}/rate_limit", headers=gh.HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    note = gh._rate_reset_note(body)
    if any(v.get("remaining") == 0 for v in body["resources"].values()):
        assert "resets at" in note
    else:
        assert "secondary limit" in note
    body["resources"]["core"]["remaining"] = 0
    assert f"the core limit ({body['resources']['core']['limit']} requests) resets at" in gh._rate_reset_note(body)


async def test_404_and_403_are_explained(store):  # noqa: F811
    _save(store, [gh.S_READ])
    async with http_client() as http:
        ctx = SimpleNamespace(connectors=Connectors(store, http, {}))
        missing = await gh._explain(ctx, ConnectorError("GitHub returned HTTP 404: Not Found", status=404),
                                    "repository 'a/b'")
        assert "found no repository 'a/b'" in str(missing) and gh.MANAGE_URL in str(missing)
        refused = await gh._explain(ctx, ConnectorError("GitHub refused this (HTTP 403): Resource not accessible by "
                                                        "personal access token", status=403), "a comment on #1 in a/b")
        assert "Resource not accessible" in str(refused) and "lack the permission" in str(refused)
        store.save(gh.NAME, Grant(access_token="ghu_x", scopes=[gh.S_READ],
                                  extra={"method": "app", "client_id": APP_ID, "app_slug": "jig-test"}),
                   account="robyn", access="read", via="test")
        missing = await gh._explain(ctx, ConnectorError("GitHub returned HTTP 404: Not Found", status=404),
                                    "repository 'a/b'")
        assert "repositories you chose for the Jig GitHub App" in str(missing)
        assert gh.install_url("jig-test") in str(missing)


# Signing in with the Jig GitHub App (device flow) ------------------------------------------------------------
def _use_app(store, client_id: str = APP_ID, slug: str = "jig-test-not-real") -> None:  # noqa: F811
    store.settings = {"github": ConnectorLimits(client_id=client_id, app_slug=slug)}


def test_github_signs_in_with_the_app_and_keeps_tokens_as_the_advanced_option(store):  # noqa: F811
    spec = gh.PROVIDER
    assert spec.kind == "device" and spec.methods == ("device", "token") and not spec.needs_client
    _use_app(store)
    row = {r["provider"]: r for r in store.status()}[gh.NAME]
    assert row["methods"] == ["device", "token"] and row["client_configured"] and row["client_source"] == "config"
    assert row["install_url"] == "https://github.com/apps/jig-test-not-real/installations/new"
    assert row["inputs"] == [{"name": "token", "prompt": "Fine-grained personal access token", "secret": True,
                              "optional": False}]


def test_the_built_in_app_says_clearly_when_it_isnt_set_up(store, monkeypatch):  # noqa: F811
    monkeypatch.setattr(gh.apps, "GITHUB_APP_CLIENT_ID", "")
    monkeypatch.setattr(gh.apps, "GITHUB_APP_SLUG", "")
    with pytest.raises(ConnectorError, match="built-in GitHub App isn't set up") as info:
        gh.resolve_app(store)
    assert "jig connect github --token" in str(info.value)
    row = {r["provider"]: r for r in store.status()}[gh.NAME]
    assert row["client_configured"] is False and row["install_url"] is None


@pytest.mark.parametrize("client_id, slug, why", [
    (APP_ID, "", "needs both client_id and app_slug"),
    ("", "jig", "needs both client_id and app_slug"),
    ("not an id", "jig", "isn't a GitHub App client ID"),
    (APP_ID, "Not/A/Slug", "isn't a GitHub App URL name"),
])
def test_your_own_app_must_be_named_in_full(store, client_id, slug, why):  # noqa: F811
    _use_app(store, client_id, slug)
    with pytest.raises(ConnectorError, match=why):
        gh.resolve_app(store)


async def test_github_refuses_a_made_up_app_and_nothing_is_shown_or_stored(store):  # noqa: F811
    _use_app(store)
    shown: list[dict] = []
    async with http_client() as http:
        with pytest.raises(ConnectorError, match=r"doesn't know a GitHub App with client ID .*HTTP 404"):
            await connect(gh.NAME, access="write", store=store, http=http, show_code=shown.append, via="test")
    assert shown == [] and store.get(gh.NAME) is None


async def test_a_refused_refresh_needs_a_reconnect(store):  # noqa: F811
    _use_app(store)
    store.save(gh.NAME, Grant(access_token="ghu_" + "x" * 36, refresh_token="ghr_" + "y" * 76, expires_at=1.0,
                              scopes=[gh.S_READ], extra={"method": "app", "client_id": APP_ID}),
               account="robyn", access="read", via="test")
    async with http_client() as http:
        with pytest.raises(ConnectorAuthError, match="Reconnect with 'jig connect github'"):
            await Connectors(store, http, {}).request(gh.NAME, "GET", f"{gh.API}/user", headers=dict(gh.HEADERS))
        with pytest.raises(ConnectorAuthError, match="doesn't record which app"):
            await gh.refresh(http, store.vault, Grant(access_token="ghu_x", refresh_token="ghr_x"))
    assert store.get(gh.NAME)["status"] == STATUS_NEEDS_RECONNECT


async def test_disconnecting_an_app_sign_in_points_to_githubs_app_settings(store):  # noqa: F811
    store.save(gh.NAME, Grant(access_token="ghu_x", scopes=[gh.S_READ], extra={"method": "app"}), account="robyn",
               access="read", via="test")
    async with http_client() as http:
        result = await Connectors(store, http, {}).disconnect(gh.NAME, via="test")
    assert gh.APP_AUTHORISATIONS_URL in result["at_provider"] and gh.INSTALLATIONS_URL in result["at_provider"]


@pytest.mark.skipif(not gh.apps.GITHUB_APP_CLIENT_ID, reason="Jig's GitHub App isn't registered yet: set "
                    "GITHUB_APP_CLIENT_ID and GITHUB_APP_SLUG in jig/connectors/apps.py (docs/connectors-setup.md)")
async def test_the_built_in_app_gets_a_real_device_code(store):  # noqa: F811
    shown: list[dict] = []
    async with http_client() as http:
        task = asyncio.create_task(connect(gh.NAME, access="read", store=store, http=http, show_code=shown.append,
                                           via="test"))
        await wait_for(lambda: shown or task.done(), timeout=30, what="GitHub's device code")
        task.cancel()
        with pytest.raises((asyncio.CancelledError, ConnectorError)):
            await task
    assert shown, "GitHub gave no code"
    assert shown[0]["verification_uri"] == "https://github.com/login/device"
    assert re.fullmatch(r"[A-Z0-9]{4}-[A-Z0-9]{4}", shown[0]["user_code"])


@pytest.mark.parametrize("header, expected", [
    ("2026-12-31 00:00:00 UTC", 1798675200.0),
    ("2026-12-31 01:00:00 +0100", 1798675200.0),
    (None, None),
])
def test_token_expiry_is_read_from_githubs_header(header, expected):
    assert gh._expiry(header) == expected


def test_an_unreadable_expiry_fails_clearly():
    with pytest.raises(ConnectorError, match="can't read"):
        gh._expiry("next Tuesday")


# Declarations and availability -----------------------------------------------------------------------------
def test_tools_are_declared_safely(jig):
    specs = {t.name: t for t in jig.registry.all() if t.name.startswith("github_")}
    assert set(specs) == TOOLS
    for name in ("github_list_repos", "github_read_file"):
        assert specs[name].category.value == "code" and specs[name].avatar_variant.value == "coding"
    for name in ("github_list_issues", "github_read_issue"):
        assert specs[name].category.value == "messages" and specs[name].avatar_variant.value == "browsing"
    for name in READS:
        assert specs[name].effect.value == "read" and not specs[name].outbound and not specs[name].human_only, name
    for name in WRITES:
        s = specs[name]
        assert s.effect.value == "side_effect" and s.outbound and s.human_only, name
        assert s.category.value == "messages" and s.avatar_variant.value == "writing"
        assert s.resolve is gh.resolve_target and s.precheck is gh.limits_problem
    from jig.policy.core import _CREDENTIAL_NAME
    assert not any(_CREDENTIAL_NAME.search(n) for n in TOOLS)


def test_tools_are_offered_only_with_the_matching_access(jig):
    def offered(mode: Mode) -> set[str]:
        return {s["function"]["name"] for s in jig.registry.schemas_for_mode(mode)} & TOOLS

    assert offered(Mode.ACTION) == set()
    _save(jig.connections, [gh.S_READ], access="read")
    assert offered(Mode.ACTION) == READS
    _save(jig.connections, [gh.S_READ, gh.S_WRITE])
    assert offered(Mode.ACTION) == TOOLS and offered(Mode.RESEARCH) == READS


def test_access_levels_are_pseudo_scopes():
    spec = gh.PROVIDER
    assert spec.kind == "device" and not spec.needs_client and spec.refresh is gh.refresh and spec.revoke is gh.revoke
    assert spec.api_hosts == frozenset({"api.github.com"}) and spec.default_access == "read"
    assert spec.access_levels["read"].scopes == ("github:read",)
    assert spec.access_levels["write"].scopes == ("github:read", "github:write")
    assert [(i.name, i.secret) for i in spec.inputs] == [("token", True)]


# The gate ------------------------------------------------------------------------------------------------
async def test_read_only_mode_refuses_every_write(jig):
    for i, (name, args) in enumerate([
        ("github_comment", {"repo": "Robyn/jig-connector-test", "number": 1, "body": "[Jig test] x"}),
        ("github_create_issue", {"repo": "Robyn/jig-connector-test", "title": "[Jig test] x"}),
    ]):
        outcome = await _run(jig, name, args, run_id=f"r_gh_ro{i}", mode=Mode.RESEARCH)
        assert outcome.error_type == "ModeViolation", name


async def test_a_write_without_a_connection_fails_clearly_before_the_sentinel(jig):
    outcome = await _run(jig, "github_comment", {"repo": "Robyn/jig-connector-test", "number": 1,
                                                 "body": "[Jig test] x"}, run_id="r_gh_none")
    assert outcome.error_type == "ToolError" and "GitHub is not connected" in outcome.error
    kinds = audit_kinds(jig, run_id="r_gh_none")
    assert "sentinel.verdict" not in kinds and "approval.requested" not in kinds


async def test_a_write_with_read_access_fails_clearly_before_the_sentinel(jig):
    _save(jig.connections, [gh.S_READ], access="read")
    outcome = await _run(jig, "github_create_issue", {"repo": "Robyn/jig-connector-test", "title": "[Jig test] x"},
                         run_id="r_gh_read")
    assert outcome.error_type == "ToolError" and "'read' access, which can't" in outcome.error
    kinds = audit_kinds(jig, run_id="r_gh_read")
    assert "sentinel.verdict" not in kinds and "approval.requested" not in kinds


async def test_a_bad_repo_name_is_refused_before_any_request(jig):
    _save(jig.connections, [gh.S_READ, gh.S_WRITE])
    outcome = await _run(jig, "github_comment", {"repo": "../../user", "number": 1, "body": "[Jig test] x"},
                         run_id="r_gh_bad")
    assert outcome.error_type == "ToolError" and "is not 'owner/name'" in outcome.error
    assert "sentinel.verdict" not in audit_kinds(jig, run_id="r_gh_bad")
    wrong_type = await _run(jig, "github_read_issue", {"repo": "a/b", "number": "1"}, run_id="r_gh_type")
    assert wrong_type.error_type == "ToolArgumentError" and "integer" in wrong_type.error


async def test_a_read_reaches_github_and_reports_its_real_answer(jig):
    """With a made-up token the read starts (the avatar shows 'coding'), GitHub answers 401, the connection is
    marked for reconnecting, and the token never appears in the error."""
    _save(jig.connections, [gh.S_READ], access="read")
    seen: list[dict] = []
    jig.bus.add_listener(lambda e: seen.append(e.data) if e.type == EventType.TOOL_START else None)
    outcome = await _run(jig, "github_read_file", {"repo": "octocat/Hello-World", "path": "README"},
                         run_id="r_gh_read_file")
    assert outcome.error_type == "ConnectorAuthError" and "HTTP 401" in outcome.error
    assert MADE_UP not in outcome.error
    assert any(d["tool"] == "github_read_file" and d["variant"] == "coding" for d in seen)
    assert jig.connections.get(gh.NAME)["status"] == STATUS_NEEDS_RECONNECT


async def test_an_allow_rule_does_not_remove_the_approval(jig):
    """A custom 'allow' rule matches, but the core human-only rule still asks: the gate adds every core 'ask'
    finding to the approval reasons whatever the custom rule says. (Each write's lookup needs a real account, so
    the full approval path is in the live test.)"""
    from jig.policy.core import evaluate_core

    rule = jig.rules.create(tool="github_*", decision="allow", note="test: try to make GitHub writes automatic")
    for name, args in (("github_comment", {"repo": "a/b", "number": 1, "body": "x"}),
                       ("github_create_issue", {"repo": "a/b", "title": "x"})):
        assert jig.rules.match(name, args)["id"] == rule["id"]
        findings = await evaluate_core(jig.registry.get(name), args, jig.vault)
        assert any(f.rule_id == "human-only-actions" and f.decision.value == "ask" for f in findings), name


# Limits ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("args, resolved, why", [
    ({"repo": "someone/else", "number": 1, "body": "[Jig test] x"}, {"repo": "someone/else"}, "allowed_targets"),
    ({"repo": "Robyn/jig-connector-test", "number": 1, "body": "[Jig test] x"}, {"repo": "elsewhere/moved"},
     "allowed_targets"),
    ({"repo": "Robyn/jig-connector-test", "number": 1, "body": "Looks good"}, {"repo": "Robyn/jig-connector-test"},
     "required_prefix"),
    ({"repo": "Robyn/jig-connector-test", "number": 1, "body": ""}, {"repo": "Robyn/jig-connector-test"},
     "required_prefix"),
    ({"repo": "Robyn/jig-connector-test", "title": "Bug", "body": "[Jig test] x"}, {"repo": "Robyn/jig-connector-test"},
     "required_prefix"),
    ({"repo": "Robyn/jig-connector-test", "number": 1, "body": "[Jig test] cc @boss"},
     {"repo": "Robyn/jig-connector-test"}, "allowed_recipients"),
    ({"repo": "Robyn/jig-connector-test", "title": "[Jig test] x", "body": "for @my-org/everyone"},
     {"repo": "Robyn/jig-connector-test"}, "allowed_recipients"),
])
def test_limits_keep_writes_to_the_test_repo_and_test_text(args, resolved, why):
    assert why in gh.limits_problem(LIMITS, args, resolved)


@pytest.mark.parametrize("args", [
    {"repo": "robyn/JIG-CONNECTOR-TEST", "number": 7, "body": "[Jig test] thanks @Robyn, see a@b.com"},
    {"repo": "Robyn/jig-connector-test", "title": "[Jig test] x", "body": "no prefix needed here"},
])
def test_limits_allow_test_writes_in_the_test_repo_in_any_case(args):
    assert gh.limits_problem(LIMITS, args, {"repo": "Robyn/jig-connector-test"}) is None


def test_no_limits_means_no_limit_problem():
    assert gh.limits_problem(type("C", (), {"connectors": {}})(), {"repo": "a/b", "number": 1, "body": "x"},
                             {"repo": "a/b"}) is None


# Argument checks ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["octocat/Hello-World", "a/b", "my-org/repo.name_2", "x/.github"])
def test_repo_names_are_accepted(value):
    assert gh._repo_path(value) == f"/repos/{value}"


@pytest.mark.parametrize("bad", ["octocat", "../x", "a/..", "a/.", "a/b/c", "a/b?x=1", "a/b#x", "-a/b", "a b/c",
                                 "a/b%2F..", "", "/a/b", "a\\b/c", "a/" + "x" * 101])
def test_repo_names_that_could_leave_the_repo_are_refused(bad):
    with pytest.raises(ToolArgumentError):
        gh._repo_path(bad)


@pytest.mark.parametrize("bad", [0, -1, True, "1", 1.0, 2**31])
def test_issue_numbers_must_be_positive_ints(bad):
    with pytest.raises(ToolArgumentError):
        gh._number(bad)


@pytest.mark.parametrize("value, expected", [("README.md", "README.md"), ("docs/a b.md", "docs/a%20b.md"),
                                             ("src/x?y#z.py", "src/x%3Fy%23z.py"), ("", "")])
def test_file_paths_are_escaped(value, expected):
    assert gh._file_path(value) == expected


@pytest.mark.parametrize("bad", ["../secret", "docs/../../user", "/etc/passwd", "a//b", "a/", "a\\b", "a/./b",
                                 "a\nb"])
def test_file_paths_that_could_leave_the_repo_are_refused(bad):
    with pytest.raises(ToolArgumentError):
        gh._file_path(bad)


@pytest.mark.parametrize("bad", ["main..dev", "-x", "/main", "main/", "a b", "x.lock", "a//b", "main?x"])
def test_refs_are_checked(bad):
    with pytest.raises(ToolArgumentError):
        gh._ref(bad)
    assert gh._ref("feature/x-1.2") == "feature/x-1.2"


@pytest.mark.parametrize("kwargs", [{"title": ""}, {"title": "two\nlines"}, {"title": "x" * 257},
                                    {"body": "", "body_required": True}, {"body": "x" * 65_537}])
def test_titles_and_bodies_are_checked(kwargs):
    with pytest.raises(ToolArgumentError):
        gh._check_text(**kwargs)


def test_mentions_are_found_but_not_emails_or_code():
    assert gh._mentions("hi @alice and @my-org/team, mail a@b.com, `@notme`", None) == ["@alice", "@my-org/team"]
