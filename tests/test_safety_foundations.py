"""Vault, audit immutability, model health and the HTTP API, all against real services."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from jig.api import create_app
from jig.constants import Mode
from jig.errors import ModelServerUnavailable
from jig.model import ModelClient, ToolCall
from jig.policy.gate import CallContext


async def test_secret_used_by_reference_and_redacted(jig):
    jig.vault.set("demo_note_secret", "s3cr3t-value-123", allowed_tools=["note_write"])
    call = ToolCall(id="c1", name="note_write",
                    arguments_raw=json.dumps({"title": "t", "body": "token={{secret:demo_note_secret}}"}))
    outcome = await jig.executor.execute(call, CallContext("r_v", None, Mode.ACTION, "store a note"))
    assert outcome.ok
    # The tool really received the value...
    assert jig.store.list_notes()[0]["body"] == "token=s3cr3t-value-123"
    # ...but what goes back to the model, and the audit trail, only has the reference.
    assert "s3cr3t-value-123" not in outcome.message_content()
    assert "[secret:demo_note_secret]" in outcome.message_content()
    audit_dump = json.dumps(jig.audit.query(limit=5000))
    assert "s3cr3t-value-123" not in audit_dump


async def test_secret_allowlist_is_a_core_rule(jig):
    jig.vault.set("only_for_notes", "abc123", allowed_tools=["note_write"])
    call = ToolCall(id="c2", name="write_file",
                    arguments_raw=json.dumps({"path": "leak.txt", "content": "{{secret:only_for_notes}}"}))
    outcome = await jig.executor.execute(call, CallContext("r_v2", None, Mode.ACTION, "write a file"))
    assert not outcome.ok and outcome.error_type == "PolicyBlocked"
    assert "secret-allowlist" in outcome.error
    assert not (jig.sandbox.root / "leak.txt").exists()


async def test_custom_block_rule(jig):
    jig.rules.create(tool="note_*", decision="block")
    call = ToolCall(id="c3", name="note_write", arguments_raw=json.dumps({"title": "t", "body": "b"}))
    outcome = await jig.executor.execute(call, CallContext("r_rule", None, Mode.ACTION, "note"))
    assert not outcome.ok and outcome.error_type == "PolicyBlocked"


async def test_audit_log_is_append_only(jig):
    jig.audit.record("test.event", "hello")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        jig.db.execute("UPDATE audit SET summary = 'tampered'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        jig.db.execute("DELETE FROM audit")


async def test_capability_probes_pass_for_configured_model(capabilities):
    assert capabilities["agent"]["tool_calling"] is True
    assert capabilities["agent"]["structured_output"] is True
    assert capabilities["sentinel"]


async def test_model_name_is_discovered_when_not_configured(config):
    client = ModelClient(replace(config.model, name=""))
    try:
        served = await client.health()
    finally:
        await client.aclose()
    assert served["model"] and client.model_name == served["model"]


async def test_health_check_fails_loudly_when_server_is_down(config):
    dead = ModelClient(replace(config.model, base_url="http://127.0.0.1:9/v1", connect_timeout_s=2.0))
    try:
        with pytest.raises(ModelServerUnavailable):
            await dead.health()
    finally:
        await dead.aclose()


async def test_health_check_fails_loudly_for_wrong_model(config):
    wrong = ModelClient(replace(config.model, name="not-a-real-model"))
    try:
        with pytest.raises(ModelServerUnavailable, match="not served"):
            await wrong.health()
    finally:
        await wrong.aclose()


def test_http_api_crud(config):
    with TestClient(create_app(config)) as client:
        health = client.get("/health").json()
        assert health["status"] == "ok"
        assert health["model"]["model"] == (config.model.name or health["model"]["model"])
        assert health["capabilities"]["agent"]["tool_calling"] is True
        assert client.get("/state").json()["state"] == "idle"

        m = client.post("/memory", json={"content": "Likes walking in the Peak District", "tags": ["hobby"]}).json()
        assert client.get("/memory", params={"q": "walking"}).json()[0]["id"] == m["id"]
        assert client.patch(f"/memory/{m['id']}", json={"content": "Likes hiking"}).json()["content"] == "Likes hiking"
        assert client.delete(f"/memory/{m['id']}").status_code == 204
        assert client.get(f"/memory/{m['id']}").status_code == 404

        rule = client.post("/rules", json={"tool": "web_fetch", "decision": "ask", "arg": "url",
                                           "pattern": "https://shop.*"}).json()
        assert client.patch(f"/rules/{rule['id']}", json={"decision": "block"}).json()["decision"] == "block"
        assert client.post("/rules", json={"tool": "x", "decision": "maybe"}).status_code == 422
        assert any(r["id"] == "no-credential-changes" for r in client.get("/rules/core").json())
        assert client.delete(f"/rules/{rule['id']}").status_code == 204

        assert client.put("/vault/api_key_demo", json={"value": "hidden-123", "allowed_tools": ["web_fetch"]}).status_code == 200
        listing = client.get("/vault").text
        assert "api_key_demo" in listing and "hidden-123" not in listing

        audit = client.get("/audit", params={"kind": "memory"}).json()
        assert [a["kind"] for a in audit] == ["memory.added", "memory.edited", "memory.forgotten"]
        assert "Peak District" not in json.dumps(audit), "the audit trail must not retain forgotten content"

        tools = {t["name"]: t for t in client.get("/tools").json()}
        assert tools["web_fetch"]["outbound"] and tools["web_fetch"]["avatar_variant"] == "browsing"
        assert tools["write_file"]["effect"] == "side_effect"
