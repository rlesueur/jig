"""Google Drive, tested without the user's Google account: the real gate, and real requests to the Drive API
and Google's consent page, which answer a made-up token or client with their real errors. The live test is
test_connector_google_drive_live.py."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from jig.config import ConnectorLimits
from jig.connectors import PROVIDERS, google, google_drive as gdrive, oauth
from jig.connectors.base import STATUS_NEEDS_RECONNECT, Connectors, Grant
from jig.constants import Mode
from jig.errors import ConnectorAuthError, ConnectorError, ToolArgumentError
from jig.model import ToolCall
from jig.policy.gate import CallContext
from jig.tools.builtin import http_client

from .conftest import audit_kinds, wait_for
from .test_connectors import FAKE_CLIENT, store  # noqa: F401  (fixture)

TOOLS = {"gdrive_search", "gdrive_read_file", "gdrive_create_file", "gdrive_update_file"}
READS = {"gdrive_search", "gdrive_read_file"}
LIMITS = type("C", (), {"connectors": {"google-drive": ConnectorLimits(allowed_targets=["root"],
                                                                         required_prefix="[Jig test]")}})()


def test_tools_are_declared_safely(jig):
    specs = {t.name: t for t in jig.registry.all() if t.name.startswith("gdrive_")}
    assert set(specs) == TOOLS
    for name, spec in specs.items():
        if name in READS:
            assert spec.effect.value == "read" and not spec.outbound
        else:
            assert spec.effect.value == "side_effect" and spec.outbound
            assert spec.default_decision.value == "ask" and spec.precheck is gdrive.limits_problem
    assert not any(w in " ".join(specs) for w in ("delete", "share", "trash")), "no delete or share tools"


def test_tools_are_offered_only_with_the_matching_access(jig):
    def offered(mode: Mode) -> set[str]:
        return {s["function"]["name"] for s in jig.registry.schemas_for_mode(mode)} & TOOLS

    assert offered(Mode.ACTION) == set()
    jig.connections.save(gdrive.NAME, Grant(access_token="ya29.x", scopes=[gdrive.S_READ]), account="me@example.com",
                         access="read", via="test")
    assert offered(Mode.ACTION) == READS
    jig.connections.save(gdrive.NAME, Grant(access_token="ya29.x", scopes=[gdrive.S_READ, gdrive.S_FILE]),
                         account="me@example.com", access="write", via="test")
    assert offered(Mode.ACTION) == TOOLS and offered(Mode.RESEARCH) == READS


async def test_the_real_drive_api_rejects_a_bad_token(store):  # noqa: F811
    store.save(gdrive.NAME, Grant(access_token="ya29.jig-test-not-real", scopes=[gdrive.S_READ]),
               account="me@example.com", access="read", via="test")
    async with http_client() as http:
        with pytest.raises(ConnectorAuthError, match="HTTP 401") as info:
            await Connectors(store, http, {}).request(gdrive.NAME, "GET", f"{gdrive.API}/files")
    assert "jig-test-not-real" not in str(info.value)
    assert store.get(gdrive.NAME)["status"] == STATUS_NEEDS_RECONNECT


async def test_consent_page_asks_only_for_the_drive_scopes():
    seen: list[str] = []
    task = asyncio.create_task(oauth.authorise(
        authorize_url=google.AUTHORIZE_URL, client_id=FAKE_CLIENT["client_id"],
        scopes=list(PROVIDERS[gdrive.NAME].access_levels["write"].scopes), extra=google.authorise_params(),
        open_browser=seen.append, label="Google", timeout=5))
    await wait_for(lambda: seen, timeout=5, what="the sign-in link")
    assert dict(httpx.URL(seen[0]).params)["scope"].split() == [gdrive.S_READ, gdrive.S_FILE]
    with pytest.raises(ConnectorError, match="no answer"):
        await task


async def test_read_only_mode_refuses_writes(jig):
    for i, (name, args) in enumerate([("gdrive_create_file", {"name": "x", "content": "y"}),
                                      ("gdrive_update_file", {"file_id": "1AbCdEfGhIjK", "content": "y"})]):
        outcome = await jig.executor.execute(ToolCall(id=f"d{i}", name=name, arguments_raw=json.dumps(args)),
                                             CallContext(run_id=f"r_d{i}", task_id=None, mode=Mode.RESEARCH,
                                                         intent="look only"))
        assert outcome.error_type == "ModeViolation", name


async def test_a_write_without_a_connection_fails_before_review(jig):
    call = ToolCall(id="d9", name="gdrive_create_file", arguments_raw=json.dumps(
        {"name": "[Jig test] x.txt", "content": "y", "folder_id": "1AbCdEfGhIjKlMn"}))
    outcome = await jig.executor.execute(call, CallContext(run_id="r_d9", task_id=None, mode=Mode.ACTION,
                                                           intent="save a test file"))
    assert outcome.error_type == "ToolError" and "not connected" in outcome.error
    assert "sentinel.verdict" not in audit_kinds(jig, run_id="r_d9")


@pytest.mark.parametrize("args, resolved, why", [
    ({"name": "[Jig test] a.txt", "folder_id": "1AbCdEfGhIjKlMn"}, {"folder_id": "1AbCdEfGhIjKlMn", "folder": "Work"},
     "allowed_targets"),
    ({"name": "notes.txt", "folder_id": "root"}, {"folder_id": "root"}, "required_prefix"),
    ({"file_id": "1AbCdEfGhIjKlMn"}, {"file": "Tax return.txt", "folder_ids": ["root"]}, "required_prefix"),
])
def test_limits_keep_writes_to_test_files(args, resolved, why):
    assert why in gdrive.limits_problem(LIMITS, args, resolved)


def test_limits_allow_a_test_file_in_my_drive():
    assert gdrive.limits_problem(LIMITS, {"name": "[Jig test] a.txt"}, {"folder_id": "root"}) is None
    assert gdrive.limits_problem(LIMITS, {"file_id": "1AbCdEfGhIjKlMn"},
                                 {"file": "[Jig test] a.txt", "folder_ids": ["root"]}) is None


def test_search_strings_are_escaped():
    assert gdrive._quote("it's a \\ test") == "'it\\'s a \\\\ test'"


@pytest.mark.parametrize("bad", ["", "a/b", "x\ny", "a" * 201])
def test_file_names_are_checked(bad):
    with pytest.raises(ToolArgumentError):
        gdrive._name(bad)


@pytest.mark.parametrize("bad", ["../etc", "abc", "1AbC dEf GhI", "1AbCdEf?x=1"])
def test_ids_are_checked(bad):
    with pytest.raises(ToolArgumentError):
        gdrive._file_id(bad)


def test_uploads_are_well_formed_multipart():
    body, ctype = gdrive._multipart({"name": "[Jig test] a.txt", "parents": ["root"]}, "Café", "text/plain")
    boundary = ctype.split("boundary=")[1]
    parts = body.decode().split(f"--{boundary}")
    assert json.loads(parts[1].split("\r\n\r\n", 1)[1]) == {"name": "[Jig test] a.txt", "parents": ["root"]}
    assert parts[2].split("\r\n\r\n", 1)[1].rstrip("\r\n") == "Café" and parts[3].startswith("--")
