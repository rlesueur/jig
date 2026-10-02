"""Real, model-free tests for the AgentDojo adapter.

These drive Jig's actual policy gate (`jig.policy.gate.ToolExecutor`) over real AgentDojo tools and a real
AgentDojo environment. No model is needed: the gate's mode, custom-rule and execution behaviour is
deterministic, so these run without a GPU. The full LLM-driven benchmark (which produces ASR/utility) needs a
live model and is run by the d1 experiment under the compute policy, not here.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("agentdojo")

from agentdojo.functions_runtime import FunctionsRuntime
from agentdojo.task_suite.load_suites import get_suite, get_suites

from jig.audit import AuditLog
from jig.constants import Effect, Mode, RESEARCH_ALLOWED_EFFECTS
from jig.db import Database
from jig.events import EventBus
from jig.model import ToolCall
from jig.policy.approvals import ApprovalQueue
from jig.policy.gate import CallContext, ToolExecutor
from jig.policy.rules import RuleStore
from jig.tools.registry import ToolContext
from jig.vault import Vault

from jigbench.agentdojo_adapter import TOOL_CLASS, EnvHolder, build_registry, classify
from jigbench.reviewers import PassThroughReviewer


def _ctx_factory(ctx: CallContext) -> ToolContext:
    return ToolContext(sandbox=None, memory=None, store=None, config=None, http=None,
                       mode=ctx.mode, run_id=ctx.run_id, task_id=ctx.task_id)


def _executor(tmp: Path, suite, holder: EnvHolder, rules: list[dict] | None = None) -> ToolExecutor:
    db = Database(tmp / "t.db")
    bus, audit = EventBus(), AuditLog(db)
    rulestore, approvals, vault = RuleStore(db), ApprovalQueue(db, bus, audit), Vault(db)
    for r in rules or []:
        rulestore.create(**r)
    registry = build_registry(suite.tools, holder)
    return ToolExecutor(registry=registry, rules=rulestore, sentinel=PassThroughReviewer(), approvals=approvals,
                        vault=vault, audit=audit, bus=bus, context_factory=_ctx_factory)


def _bind(suite, holder: EnvHolder) -> None:
    holder.runtime = FunctionsRuntime(suite.tools)
    holder.env = suite.load_and_inject_default_environment({})


def _dummy_required_args(suite, name: str) -> dict:
    func = next(f for f in suite.tools if f.name == name)
    return {n: "x" for n, fld in func.parameters.model_fields.items() if fld.is_required()}


def test_every_suite_tool_is_classified() -> None:
    for suite in get_suites("v1").values():
        for tool in suite.tools:
            assert tool.name in TOOL_CLASS, f"{tool.name} in suite {suite.name} is unclassified"


def test_classification_invariants() -> None:
    # A side-effecting tool must never be one of the research-allowed effects (so research mode refuses it).
    for name, (effect, _outbound, _cat) in TOOL_CLASS.items():
        if effect is Effect.SIDE_EFFECT:
            assert effect not in RESEARCH_ALLOWED_EFFECTS, name
    # Reading an external web page is outbound, so the Sentinel reviews it even though it only reads.
    assert classify("get_webpage") == (Effect.READ, True, classify("get_webpage")[2])
    assert TOOL_CLASS["get_webpage"][1] is True
    assert TOOL_CLASS["send_money"][0] is Effect.SIDE_EFFECT


def test_read_tool_executes_through_gate(tmp_path: Path) -> None:
    suite = get_suite("v1", "banking")
    holder = EnvHolder()
    ex = _executor(tmp_path, suite, holder)
    _bind(suite, holder)
    call = ToolCall(id="1", name="get_balance", arguments_raw="{}")
    ctx = CallContext(run_id="r", task_id=None, mode=Mode.ACTION, intent="check my balance")
    outcome = asyncio.run(ex.execute(call, ctx))
    assert outcome.ok, outcome.error
    assert outcome.result is not None


def test_side_effect_refused_in_research_mode(tmp_path: Path) -> None:
    suite = get_suite("v1", "banking")
    holder = EnvHolder()
    ex = _executor(tmp_path, suite, holder)
    _bind(suite, holder)
    args = _dummy_required_args(suite, "send_money")
    call = ToolCall(id="1", name="send_money", arguments_raw=json.dumps(args))
    ctx = CallContext(run_id="r", task_id=None, mode=Mode.RESEARCH, intent="send money to a friend")
    outcome = asyncio.run(ex.execute(call, ctx))
    assert not outcome.ok
    assert outcome.error_type == "ModeViolation", outcome.error


def test_custom_rule_blocks_tool(tmp_path: Path) -> None:
    suite = get_suite("v1", "banking")
    holder = EnvHolder()
    ex = _executor(tmp_path, suite, holder, rules=[{"tool": "get_balance", "decision": "block"}])
    _bind(suite, holder)
    call = ToolCall(id="1", name="get_balance", arguments_raw="{}")
    ctx = CallContext(run_id="r", task_id=None, mode=Mode.ACTION, intent="check my balance")
    outcome = asyncio.run(ex.execute(call, ctx))
    assert not outcome.ok
    assert outcome.error_type == "PolicyBlocked", outcome.error


def test_unknown_tool_is_not_classified_silently() -> None:
    with pytest.raises(KeyError):
        classify("totally_made_up_tool")
