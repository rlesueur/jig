"""API token, browser sessions and the authentication middleware, against the real app and model server."""

from __future__ import annotations

import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from jig.api import create_app
from jig.auth import SESSION_COOKIE, TokenFileError, TokenStore, check_private
from jig.config import load_config

ORIGIN = "http://testserver"


@pytest.fixture(scope="module")
def module_config(tmp_path_factory):
    base = tmp_path_factory.mktemp("auth")
    return load_config(data_dir=base / "data", sandbox_dir=base / "sandbox")


@pytest.fixture(scope="module")
def app(module_config):
    return create_app(module_config)


@pytest.fixture(scope="module")
def _client(app):
    # One real runtime (health check and capability probes included) for the whole module.
    with TestClient(app) as client:
        yield client


@pytest.fixture
def anon(_client):
    _client.cookies.clear()
    return _client


@pytest.fixture
def config(module_config):
    return module_config


def _bearer(app) -> dict[str, str]:
    return {"Authorization": f"Bearer {app.state.auth.tokens.get()}"}


def test_token_is_created_private(config, app):
    path = config.data_dir / "api-token"
    assert path.is_file()
    check_private(path)  # raises unless only the current user can read it
    assert len(app.state.auth.tokens.get()) >= 40


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACL check")
def test_permissive_token_file_fails_loudly(tmp_path):
    store = TokenStore(tmp_path)
    store.ensure()
    subprocess.run(["icacls", str(store.path), "/grant", "*S-1-1-0:R"], check=True, capture_output=True)  # Everyone
    store._cache = None
    with pytest.raises(TokenFileError, match="not private"):
        store.get()
    store.rotate()  # recreates the file with a private ACL
    check_private(store.path)


def test_health_is_public_and_minimal(anon):
    r = anon.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/status", None),
    ("GET", "/state", None),
    ("GET", "/tasks", None),
    ("POST", "/goals", {"description": "should never be created"}),
    ("POST", "/tasks/t_missing/pause", None),
    ("POST", "/agent/pause", None),
    ("GET", "/approvals", None),
    ("POST", "/approvals/ap_missing", {"approve": True}),
    ("GET", "/memory", None),
    ("DELETE", "/memory/1", None),
    ("GET", "/rules", None),
    ("GET", "/audit", None),
    ("GET", "/vault", None),
    ("POST", "/chat", {"message": "hello"}),
    ("GET", "/events/sse", None),
    ("GET", "/events/recent", None),
    ("POST", "/auth/login-code", None),
    ("GET", "/openapi.json", None),
])
def test_unauthenticated_requests_are_rejected(anon, method, path, body):
    r = anon.request(method, path, json=body)
    assert r.status_code == 401, (path, r.status_code, r.text)
    assert r.headers["www-authenticate"].startswith("Bearer")
    assert "authentication required" in r.json()["error"]


def test_wrong_token_is_rejected(anon):
    r = anon.get("/approvals", headers={"Authorization": "Bearer not-the-token-not-the-token-not-the-token"})
    assert r.status_code == 401


def test_event_stream_websocket_requires_auth(app, anon):
    with pytest.raises(WebSocketDisconnect) as exc:
        with anon.websocket_connect("/events"):
            pass
    assert exc.value.code == 1008
    with anon.websocket_connect("/events", headers=_bearer(app)) as ws:
        snapshot = ws.receive_json()
    assert snapshot["snapshot"] is True and snapshot["data"]["state"] == "idle"


def test_bearer_token_works_and_approvals_need_it(app, anon):
    h = _bearer(app)
    assert anon.get("/approvals", headers=h).json() == []
    r = anon.post("/approvals/ap_missing", json={"approve": True}, headers=h)
    assert r.status_code == 404  # authenticated, so it reaches the handler
    assert anon.get("/auth/session", headers=h).json() == {"authenticated": True, "via": "bearer"}


def test_browser_session_with_one_time_login_code(app, anon):
    code = anon.post("/auth/login-code", headers=_bearer(app)).json()["code"]
    assert anon.get("/auth/session").json()["authenticated"] is False
    r = anon.post("/auth/session", json={"code": code})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert f"{SESSION_COOKIE}=" in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert app.state.auth.tokens.get() not in cookie, "the cookie must not carry the token itself"
    # The cookie now authenticates reads...
    assert anon.get("/auth/session").json() == {"authenticated": True, "via": "cookie"}
    assert anon.get("/memory").status_code == 200
    # ...and same-origin writes, but never cross-origin ones.
    assert anon.post("/memory", json={"content": "cookie write"}, headers={"Origin": ORIGIN}).status_code == 201
    assert anon.post("/memory", json={"content": "csrf"}, headers={"Origin": "http://evil.example"}).status_code == 403
    assert anon.post("/memory", json={"content": "no origin"}).status_code == 403
    # The code works once only.
    anon.cookies.clear()
    assert anon.post("/auth/session", json={"code": code}).status_code == 401


def test_websocket_with_cookie_checks_origin(app, anon):
    assert anon.post("/auth/session", json={"token": app.state.auth.tokens.get()}).status_code == 200
    with pytest.raises(WebSocketDisconnect) as exc:
        with anon.websocket_connect("/events", headers={"Origin": "http://evil.example"}):
            pass
    assert exc.value.code == 1008
    with anon.websocket_connect("/events", headers={"Origin": ORIGIN}) as ws:
        assert ws.receive_json()["snapshot"] is True


def test_rotating_the_token_signs_everyone_out(app, anon):
    old = app.state.auth.tokens.get()
    assert anon.post("/auth/session", json={"token": old}).status_code == 200
    assert anon.get("/auth/session").json()["authenticated"] is True
    TokenStore(app.state.auth.tokens.path.parent).rotate()
    assert anon.get("/auth/session").json()["authenticated"] is False
    assert anon.get("/memory", headers={"Authorization": f"Bearer {old}"}).status_code == 401
    assert anon.get("/memory", headers=_bearer(app)).status_code == 200


def test_web_ui_is_served_with_a_strict_policy(anon):
    page = anon.get("/")
    assert page.status_code == 200 and "<jig-avatar" in page.text
    assert "script-src 'self'" in page.headers["content-security-policy"]
    js = anon.get("/avatar/jig-avatar.js")
    assert js.status_code == 200 and "customElements.define('jig-avatar'" in js.text
    assert anon.get("/web/app.js").status_code == 200
