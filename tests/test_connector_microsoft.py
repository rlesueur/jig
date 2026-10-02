"""Microsoft (Outlook calendar and OneDrive), tested without the user's account: the real gate, and real requests
to Microsoft's sign-in service and Graph, which answer a made-up client, code or token with their real errors.
The live test is test_connector_microsoft_live.py."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from jig.config import ConnectorLimits
from jig.connectors import PROVIDERS, microsoft as ms, oauth
from jig.connectors.base import STATUS_NEEDS_RECONNECT, Connectors, Grant
from jig.constants import Mode
from jig.errors import ConnectorAuthError, ConnectorError, ToolArgumentError
from jig.model import ToolCall
from jig.policy.gate import CallContext
from jig.tools.builtin import http_client

from .conftest import audit_kinds, wait_for
from .test_connectors import store  # noqa: F401  (fixture)

FAKE_ID = "00000000-0000-0000-0000-00000000abcd"
CAL_READS = {"outlook_list_calendars", "outlook_list_events", "outlook_get_event"}
CAL_WRITES = {"outlook_create_event", "outlook_update_event", "outlook_cancel_event"}
FILE_READS = {"onedrive_search", "onedrive_list_folder", "onedrive_read_file"}
FILE_WRITES = {"onedrive_upload_file"}
TOOLS = CAL_READS | CAL_WRITES | FILE_READS | FILE_WRITES
LIMITS = type("C", (), {"connectors": {"microsoft": ConnectorLimits(
    allowed_targets=["[Jig test]", "Jig test"], allowed_recipients=["me@example.com"],
    required_prefix="[Jig test]")}})()


def _save(store_, access: str) -> None:
    scopes = list(PROVIDERS[ms.NAME].access_levels[access].scopes)
    store_.save(ms.NAME, Grant(access_token="EwB-x", refresh_token="M.C-x", scopes=scopes), account="me@outlook.com",
                access=access, via="test")


def test_tools_are_declared_safely(jig):
    specs = {t.name: t for t in jig.registry.all() if t.name.startswith(("outlook_", "onedrive_"))}
    assert set(specs) == TOOLS
    for name, spec in specs.items():
        if name in CAL_READS | FILE_READS:
            assert spec.effect.value == "read" and not spec.outbound
        else:
            assert spec.effect.value == "side_effect" and spec.outbound
    for name in CAL_WRITES:
        assert specs[name].human_only and specs[name].variant.value == "scheduling"
        assert specs[name].precheck is ms.calendar_limits and specs[name].resolve is ms.resolve_calendar
    assert specs["onedrive_upload_file"].default_decision.value == "ask"
    assert specs["onedrive_upload_file"].precheck is ms.upload_limits


def test_one_sign_in_offers_both_and_only_with_matching_access(jig):
    def offered(mode: Mode) -> set[str]:
        return {s["function"]["name"] for s in jig.registry.schemas_for_mode(mode)} & TOOLS

    assert offered(Mode.ACTION) == set()
    _save(jig.connections, "read")
    assert offered(Mode.ACTION) == CAL_READS | FILE_READS
    _save(jig.connections, "write")
    assert offered(Mode.ACTION) == TOOLS and offered(Mode.RESEARCH) == CAL_READS | FILE_READS


def test_the_provider_is_a_public_client_with_no_revoke():
    spec = PROVIDERS[ms.NAME]
    assert spec.kind == "oauth" and not spec.needs_client and spec.client_status is ms.client_status
    assert spec.api_hosts == frozenset({"graph.microsoft.com"}) and spec.manage_url == ms.MANAGE_URL
    assert ms.client_from_id(FAKE_ID.upper()) == {"client_id": FAKE_ID, "tenant": "common"}
    assert ms.client_from_id(FAKE_ID, "Contoso.onmicrosoft.com") == {"client_id": FAKE_ID,
                                                                     "tenant": "contoso.onmicrosoft.com"}
    for bad in ("", "abc", FAKE_ID + "0", "123.apps.googleusercontent.com"):
        with pytest.raises(ConnectorError, match=r"Application \(client\) ID"):
            ms.client_from_id(bad)
    for bad in ("../x", "contoso", "a b.com", "https://contoso.com"):
        with pytest.raises(ConnectorError, match="isn't a Microsoft tenant"):
            ms.client_from_id(FAKE_ID, bad)


async def test_disconnecting_says_where_to_remove_access(store):  # noqa: F811
    _save(store, "read")
    async with http_client() as http:
        result = await Connectors(store, http, {}).disconnect(ms.NAME, via="test")
    assert ms.MANAGE_URL in result["at_provider"] and ms.WORK_MANAGE_URL in result["at_provider"]
    assert store.get(ms.NAME) is None


def test_the_built_in_app_says_clearly_when_it_isnt_set_up(store, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ms.apps, "MICROSOFT_CLIENT_ID", "")
    with pytest.raises(ConnectorError, match="built-in Microsoft app isn't set up") as info:
        ms.resolve_client(store)
    assert "--client-id" in str(info.value) and "[connectors.microsoft]" in str(info.value)
    row = {r["provider"]: r for r in store.status()}[ms.NAME]
    assert row["client_configured"] is False and "built-in Microsoft app" in row["client_problem"]


def test_which_app_signs_in(store, monkeypatch):  # noqa: F811
    built_in = "11111111-2222-3333-4444-555555555555"
    monkeypatch.setattr(ms.apps, "MICROSOFT_CLIENT_ID", built_in)
    assert ms.resolve_client(store) == {"client_id": built_in, "tenant": "common", "source": "built-in"}
    store.settings = {"microsoft": ConnectorLimits(client_id=FAKE_ID, tenant="contoso.onmicrosoft.com")}
    assert ms.resolve_client(store) == {"client_id": FAKE_ID, "tenant": "contoso.onmicrosoft.com", "source": "config"}
    other = "99999999-8888-7777-6666-555555555555"
    store.set_client(ms.FAMILY, ms.client_from_id(other), via="test")
    assert ms.resolve_client(store)["client_id"] == other and ms.resolve_client(store)["source"] == "vault"
    store.delete_client(ms.FAMILY, via="test")
    store.settings = {"microsoft": ConnectorLimits(tenant="contoso.onmicrosoft.com")}
    with pytest.raises(ConnectorError, match="sets a tenant but no client_id"):
        ms.resolve_client(store)
    store.settings = {"microsoft": ConnectorLimits(client_id="nope")}
    with pytest.raises(ConnectorError, match=r"\[connectors.microsoft\].*Application \(client\) ID"):
        ms.resolve_client(store)


async def test_connecting_without_an_app_never_opens_a_sign_in_page(store, monkeypatch):  # noqa: F811
    from jig.connectors import connect

    monkeypatch.setattr(ms.apps, "MICROSOFT_CLIENT_ID", "")
    opened: list[str] = []
    async with http_client() as http:
        with pytest.raises(ConnectorError, match="built-in Microsoft app isn't set up"):
            await connect(ms.NAME, access="read", store=store, http=http, open_browser=opened.append, via="test")
    assert opened == [] and store.get(ms.NAME) is None


def test_scopes_are_normalised():
    assert ms.normalise_scopes("https://graph.microsoft.com/User.Read Files.Read", "M.C-x") == \
        ["User.Read", "Files.Read", "offline_access"]
    assert ms.normalise_scopes("User.Read", None) == ["User.Read"]


async def test_sign_in_uses_the_common_authority_with_a_localhost_redirect_and_the_graph_scopes():
    seen: list[str] = []
    task = asyncio.create_task(oauth.authorise(
        authorize_url=ms.authorize_url(ms.DEFAULT_TENANT), client_id=FAKE_ID,
        scopes=list(PROVIDERS[ms.NAME].access_levels["write"].scopes), extra=ms.authorise_params(),
        open_browser=seen.append, label="Microsoft", redirect_host="localhost", timeout=5))
    await wait_for(lambda: seen, timeout=5, what="the sign-in link")
    url = httpx.URL(seen[0])
    params = dict(url.params)
    assert url.host == "login.microsoftonline.com" and url.path == "/common/oauth2/v2.0/authorize"
    assert params["scope"].split() == ["User.Read", "offline_access", "Calendars.ReadWrite", "Files.ReadWrite"]
    assert params["redirect_uri"].startswith("http://localhost:") and params["code_challenge_method"] == "S256"
    assert "client_secret" not in params
    async with http_client() as http:
        r = await http.get(seen[0], follow_redirects=False)
    assert r.status_code == 200 and "login.microsoftonline.com" in str(r.url)
    with pytest.raises(ConnectorError, match="no answer"):
        await task


async def test_the_real_token_endpoint_refuses_a_made_up_code():
    async with http_client() as http:
        with pytest.raises(ConnectorError, match=r"HTTP 400, invalid_grant.*nothing was connected"):
            await ms.exchange_code(http, {"client_id": FAKE_ID, "tenant": "common"}, code="not-a-code",
                                   redirect_uri="http://localhost:1/", verifier="a" * 50,
                                   scopes=["User.Read", "offline_access"])


@pytest.mark.skipif(not ms.apps.MICROSOFT_CLIENT_ID, reason="Jig's Microsoft app isn't registered yet: set "
                    "MICROSOFT_CLIENT_ID in jig/connectors/apps.py (docs/connectors-setup.md)")
async def test_the_built_in_app_is_registered_for_personal_and_work_accounts():
    # A made-up code: Microsoft says the code is bad (invalid_grant) only once it has accepted the app itself,
    # as a public client for both kinds of account, at the common authority.
    async with http_client() as http:
        r = await http.post(ms.token_url(ms.DEFAULT_TENANT), data={
            "client_id": ms.apps.MICROSOFT_CLIENT_ID, "grant_type": "authorization_code", "code": "not-a-code",
            "redirect_uri": "http://localhost:1/", "code_verifier": "a" * 50, "scope": "User.Read"}, timeout=30)
    body = r.json()
    codes = set(body.get("error_codes") or [])
    assert not codes & {700016, 9002331, 9002332, 7000218}, body.get("error_description")
    assert body.get("error") == "invalid_grant", body.get("error_description")


async def test_a_refused_refresh_needs_a_reconnect(store):  # noqa: F811
    async with http_client() as http:
        with pytest.raises(ConnectorAuthError, match="no longer accepts"):
            await ms.refresh(http, store.vault, Grant(access_token="EwB-x", refresh_token="M.C-not-real",
                                                      scopes=["User.Read", "offline_access"],
                                                      extra={"client_id": FAKE_ID, "tenant": "common"}))
        with pytest.raises(ConnectorAuthError, match="doesn't record which app"):
            await ms.refresh(http, store.vault, Grant(access_token="EwB-x", refresh_token="M.C-not-real"))


async def test_real_graph_rejects_a_bad_token(store):  # noqa: F811
    store.save(ms.NAME, Grant(access_token="EwB-jig-test-not-real", scopes=["User.Read"]), account="me@outlook.com",
               access="read", via="test")
    async with http_client() as http:
        with pytest.raises(ConnectorAuthError, match="HTTP 401") as info:
            await Connectors(store, http, {}).request(ms.NAME, "GET", f"{ms.API}/me")
    assert "jig-test-not-real" not in str(info.value)
    assert store.get(ms.NAME)["status"] == STATUS_NEEDS_RECONNECT


async def test_the_token_only_goes_to_graph(store):  # noqa: F811
    store.save(ms.NAME, Grant(access_token="EwB-x", scopes=["User.Read"]), account="me@outlook.com", access="read",
               via="test")
    async with http_client() as http:
        for url in ("https://graph.microsoft.com.evil.example/v1.0/me", "http://graph.microsoft.com/v1.0/me",
                    "https://public.bn.files.1drv.com/x"):
            with pytest.raises(ConnectorError, match="may only go to"):
                await Connectors(store, http, {}).request(ms.NAME, "GET", url)


async def test_read_only_mode_refuses_writes(jig):
    calls = [("outlook_create_event", {"subject": "x", "start": "2026-10-04", "end": "2026-10-05"}),
             ("outlook_cancel_event", {"event_id": "AQMkADAwATM0MDAAMS1"}),
             ("onedrive_upload_file", {"name": "x.txt", "content": "y"})]
    for i, (name, args) in enumerate(calls):
        outcome = await jig.executor.execute(ToolCall(id=f"m{i}", name=name, arguments_raw=json.dumps(args)),
                                             CallContext(run_id=f"r_m{i}", task_id=None, mode=Mode.RESEARCH,
                                                         intent="look only"))
        assert outcome.error_type == "ModeViolation", name


async def test_a_write_without_a_connection_fails_before_review(jig):
    call = ToolCall(id="m9", name="outlook_create_event", arguments_raw=json.dumps(
        {"subject": "[Jig test] x", "start": "2026-10-04T10:00:00+01:00", "end": "2026-10-04T11:00:00+01:00"}))
    outcome = await jig.executor.execute(call, CallContext(run_id="r_m9", task_id=None, mode=Mode.ACTION,
                                                           intent="add a test event"))
    assert outcome.error_type == "ToolError" and "not connected" in outcome.error
    assert "sentinel.verdict" not in audit_kinds(jig, run_id="r_m9")


@pytest.mark.parametrize("args, resolved, why", [
    ({"subject": "[Jig test] a"}, {"calendar_id": "AQMk-main", "calendar": "Calendar"}, "allowed_targets"),
    ({"subject": "Lunch"}, {"calendar_id": "AQMk-test", "calendar": "[Jig test]"}, "required_prefix"),
    ({"event_id": "AQMk-e"}, {"calendar": "[Jig test]", "event_summary": "Dentist"}, "required_prefix"),
    ({"subject": "[Jig test] a", "attendees": ["boss@example.com"]}, {"calendar": "[Jig test]"},
     "allowed_recipients"),
])
def test_calendar_limits(args, resolved, why):
    assert why in ms.calendar_limits(LIMITS, args, resolved)


def test_calendar_limits_allow_a_test_event_in_the_test_calendar():
    assert ms.calendar_limits(LIMITS, {"calendar_id": "AQMk-test", "subject": "[Jig test] a",
                                       "attendees": ["me@example.com"]},
                              {"calendar_id": "AQMk-test", "calendar": "[Jig test]"}) is None


@pytest.mark.parametrize("args, why", [
    ({"folder": "", "name": "[Jig test] a.txt"}, "allowed_targets"),
    ({"folder": "Documents", "name": "[Jig test] a.txt"}, "allowed_targets"),
    ({"folder": "Jig test", "name": "notes.txt"}, "required_prefix"),
])
def test_upload_limits(args, why):
    assert why in ms.upload_limits(LIMITS, args, None)


def test_upload_limits_allow_the_test_folder():
    assert ms.upload_limits(LIMITS, {"folder": "/Jig test/", "name": "[Jig test] a.txt"}, None) is None


@pytest.mark.parametrize("bad", ["..", "a/../b", "a//b", "C:/x", "a?b", "a#b", "a%2fb", "a /b", "x\ny"])
def test_folder_paths_are_checked(bad):
    with pytest.raises(ToolArgumentError):
        ms.folder_path(bad)


@pytest.mark.parametrize("bad", ["", "a/b", "a\\b", "..", "x.", "a:b", "a" * 201])
def test_file_names_are_checked(bad):
    with pytest.raises(ToolArgumentError):
        ms.file_name(bad)


def test_paths_are_escaped():
    assert ms._drive_path("") == "/me/drive/root"
    assert ms._drive_path("Jig test/[Jig test] a.txt") == "/me/drive/root:/Jig%20test/%5BJig%20test%5D%20a.txt:"
    assert ms._cal_base("default") == "/me/calendar"
    assert ms._cal_base("AQMk/ab+c=") == "/me/calendars/AQMk%2Fab%2Bc%3D"
    with pytest.raises(ToolArgumentError):
        ms._cal_base("../../me/messages")


def test_times():
    assert ms._when("2026-10-04T15:00:00+01:00", "start", "") == (
        {"dateTime": "2026-10-04T14:00:00", "timeZone": "UTC"}, False)
    assert ms._when("2026-10-04T15:00:00", "start", "Europe/London") == (
        {"dateTime": "2026-10-04T15:00:00", "timeZone": "Europe/London"}, False)
    assert ms._when("2026-10-04", "start", "") == ({"dateTime": "2026-10-04T00:00:00", "timeZone": "UTC"}, True)
    for bad in ("2026-13-01", "tomorrow", "2026-10-04T15:00:00"):
        with pytest.raises(ToolArgumentError):
            ms._when(bad, "start", "")
    assert ms._parent_path("/drive/root:/Jig test") == "Jig test" and ms._parent_path("/drive/root:") == ""
