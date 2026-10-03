"""Connecting accounts from Settings > Connections: the local web API, against the providers' real services
(which answer made-up tokens and apps with their real refusals). Whatever is typed in goes only to the vault:
it is never echoed back, put in an error or written to the audit log."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from jig.config import ConnectorLimits
from jig.connectors import github as gh
from jig.connectors import microsoft as ms

MADE_UP_PAT = "github_pat_11JIGWEBTEST000000000_notarealtokenjustfortestingjigsconnector000000000000"
MADE_UP_DISCORD = "jigwebtestnotarealdiscordbottoken"
GOOGLE_FILE = {"installed": {"client_id": "000000000000-jigwebtest.apps.googleusercontent.com",
                             "client_secret": "GOCSPX-jigwebtestnotarealsecret00",
                             "redirect_uris": ["http://localhost"]}}
MS_ID = "00000000-0000-0000-0000-00000000abcd"


@pytest.fixture
def client(config):
    from jig.api import create_app

    app = create_app(config)
    token = app.state.auth.tokens.get()
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as c:
        c.jig = app.state.jig
        yield c


def _audit_text(c) -> str:
    return json.dumps(c.jig.audit.query(limit=500))


def test_every_connector_has_plain_steps_and_a_way_in(client):
    rows = {r["provider"]: r for r in client.get("/connections").json()}
    assert set(rows) == {"gmail", "google-calendar", "google-drive", "microsoft", "github", "discord", "whatsapp"}
    for name, r in rows.items():
        assert r["guide"]["summary"] and r["guide"]["steps"], name
        assert r["methods"], name
        for step in r["guide"]["steps"] + r["guide"].get("setup", []):
            assert all(link["url"].startswith("https://") for link in step["links"]), (name, step)
    assert rows["gmail"]["guide"]["setup"] and "In production" in json.dumps(rows["gmail"]["guide"])
    assert rows["github"]["methods"] == ["device", "token"]
    assert [i["name"] for i in rows["whatsapp"]["inputs"]] == ["token", "phone_number_id", "waba_id"]


def test_a_typed_token_is_checked_with_the_provider_and_never_echoed(client):
    r = client.post("/connections/github/connect",
                    json={"confirm": True, "method": "token", "access": "read", "values": {"token": MADE_UP_PAT}})
    assert r.status_code == 400 and "GitHub rejected the token" in r.json()["error"]
    assert MADE_UP_PAT not in r.text and MADE_UP_PAT not in _audit_text(client)
    r = client.post("/connections/discord/connect",
                    json={"confirm": True, "method": "token", "values": {"token": MADE_UP_DISCORD}})
    assert r.status_code == 400 and "Discord rejected the bot token" in r.json()["error"]
    assert MADE_UP_DISCORD not in r.text and MADE_UP_DISCORD not in _audit_text(client)
    assert client.jig.connections.get("github") is None and client.jig.connections.get("discord") is None


def test_malformed_requests_never_echo_what_was_sent(client):
    secret = "github_pat_11JIGECHOTEST_this_must_never_come_back"
    for body in ({"confirm": True, "access": {"x": secret}},
                 {"confirm": True, "method": "token", "values": [secret]},
                 {"confirm": True, "method": "token", "values": {"token": [secret]}},
                 {"confirm": True, "method": "token", "values": {"nonsense": secret}},
                 {"confirm": secret}):
        r = client.post("/connections/github/connect", json=body)
        assert r.status_code in (400, 422) and secret not in r.text, (body, r.text)
    r = client.post("/connections/google/client", json={"confirm": True, "client_json": {"a": secret}})
    assert r.status_code == 400 and secret not in r.text


def test_connecting_needs_confirmation_and_a_known_method(client):
    assert client.post("/connections/github/connect", json={"method": "token"}).status_code == 400
    r = client.post("/connections/discord/connect", json={"confirm": True, "method": "device"})
    assert r.status_code == 400 and "connects by token" in r.json()["error"]
    r = client.post("/connections/github/connect", json={"confirm": True, "access": "admin"})
    assert r.status_code == 400 and "access must be one of" in r.json()["error"]


def test_the_google_client_file_goes_to_the_vault_and_is_never_shown(client):
    secret = GOOGLE_FILE["installed"]["client_secret"]
    r = client.post("/connections/google/client", json={"confirm": True, "client_json": json.dumps({"web": {}})})
    assert r.status_code == 400 and "not a Desktop app client" in r.json()["error"]
    r = client.post("/connections/google/client", json={"confirm": True, "client_json": "not json"})
    assert r.status_code == 400 and "isn't the client file" in r.json()["error"]
    r = client.post("/connections/google/client", json={"confirm": True, "client_json": json.dumps(GOOGLE_FILE)})
    assert r.status_code == 200 and r.json()["stored"] and secret not in r.text
    rows = {x["provider"]: x for x in client.get("/connections").json()}
    assert rows["gmail"]["client_configured"] and rows["google-drive"]["client_configured"]
    assert secret not in json.dumps(rows) and secret not in _audit_text(client)
    assert client.jig.connections.client("google")["client_secret"] == secret
    assert client.delete("/connections/google/client").json()["deleted"] is True
    assert not client.jig.connections.has_client("google")


def test_your_own_microsoft_app_can_be_set_and_removed(client):
    r = client.post("/connections/microsoft/client", json={"confirm": True, "client_id": "nope"})
    assert r.status_code == 400 and "Application (client) ID" in r.json()["error"]
    r = client.post("/connections/microsoft/client",
                    json={"confirm": True, "client_id": MS_ID, "tenant": "contoso.onmicrosoft.com"})
    assert r.status_code == 200
    row = {x["provider"]: x for x in client.get("/connections").json()}["microsoft"]
    assert row["client_configured"] and row["client_source"] == "vault"
    assert ms.resolve_client(client.jig.connections)["tenant"] == "contoso.onmicrosoft.com"
    assert client.delete("/connections/microsoft/client").json()["deleted"] is True
    assert client.post("/connections/discord/client", json={"confirm": True}).status_code == 404


def test_microsoft_says_when_its_built_in_app_isnt_set_up(client, monkeypatch):
    monkeypatch.setattr(ms.apps, "MICROSOFT_CLIENT_ID", "")
    r = client.post("/connections/microsoft/connect", json={"confirm": True})
    assert r.status_code == 409 and "built-in Microsoft app isn't set up" in r.json()["error"]


def test_a_device_sign_in_with_a_made_up_app_fails_at_github(client):
    client.jig.connections.settings = {"github": ConnectorLimits(client_id="Iv23liJigWebTestNotReal",
                                                                 app_slug="jig-web-test-not-real")}
    r = client.post("/connections/github/connect", json={"confirm": True})
    assert r.status_code == 400 and "doesn't know a GitHub App" in r.json()["error"]
    attempt = {x["provider"]: x for x in client.get("/connections").json()}["github"]["attempt"]
    assert attempt["status"] == "failed" and attempt["method"] == "device"
    assert gh.install_url("jig-web-test-not-real") in json.dumps(client.get("/connections").json())
