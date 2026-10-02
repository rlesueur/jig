"""Guided set-up in Settings > Connections, and help with it in chat. Each step's check runs against the
provider's real service (made-up apps get their real refusals), and the agent's help tool is read-only: it gives
the same steps and the connection's state, and never takes or shows a secret. Against the real model."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from jig.connectors import walkthrough as wt
from jig.constants import Effect, EventType, Mode
from jig.errors import JigError
from jig.policy.gate import CallContext

GOOGLE_FILE = {"installed": {"client_id": "000000000000-jigwalktest.apps.googleusercontent.com",
                             "client_secret": "GOCSPX-jigwalktestnotarealsecret0",
                             "project_id": "jig-walk-test", "redirect_uris": ["http://localhost"]}}
MIDJOURNEY_APP = "936929561302675456"  # a real, public Discord application
MADE_UP_APP = "100000000000000001"


@pytest.fixture
def client(config):
    from jig.api import create_app

    app = create_app(config)
    token = app.state.auth.tokens.get()
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as c:
        c.jig = app.state.jig
        yield c


def _check(client, name: str, check: str, values=None, confirm=True):
    return client.post(f"/connections/{name}/walkthrough/{check}", json={"confirm": confirm, "values": values})


def test_every_connector_has_a_walkthrough_with_safe_links(client):
    rows = {r["provider"]: r for r in client.get("/connections").json()}
    for name, r in rows.items():
        steps = r["walkthrough"]
        assert steps and len({s["id"] for s in steps}) == len(steps), name
        for s in steps:
            assert s["title"] and s["say"], (name, s["id"])
            assert all(link["url"].startswith("https://") for link in s["links"]), (name, s)
    gmail = [s["id"] for s in rows["gmail"]["walkthrough"]]
    assert gmail == ["google-intro", "google-project", "google-apis", "google-consent", "google-publish",
                     "google-client", "google-warning", "connect", "try"]
    warning = next(s for s in rows["gmail"]["walkthrough"] if s["id"] == "google-warning")
    assert "hasn't verified" in json.dumps(warning) or "unverified" in json.dumps(warning).lower()
    assert [s["action"]["type"] for s in rows["microsoft"]["walkthrough"]] == ["connect", "try"]
    assert [s["action"]["type"] for s in rows["slack"]["walkthrough"]][-1] == "pick"
    assert "GOCSPX" not in json.dumps(rows)


def test_checks_need_confirmation_and_a_known_check(client):
    assert _check(client, "discord", "discord_app", {"application_id": MIDJOURNEY_APP}, confirm=None).status_code == 400
    r = _check(client, "discord", "nonsense")
    assert r.status_code == 400 and "no check called" in r.json()["error"]
    assert _check(client, "nope", "try").status_code == 404
    r = _check(client, "slack", "google_client")
    assert r.status_code == 400 and "only the Google connectors" in r.json()["error"]


def test_the_google_client_check_asks_google_itself(client):
    r = _check(client, "gmail", "google_client")
    assert r.status_code == 200 and r.json()["ok"] is False and "client file yet" in r.json()["say"]
    stored = client.post("/connections/google/client",
                         json={"confirm": True, "client_json": json.dumps(GOOGLE_FILE)})
    assert stored.status_code == 200
    r = _check(client, "google-drive", "google_client")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is False and "doesn't recognise this app" in r.json()["say"]
    assert GOOGLE_FILE["installed"]["client_secret"] not in r.text
    assert client.jig.connections.client("google")["project_id"] == "jig-walk-test"
    kinds = [e["kind"] for e in client.jig.audit.query(limit=50)]
    assert "connector.walkthrough_check" in kinds


def test_the_discord_check_finds_a_real_app_and_refuses_a_made_up_one(client):
    r = _check(client, "discord", "discord_app", {"application_id": MIDJOURNEY_APP})
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    assert "Midjourney" in r.json()["say"] and MIDJOURNEY_APP in r.json()["invite_url"]
    r = _check(client, "discord", "discord_app", {"application_id": MADE_UP_APP})
    assert r.status_code == 200 and r.json()["ok"] is False and "doesn't know an app" in r.json()["say"]
    r = _check(client, "discord", "discord_app", {"application_id": "not a number"})
    assert r.status_code == 400


def test_the_matrix_check_finds_a_real_homeserver(client):
    r = _check(client, "matrix", "matrix_server", {"homeserver": "matrix.org"})
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    assert r.json()["homeserver"].startswith("https://")
    r = _check(client, "matrix", "matrix_server", {"homeserver": "jig-walk-test.invalid"})
    assert r.status_code == 400
    r = _check(client, "matrix", "matrix_server", {"homeserver": "http://matrix.org"})
    assert r.status_code == 400 and "https://" in r.json()["error"]


def test_the_signal_check_says_clearly_when_signal_cli_isnt_there(client, tmp_path):
    r = _check(client, "signal", "signal_cli", {"signal_cli": str(tmp_path / "signal-cli.bat")})
    assert r.status_code == 400 and r.json()["error"]
    r = _check(client, "signal", "signal_cli", {})
    assert r.status_code == 400 and "type the path" in r.json()["error"]


def test_trying_an_account_that_isnt_connected_fails_clearly(client):
    r = _check(client, "gmail", "try")
    assert r.status_code == 400 and r.json()["error"]


def _ctx(jig):
    return jig._tool_context(CallContext(run_id="r_walk_test", task_id=None, mode=Mode.RESEARCH,
                                         intent="help connect an account"))


async def test_the_help_tool_is_read_only_and_never_takes_secrets(jig):
    spec = jig.registry.get("connection_help")
    assert spec.effect is Effect.READ
    help_fn = spec.fn
    every = await help_fn(_ctx(jig))
    assert {a["provider"] for a in every["accounts"]} >= {"gmail", "microsoft", "github", "slack"}
    gmail = await help_fn(_ctx(jig), provider="Gmail")
    assert gmail["provider"] == "gmail" and gmail["now"]["status"] == "not_connected"
    assert gmail["settings_link"] == "#settings/connections/gmail" and gmail["rules"] == wt.SECRET_RULE
    titles = [s["title"] for s in gmail["steps"]]
    assert len(titles) == 9 and gmail["now"]["google_app_set_up"] is False
    client_step = next(s for s in gmail["steps"] if "typed_where" in s)
    assert "never in the chat" in client_step["typed_where"]
    assert (await help_fn(_ctx(jig), provider="outlook"))["provider"] == "microsoft"
    with pytest.raises(JigError, match="no account called"):
        await help_fn(_ctx(jig), provider="myspace")
    jig.connections.set_client("google", {"client_id": GOOGLE_FILE["installed"]["client_id"],
                                          "client_secret": GOOGLE_FILE["installed"]["client_secret"]}, via="test")
    drive = await help_fn(_ctx(jig), provider="drive")
    assert drive["now"]["google_app_set_up"] is True and len(drive["steps"]) == 2
    assert GOOGLE_FILE["installed"]["client_secret"] not in json.dumps(drive)


async def test_asking_for_help_in_chat_uses_the_help_tool(jig, events):
    items = [item async for item in jig.chat("Help me connect my Gmail to you, please.")]
    assert items[-1]["type"] == "done", items[-1]
    used = [e.data for e in events if e.type == EventType.TOOL_END and e.data.get("tool") == "connection_help"]
    assert used and all(u["ok"] for u in used), [(e.type, e.data.get("tool")) for e in events]
