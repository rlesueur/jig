"""MCP over a real stdio server: Jig speaks the protocol, and the gate is the real one.

The safety checker is the real Sentinel pointed at an address nothing is listening on, never the model
server. An action it cannot review does not run. Nothing here is a stand-in for the MCP protocol.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from jig.config import EndpointConfig, load_config
from jig.constants import Mode
from jig.db import Database
from jig.audit import AuditLog
from jig.events import EventBus
from jig.mcp import McpService
from jig.mcp.protocol import classify
from jig.mcp.stdio import McpError
from jig.model import ModelClient, ToolCall
from jig.policy.approvals import ApprovalQueue
from jig.policy.core import evaluate_core
from jig.policy.gate import CallContext, ToolExecutor
from jig.policy.rules import RuleStore
from jig.policy.sentinel import Sentinel
from jig.tools.builtin import build_registry
from jig.tools.registry import ToolContext
from jig.vault import Vault

SERVER = Path(__file__).with_name("mcp_stdio_server.py")
SECRET = "jig-mcp-test-secret-9f3c"


class _Stack:
    def __init__(self, tmp_path: Path):
        self.config = load_config(data_dir=tmp_path / "data", sandbox_dir=tmp_path / "sandbox")
        self.db = Database(self.config.db_path)
        self.audit = AuditLog(self.db)
        self.vault = Vault(self.db, self.config.vault)
        self.bus = EventBus()
        self.rules = RuleStore(self.db)
        self.approvals = ApprovalQueue(self.db, self.bus, self.audit)
        self.registry = build_registry()
        self.redactions: dict[str, str] = {}
        self.mcp = McpService(self.db, self.vault, self.audit, self.registry, self.redactions)
        # Not the user's model server. Connection refused, so a review fails closed and nothing is sent anywhere else.
        endpoint = EndpointConfig(base_url="http://127.0.0.1:9/v1", name="sentinel", connect_timeout_s=2.0,
                                  first_token_timeout_s=2.0, liveness_timeout_s=2.0)
        self.model = ModelClient(endpoint, label="sentinel model")
        self.executor = ToolExecutor(
            registry=self.registry, rules=self.rules, sentinel=Sentinel(self.model), approvals=self.approvals,
            vault=self.vault, audit=self.audit, bus=self.bus, context_factory=self._context,
            always_redact=self.redactions, config=self.config)
        self.calls = tmp_path / "mcp-calls.txt"

    def _context(self, ctx: CallContext) -> ToolContext:
        return ToolContext(sandbox=None, memory=None, store=None, config=self.config, http=None,
                           mode=ctx.mode, run_id=ctx.run_id, task_id=ctx.task_id)

    async def add(self, *, label: str = "Adder", access: str = "act") -> dict:
        row = self.mcp.add(label=label, command=sys.executable, args=[str(SERVER), "--calls", str(self.calls)],
                           access=access, via="test")
        try:
            return await self.mcp.refresh(row["id"])
        except McpError as exc:
            if exc.start_failed:
                pytest.skip(f"the MCP test server could not be started ({exc})")
            raise

    def called(self) -> list[str]:
        if not self.calls.is_file():
            return []
        return [line for line in self.calls.read_text(encoding="utf-8").splitlines() if line]

    async def close(self) -> None:
        await self.mcp.close()
        await self.model.aclose()
        self.db.close()


def _ctx(mode: Mode, run_id: str) -> CallContext:
    return CallContext(run_id, None, mode, "use the MCP server")


def _tool(status: dict, remote: str) -> dict:
    return next(tool for tool in status["tools"] if tool["remote"] == remote)


async def _stack(tmp_path: Path):
    stack = _Stack(tmp_path)
    try:
        yield stack
    finally:
        await stack.close()


@pytest.fixture
async def mcp(tmp_path: Path):
    async for stack in _stack(tmp_path):
        yield stack


def test_an_undeclared_effect_fails_closed():
    info = classify({"name": "shout", "inputSchema": {"type": "object"}})
    assert info["effect"].value == "side_effect" and info["outbound"] is True and info["declared"] is False
    assert info["decision"].value == "ask"
    hinted = classify({"name": "look", "annotations": {"readOnlyHint": True, "openWorldHint": False}})
    assert hinted["effect"].value == "read" and hinted["outbound"] is False and hinted["declared"] is True
    quiet_read = classify({"name": "peek", "_meta": {"jig": {"effect": "read", "category": "files"}}})
    assert quiet_read["effect"].value == "read" and quiet_read["outbound"] is True


async def test_a_declared_local_read_runs_without_the_safety_checker(mcp: _Stack):
    status = await mcp.add()
    tool = _tool(status, "add")
    assert tool["effect"] == "read" and tool["outbound"] is False and tool["declared"] is True
    assert tool["human_only"] is False
    call = ToolCall(id="m-add", name=tool["name"], arguments_raw=json.dumps({"a": 2, "b": 3}))
    outcome = await mcp.executor.execute(call, _ctx(Mode.RESEARCH, "r-mcp-add"))
    assert outcome.ok, outcome.error
    assert outcome.result["text"] == "5"
    assert outcome.result["source"] == "mcp" and "untrusted" in outcome.result
    assert "sentinel" not in outcome.policy
    assert mcp.called() == ["add"]
    recorded = json.loads(mcp.audit.query(kind="tool.result", run_id="r-mcp-add")[0]["data_json"])
    assert set(recorded["result"]) == {"chars"} and recorded["result"]["chars"] > 0
    blob = json.dumps(mcp.audit.query(limit=5000))
    assert "Add two integers" not in blob


async def test_research_mode_refuses_a_side_effect_and_an_undeclared_tool_before_they_run(mcp: _Stack):
    status = await mcp.add()
    for remote in ("stamp", "shout"):
        tool = _tool(status, remote)
        call = ToolCall(id=f"m-{remote}", name=tool["name"], arguments_raw=json.dumps({"text": "hello"} if remote == "shout" else {}))
        outcome = await mcp.executor.execute(call, _ctx(Mode.RESEARCH, f"r-mcp-{remote}"))
        assert not outcome.ok and outcome.error_type == "ModeViolation", outcome.error
        assert "sentinel" not in outcome.policy
    assert mcp.called() == []
    blob = json.dumps(mcp.audit.query(limit=5000))
    assert "hello" not in blob


async def test_an_undeclared_tool_still_asks_and_is_not_called_when_the_checker_cannot_review(mcp: _Stack):
    """A custom allow rule does not skip the core ask. The real Sentinel is consulted and, with nowhere to
    reach it, the call stops. The server is not asked to run the tool."""
    status = await mcp.add()
    tool = _tool(status, "shout")
    assert tool["effect"] == "side_effect" and tool["outbound"] is True and tool["human_only"] is True
    spec = mcp.registry.get(tool["name"])
    mcp.rules.create(tool=tool["name"], decision="allow", note="test: try to make an undeclared tool automatic")
    findings = await evaluate_core(spec, {"text": "hello"}, mcp.vault)
    assert any(f.rule_id == "human-only-actions" and f.decision.value == "ask" for f in findings)
    call = ToolCall(id="m-shout", name=tool["name"], arguments_raw=json.dumps({"text": "hello"}))
    outcome = await mcp.executor.execute(call, _ctx(Mode.ACTION, "r-mcp-shout"))
    assert not outcome.ok and outcome.error_type == "SentinelError", outcome.error
    assert outcome.policy["rule"]["decision"] == "allow"
    assert any(item["rule"] == "human-only-actions" for item in outcome.policy["core"])
    assert "shout" not in mcp.called()


async def test_read_only_hides_and_refuses_anything_that_is_not_a_read(mcp: _Stack):
    status = await mcp.add(label="Reader", access="read")
    offered = {tool["remote"] for tool in status["tools"] if mcp.registry.get(tool["name"]).is_available()}
    assert offered == {"add", "env_flag", "label"}
    stamp = _tool(status, "stamp")
    spec = mcp.registry.get(stamp["name"])
    assert spec.is_available() is False
    with pytest.raises(McpError, match="read only"):
        await spec.fn(ToolContext(sandbox=None, memory=None, store=None, config=mcp.config, http=None,
                                  mode=Mode.ACTION, run_id="r", task_id=None))
    assert "stamp" not in mcp.called()


async def test_a_vault_secret_reaches_the_server_and_nowhere_else(mcp: _Stack):
    status = await mcp.add()
    await mcp.mcp.set_env(status["id"], "JIG_MCP_SECRET", SECRET, via="test")
    tool = _tool(mcp.mcp.one(status["id"]), "env_flag")
    call = ToolCall(id="m-env", name=tool["name"], arguments_raw="{}")
    outcome = await mcp.executor.execute(call, _ctx(Mode.RESEARCH, "r-mcp-env"))
    assert outcome.ok, outcome.error
    assert outcome.result["text"] == "set"
    shown = json.dumps(mcp.mcp.status())
    blob = json.dumps(mcp.audit.query(limit=5000))
    listed = json.dumps(mcp.vault.list())
    leaked = SECRET in shown or SECRET in blob or SECRET in listed or SECRET in json.dumps(outcome.result)
    assert not leaked
    assert "JIG_MCP_SECRET" in shown
    public = mcp.mcp.one(status["id"])
    assert public["env"] == ["JIG_MCP_SECRET"]
    assert "command" in public and SECRET not in public["command"]


async def test_naming_an_mcp_secret_in_a_tool_call_is_blocked(mcp: _Stack):
    status = await mcp.add()
    await mcp.mcp.set_env(status["id"], "JIG_MCP_SECRET", SECRET, via="test")
    spec = mcp.registry.get(_tool(mcp.mcp.one(status["id"]), "label")["name"])
    reference = "{{secret:mcp." + status["id"] + ".env.JIG_MCP_SECRET}}"
    findings = await evaluate_core(spec, {"label": reference}, mcp.vault)
    assert any(f.rule_id == "secret-allowlist" and f.decision.value == "block" for f in findings)
    call = ToolCall(id="m-leak", name=spec.name, arguments_raw=json.dumps({"label": reference}))
    outcome = await mcp.executor.execute(call, _ctx(Mode.RESEARCH, "r-mcp-leak"))
    assert not outcome.ok and outcome.error_type == "PolicyBlocked", outcome.error
    assert outcome.error is not None and SECRET not in outcome.error
    assert "label" not in mcp.called()
