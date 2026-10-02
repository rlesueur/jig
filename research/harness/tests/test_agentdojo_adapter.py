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

from jigbench.agentdojo_adapter import (TOOL_CLASS, EnvHolder, JigGatedToolsExecutor, ReviewerFailed,
                                        build_registry, classify)
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


def _gated(tmp: Path, suite, holder: EnvHolder, mode: Mode, sentinel=None):
    import threading

    ex = _executor(tmp, suite, holder)
    if sentinel is not None:
        ex.sentinel = sentinel
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    return JigGatedToolsExecutor(ex, holder, loop, mode, run_id="r", intent="pay my bill"), loop


def test_gate_refusals_and_tool_errors_are_counted_apart(tmp_path: Path) -> None:
    suite = get_suite("v1", "banking")
    holder = EnvHolder()
    gated, loop = _gated(tmp_path, suite, holder, Mode.RESEARCH)
    _bind(suite, holder)
    try:
        gated._run_call("send_money", _dummy_required_args(suite, "send_money"), "1")  # refused by mode
        gated._run_call("get_most_recent_transactions", {"n": "not-a-number"}, "2")    # allowed, tool fails
        gated._run_call("get_balance", {}, "3")                                       # allowed, succeeds
    finally:
        loop.call_soon_threadsafe(loop.stop)
    assert (gated.blocked, gated.tool_errors, gated.executed) == (1, 1, 1)
    assert gated.blocks_by_type == {"ModeViolation": 1}


def test_reviewer_failure_is_a_harness_error_not_a_block(tmp_path: Path) -> None:
    import socket

    from jig.config import EndpointConfig
    from jig.model import ModelClient
    from jig.policy.sentinel import Sentinel

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here: the real Sentinel's model call genuinely fails
    sentinel = Sentinel(ModelClient(EndpointConfig(base_url=f"http://127.0.0.1:{port}/v1", name="none"),
                                    label="sentinel"))
    suite = get_suite("v1", "banking")
    holder = EnvHolder()
    gated, loop = _gated(tmp_path, suite, holder, Mode.ACTION, sentinel=sentinel)
    _bind(suite, holder)
    try:
        with pytest.raises(ReviewerFailed):
            gated._run_call("send_money", _dummy_required_args(suite, "send_money"), "1")
    finally:
        loop.call_soon_threadsafe(loop.stop)
    assert gated.blocked == 0


def test_attacks_resolve_the_model_name_for_every_condition() -> None:
    import yaml
    from agentdojo.agent_pipeline import AgentPipeline
    from agentdojo.attacks.attack_registry import load_attack
    from agentdojo.attacks.base_attacks import get_model_name_from_pipeline

    from jigbench.experiments.d1_agentdojo import pipeline_name

    configs = Path(__file__).resolve().parents[1] / "configs"
    suite = get_suite("v1", "banking")
    for path in configs.glob("*/d1_*.yaml"):
        cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
        for cond in cfg["conditions"]:
            pipeline = AgentPipeline([])
            pipeline.name = pipeline_name(cond["id"])
            assert get_model_name_from_pipeline(pipeline) == "Local model", cond["id"]
            for attack in cfg["attacks"]:
                if attack == "none":
                    continue
                user_task = next(iter(suite.user_tasks.values()))
                injection_task = next(iter(suite.injection_tasks.values()))
                injections = load_attack(attack, suite, pipeline).attack(user_task, injection_task)
                assert injections, (path.name, cond["id"], attack)


def test_unknown_tool_is_not_classified_silently() -> None:
    with pytest.raises(KeyError):
        classify("totally_made_up_tool")
