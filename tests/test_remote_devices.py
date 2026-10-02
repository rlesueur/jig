"""Device pairing, remote-access request checks and Tailscale, against the real app, a real `jig serve`
process and the real model server. Nothing is mocked.

* Pairing end to end: one-time code, single use, expiry, the wrong-code lock-out, per-device revoke,
  device expiry, and rotating the master token revoking every device (API and CLI).
* Tailnet requests in container mode, where `tailscale serve` on the host forwards to Jig and the
  tailnet name is configured: Secure cookies, the origin allow-list (HTTP and WebSocket), and the
  master token / browser sign-in refused over the tailnet.
* On the host, a request that *claims* to come through Tailscale (forged Host and Tailscale-User-*
  headers) over a real local TCP connection is refused, because the connection is not tailscaled's.
* A real `tailscale serve` round trip, only if Tailscale is installed and signed in on this machine.
"""

from __future__ import annotations

import json
import socket
import sqlite3
import subprocess
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from jig.api import create_app
from jig.auth import TokenStore
from jig.config import RemoteConfig, load_config
from jig.devices import DEVICE_COOKIE, DeviceStore, PairingError, revoke_all_offline
from jig.remote import (RemoteAccess, funnels_to_port, serve_entry, tailscale_info, tcp_owner_pid,
                        verify_tailscale_peer)

from .server_helpers import config_path, free_port, kill, start_jig, token, wait_health, wait_stopped

LOCAL = "http://testserver"
TAILNET_HOST = "jig-test.example-tailnet.ts.net"
TAILNET = f"https://{TAILNET_HOST}"


def _bearer(app) -> dict[str, str]:
    return {"Authorization": f"Bearer {app.state.auth.tokens.get()}"}


def _audit(app, kind: str) -> list[dict]:
    return app.state.jig.audit.query(kind=kind, limit=500)


# Host mode: pairing over a local connection ---------------------------------------------------------

@pytest.fixture(scope="module")
def host_app(tmp_path_factory):
    base = tmp_path_factory.mktemp("pairing")
    app = create_app(load_config(data_dir=base / "data", sandbox_dir=base / "sandbox"))
    with TestClient(app) as client:
        yield app, client


@pytest.fixture
def host(host_app):
    app, client = host_app
    client.cookies.clear()
    return app, client


def _pair(app, client, *, name: str = "Robyn's phone", origin: str = LOCAL, days: int | None = None) -> dict:
    created = client.post("/devices/pairing", headers=_bearer(app), json={"expires_in_days": days})
    assert created.status_code == 201, created.text
    p = created.json()
    r = client.post("/auth/pair", json={"code": p["display_code"], "name": name}, headers={"Origin": origin})
    assert r.status_code == 200, r.text
    return {"pairing": p, "device": r.json()["device"], "cookie": r.headers["set-cookie"]}


def test_pairing_end_to_end_single_use_and_revoke(host):
    app, client = host
    created = client.post("/devices/pairing", headers=_bearer(app), json={})
    assert created.status_code == 201
    p = created.json()
    assert len(p["code"]) == 8 and p["display_code"] == f"{p['code'][:4]}-{p['code'][4:]}"
    assert p["expires_in"] == 300 and p["url"] == f"{LOCAL}/#pair={p['code']}" and p["remote_enabled"] is False
    assert p["qr_svg_data_uri"].startswith("data:image/svg+xml")
    assert all(p["code"] not in r["data_json"] for r in _audit(app, "device.pairing_created"))

    # Without the right Origin the public pairing route refuses, and the code survives for the real device.
    r = client.post("/auth/pair", json={"code": p["code"], "name": "evil"}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert client.get("/devices").status_code == 401  # nothing granted
    r = client.post("/auth/pair", json={"code": p["code"].lower(), "name": "Robyn's phone"}, headers={"Origin": LOCAL})
    assert r.status_code == 200, r.text
    cookie = r.headers["set-cookie"]
    print("device cookie attributes:", cookie.split(";", 1)[1])
    assert cookie.startswith(f"{DEVICE_COOKIE}=dev_") and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert "Secure" not in cookie  # plain HTTP on 127.0.0.1
    device = r.json()["device"]
    assert device["name"] == "Robyn's phone" and device["paired_via"] == "local" and device["expires_at"] is None

    # The device session works for reads and same-origin writes...
    session = client.get("/auth/session").json()
    assert session["via"] == "device" and session["device"]["name"] == "Robyn's phone"
    listed = client.get("/devices").json()
    assert [d["current"] for d in listed if d["id"] == device["id"]] == [True]
    assert listed[0]["last_used_at"] is not None
    assert client.post("/memory", json={"content": "from the phone"}, headers={"Origin": LOCAL}).status_code == 201
    assert client.post("/memory", json={"content": "csrf"}, headers={"Origin": "http://evil.example"}).status_code == 403

    # Single use: the same code again fails.
    phone = client.cookies.get(DEVICE_COOKIE)
    client.cookies.clear()
    again = client.post("/auth/pair", json={"code": p["code"], "name": "second"}, headers={"Origin": LOCAL})
    assert again.status_code == 401 and "not valid" in again.json()["error"]

    # Revoke from the host: the device's cookie stops working at once.
    revoked = client.delete(f"/devices/{device['id']}", headers=_bearer(app))
    assert revoked.status_code == 200 and revoked.json()["revoked_reason"] == "revoked by the user"
    client.cookies.set(DEVICE_COOKIE, phone)
    assert client.get("/devices").status_code == 401
    assert client.get("/auth/session").json()["authenticated"] is False
    kinds = [r["kind"] for r in app.state.jig.audit.query(limit=500)]
    assert {"device.pairing_created", "device.paired", "device.revoked"} <= set(kinds)
    paired = json.loads(_audit(app, "device.paired")[-1]["data_json"])
    assert paired["device_name"] == "Robyn's phone" and paired["source"] == "local"


def test_signing_out_on_a_device_unpairs_it(host):
    app, client = host
    d = _pair(app, client, name="tablet")["device"]
    assert client.post("/auth/logout", headers={"Origin": LOCAL}).status_code == 200
    assert client.get("/auth/session").json()["authenticated"] is False
    assert app.state.devices.get(d["id"])["revoked_reason"] == "signed out on the device"


def test_device_expiry_is_enforced(host):
    app, client = host
    d = _pair(app, client, name="laptop for a week", days=7)
    assert "Max-Age=6047" in d["cookie"] or "Max-Age=6048" in d["cookie"]  # about 7 days
    assert client.get("/devices").status_code == 200
    # As a week later: the stored expiry is now in the past.
    con = sqlite3.connect(app.state.devices.path)
    con.execute("UPDATE devices SET expires_at = ? WHERE id = ?",
                ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), d["device"]["id"]))
    con.commit()
    con.close()
    assert client.get("/devices").status_code == 401


def test_rotating_the_master_token_revokes_every_device(host):
    app, client = host
    _pair(app, client, name="one")
    client.cookies.clear()
    _pair(app, client, name="two")
    two = client.cookies.get(DEVICE_COOKIE)
    assert client.get("/devices").status_code == 200
    active_before = len(app.state.devices.list())
    assert active_before >= 2
    # Confirmation is required; a paired device cannot rotate it.
    assert client.post("/auth/token/rotate", json={}, headers={"Origin": LOCAL}).status_code == 400
    r = client.post("/auth/token/rotate", json={"confirm": True}, headers=_bearer(app))
    assert r.status_code == 200 and r.json()["devices_revoked"] == active_before
    client.cookies.clear()
    client.cookies.set(DEVICE_COOKIE, two)
    assert client.get("/devices").status_code == 401
    assert app.state.devices.list() == []
    assert len(_audit(app, "auth.token_rotated")) == 1


def test_rotation_by_the_cli_revokes_devices_even_while_running(tmp_path):
    """'jig token rotate' changes the file (no API); devices bound to the old token stop working, and the
    CLI marks them revoked in devices.db."""
    data = tmp_path / "data"
    tokens = TokenStore(data)
    tokens.ensure()
    store = DeviceStore(data, tokens.get)
    try:
        pairing = store.new_pairing()
        device, cookie = store.redeem(pairing["code"], name="phone", paired_via="local", tailscale_login=None)
        assert store.authenticate(cookie, tailscale_login=None)["id"] == device["id"]
        proc = subprocess.run([sys.executable, "-m", "jig.cli", "--config", str(config_path()), "token", "rotate"],
                              capture_output=True, text=True, timeout=60, env={**__import__("os").environ,
                                                                                "JIG_DATA_DIR": str(data)})
        assert proc.returncode == 0, proc.stderr
        assert "1 paired device(s) were revoked" in proc.stderr
        assert store.authenticate(cookie, tailscale_login=None) is None
        assert store.get(device["id"])["revoked_reason"] == "the master API token was rotated"
    finally:
        store.close()
    assert revoke_all_offline(data, "again") == 0


def test_pairing_codes_expire_and_wrong_codes_lock_out(tmp_path):
    tokens = TokenStore(tmp_path)
    tokens.ensure()
    store = DeviceStore(tmp_path, tokens.get, pairing_ttl_s=1.0)
    try:
        expiring = store.new_pairing()
        time.sleep(1.5)  # real time passes beyond the code's lifetime
        with pytest.raises(PairingError, match="expire"):
            store.redeem(expiring["code"], name="late", paired_via="local", tailscale_login=None)

        store.pairing_ttl_s = 300
        good = store.new_pairing()
        for _ in range(3):  # the expired attempt above counted as the first wrong code
            with pytest.raises(PairingError, match="not valid"):
                store.redeem("ZZZZ-ZZZZ", name="guess", paired_via="local", tailscale_login=None)
        with pytest.raises(PairingError, match="too many wrong codes"):
            store.redeem("YYYY-YYYY", name="guess", paired_via="local", tailscale_login=None)
        assert store.pending_codes() == 0
        with pytest.raises(PairingError):  # the valid code was cancelled with the rest
            store.redeem(good["code"], name="real", paired_via="local", tailscale_login=None)
        with pytest.raises(ValueError, match="between 1 and 365"):
            store.new_pairing(expires_in_days=0)
    finally:
        store.close()


# Tailnet requests (container mode: the tailnet name is configured) ----------------------------------

@pytest.fixture(scope="module")
def tailnet_app(tmp_path_factory):
    base = tmp_path_factory.mktemp("tailnet")
    config = load_config(data_dir=base / "data", sandbox_dir=base / "sandbox")
    config = replace(config, deployment="container", remote=RemoteConfig(hostname=TAILNET_HOST))
    app = create_app(config)
    with TestClient(app) as local:
        yield app, local


def test_forwarded_https_requests_get_secure_cookies_and_strict_origins(tailnet_app):
    app, local = tailnet_app
    # What `tailscale serve` on the host forwards: plain HTTP to the published port, Host = the tailnet name.
    remote = TestClient(app, base_url=f"http://{TAILNET_HOST}")
    forwarded = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": TAILNET_HOST}
    # Pairing codes are only created on the host (a local request), and the URL uses the tailnet origin.
    assert remote.post("/devices/pairing", json={}, headers={**forwarded, **_bearer(app)}).status_code == 403
    p = local.post("/devices/pairing", headers=_bearer(app), json={}).json()
    assert p["url"] == f"{TAILNET}/#pair={p['code']}" and p["remote_enabled"] is True

    # The master token and browser sign-in are refused over the tailnet.
    r = remote.get("/memory", headers={**forwarded, **_bearer(app)})
    assert r.status_code == 403 and "master API token is not accepted over the tailnet" in r.json()["error"]
    r = remote.post("/auth/session", json={"token": app.state.auth.tokens.get()}, headers={**forwarded, "Origin": TAILNET})
    assert r.status_code == 403 and "pair this device" in r.json()["error"]

    # Only the exact configured origin may pair: not http://, not another ts.net name, not a lookalike.
    for bad in (f"http://{TAILNET_HOST}", "https://other.example-tailnet.ts.net", f"{TAILNET}.evil.example",
                "https://evil.example", None):
        r = remote.post("/auth/pair", json={"code": p["code"], "name": "x"},
                        headers={**forwarded, **({"Origin": bad} if bad else {})})
        assert r.status_code == 403, bad
    r = remote.post("/auth/pair", json={"code": p["code"], "name": "Phone over Tailscale"},
                    headers={**forwarded, "Origin": TAILNET})
    assert r.status_code == 200, r.text
    cookie = r.headers["set-cookie"]
    print("tailnet device cookie attributes:", cookie.split(";", 1)[1])
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert r.json()["device"]["paired_via"] == "tailnet"
    # A client honours Secure: it does not send the cookie back over plain HTTP. The phone's browser is on
    # https://<tailnet name>, so carry on as that browser.
    assert remote.get("/memory", headers=forwarded).status_code == 401
    phone = remote.cookies.get(DEVICE_COOKIE)
    remote = TestClient(app, base_url=TAILNET)
    remote.cookies.set(DEVICE_COOKIE, phone)

    # The device session over the tailnet: reads work; writes need the tailnet origin exactly.
    assert remote.get("/auth/session", headers=forwarded).json()["source"] == "tailnet"
    assert remote.get("/memory", headers=forwarded).status_code == 200
    assert remote.post("/memory", json={"content": "x"}, headers={**forwarded, "Origin": TAILNET}).status_code == 201
    for bad in (LOCAL, f"http://{TAILNET_HOST}", "https://evil.example"):
        assert remote.post("/memory", json={"content": "x"}, headers={**forwarded, "Origin": bad}).status_code == 403
    # Adding devices and turning remote access on are host-only.
    assert remote.post("/devices/pairing", json={}, headers={**forwarded, "Origin": TAILNET}).status_code == 403

    # WebSocket Origin check over the tailnet (an absolute URL: the test client ignores base_url for WebSockets).
    events = f"wss://{TAILNET_HOST}/events"
    with pytest.raises(WebSocketDisconnect) as exc:
        with remote.websocket_connect(events, headers={**forwarded, "Origin": "https://evil.example"}):
            pass
    assert exc.value.code == 1008 and exc.value.reason == "cross-origin request refused"
    with remote.websocket_connect(events, headers={**forwarded, "Origin": TAILNET}) as ws:
        assert ws.receive_json()["snapshot"] is True

    # Another .ts.net name is refused outright, and so is anything marked as Funnel traffic.
    other = TestClient(app, base_url="http://other.example-tailnet.ts.net")
    r = other.get("/health")
    assert r.status_code == 403 and "remote access is turned off" in r.json()["error"]
    r = remote.get("/health", headers={"Tailscale-Funnel-Request": "?1"})
    assert r.status_code == 403 and "Funnel" in r.json()["error"]

    # Container mode: remote enable/disable and turning off from inside are refused with guidance.
    assert "tailscale serve --bg" in local.post("/remote/enable", json={"confirm": True},
                                                headers=_bearer(app)).json()["error"]
    status = local.get("/remote", headers=_bearer(app)).json()
    assert status["applicable"] is False and status["enabled"] is True and status["url"] == TAILNET


# Host mode: forged Tailscale headers over a real local TCP connection -------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="connection-owner lookup is implemented for Windows")
def test_tcp_owner_lookup_finds_the_real_owner():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    client = socket.create_connection(server.getsockname())
    try:
        accepted, peer = server.accept()
        import os

        assert tcp_owner_pid(client.getsockname(), server.getsockname()) == os.getpid()
        ok, why = verify_tailscale_peer(peer, server.getsockname())
        assert ok is False
        print("our own connection, checked as if it claimed to be tailscaled:", why)
        accepted.close()
    finally:
        client.close()
        server.close()


def test_forged_tailscale_headers_from_a_local_process_are_refused(tmp_path):
    """A real `jig serve` with remote access recorded as on. A local process (this test) forges the Host,
    X-Forwarded-* and Tailscale-User-Login headers that tailscale serve would send. The connection is not
    tailscaled's, so Jig refuses it before any route runs, including the public ones."""
    data_dir, port = tmp_path / "data", free_port()
    data_dir.mkdir(parents=True)
    (data_dir / "remote.json").write_text(json.dumps({
        "enabled": True, "hostname": TAILNET_HOST, "origin": TAILNET, "port": port,
        "owner_login": "owner@example.com", "enabled_at": "2026-10-02T00:00:00+00:00"}), encoding="utf-8")
    proc, log = start_jig(data_dir, port)
    try:
        wait_health(port, proc=proc, log=log)
        base = f"http://127.0.0.1:{port}"
        forged = {"Host": TAILNET_HOST, "X-Forwarded-Proto": "https", "X-Forwarded-Host": TAILNET_HOST,
                  "X-Forwarded-For": "100.101.102.103", "Tailscale-User-Login": "owner@example.com",
                  "Tailscale-User-Name": "Owner", "Origin": TAILNET}
        for method, path in (("GET", "/health"), ("GET", "/"), ("GET", "/devices"), ("POST", "/auth/pair")):
            r = httpx.request(method, base + path, headers=forged, json={"code": "AAAA-AAAA", "name": "x"}
                              if method == "POST" else None)
            assert r.status_code == 403, (path, r.status_code, r.text)
            assert "did not come through Tailscale on this computer" in r.json()["error"]
        print("forged request refused:", r.json()["error"])
        # Even with the master token, a forged tailnet request gets nowhere.
        r = httpx.get(base + "/memory", headers={**forged, **token(data_dir)})
        assert r.status_code == 403
        # On a normal local connection the forged identity headers are dropped and grant nothing.
        r = httpx.get(base + "/devices", headers={"Tailscale-User-Login": "owner@example.com"})
        assert r.status_code == 401
        r = httpx.get(base + "/auth/session", headers={"Tailscale-User-Login": "owner@example.com", **token(data_dir)})
        assert r.json() == {"authenticated": True, "via": "bearer", "source": "local"}
    finally:
        kill(proc)
        wait_stopped(data_dir)


# Funnel and serve-config parsing on the shape `tailscale serve status --json` prints ----------------

SERVE_STATUS = {
    "TCP": {"443": {"HTTPS": True}},
    "Web": {f"{TAILNET_HOST}:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8766"}}}},
}


def test_serve_entry_and_funnel_detection():
    entry = serve_entry(SERVE_STATUS, TAILNET_HOST, 8766)
    assert entry == {"present": True, "proxy": "http://127.0.0.1:8766", "paths": ["/"], "points_at_jig": True,
                     "funnel": False}
    assert funnels_to_port(SERVE_STATUS, 8766) == []
    funnelled = {**SERVE_STATUS, "AllowFunnel": {f"{TAILNET_HOST}:443": True}}
    assert funnels_to_port(funnelled, 8766) == [f"{TAILNET_HOST}:443"]
    assert funnels_to_port(funnelled, 9999) == []
    foreground = {"Foreground": {"abc": funnelled}}
    assert funnels_to_port(foreground, 8766) == [f"{TAILNET_HOST}:443"]
    other = {"Web": {f"{TAILNET_HOST}:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:3000"},
                                                          "/jig": {"Proxy": "http://127.0.0.1:8766"}}}}}
    assert serve_entry(other, TAILNET_HOST, 8766)["points_at_jig"] is False
    assert serve_entry({}, TAILNET_HOST, 8766)["present"] is False


def test_tailscale_status_reports_exact_steps():
    info = tailscale_info()
    print("Tailscale on this machine:", json.dumps(info.as_dict(), indent=2))
    if not info.installed:
        assert info.steps and "Install Tailscale" in info.steps[0] and "jig remote enable" in info.steps[-1]
    elif not info.ready:
        assert info.steps


# Real tailscale serve round trip (only with Tailscale installed and signed in) ----------------------

_ts = tailscale_info()


@pytest.mark.skipif(not _ts.ready, reason=f"Tailscale is not ready on this machine ({_ts.error or _ts.steps[:1]}); "
                                          "the real `tailscale serve` round trip is untested pending sign-in")
def test_real_tailscale_serve_round_trip(tmp_path):
    from jig.remote import serve_config

    original = serve_config(_ts.cli)
    data_dir, port = tmp_path / "data", free_port()
    proc, log = start_jig(data_dir, port)
    remote = RemoteAccess(load_config(data_dir=data_dir))
    try:
        wait_health(port, proc=proc, log=log)
        status = remote.enable(port)
        url = status["url"]
        assert url == f"https://{_ts.dns_name}"
        r = httpx.get(f"{url}/", timeout=30)
        assert r.status_code == 200 and "<jig-avatar" in r.text  # the UI over the real ts.net certificate
        r = httpx.get(f"{url}/devices", timeout=30)
        assert r.status_code == 401 and "pair this device" in r.json()["error"]  # verified, but not paired
    finally:
        remote.disable(port)
        kill(proc)
        wait_stopped(data_dir)
    assert serve_config(_ts.cli) == original  # the user's Tailscale config is exactly as before
