"""The gate: every tool call passes through here, whatever the prompt said.

Order of checks for one call:
  1. the tool exists and its arguments validate against its schema (for an outbound tool, a bare web
     address such as ``www.example.com`` in a URL argument is first completed to ``https://``, see urls.py,
     so every later check sees the complete URL);
  2. mode: research mode refuses anything that is not read-only or a private write;
  3. core rules (non-overridable): block, or force a human approval;
  4. the user's custom rule for this tool (allow / ask / block);
  5. the Sentinel, for every outbound or side-effecting action (deny cannot be overridden);
  6. an approval, if any of the above asked for one; the run pauses until answered;
  7. secret references are resolved, the tool runs, and secret values are redacted from the result.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..audit import AuditLog
from ..constants import ApprovalStatus, Decision, Effect, EventType, Mode, Verdict
from ..errors import (ApprovalDenied, JigError, ModeViolation, PolicyBlocked, SentinelError, ToolArgumentError,
                      ToolError, ToolNotFound)
from ..events import EventBus
from ..model import ModelError, ToolCall
from ..pause import until_paused
from ..tools.registry import ToolContext, ToolRegistry
from ..vault import Vault
from .approvals import ApprovalQueue
from .core import evaluate_core
from .rules import RuleStore
from .sentinel import Sentinel
from .urls import complete_url_args

TOOL_TIMEOUT_S = 180.0


@dataclass
class CallContext:
    run_id: str
    task_id: str | None
    mode: Mode
    intent: str
    # Called with True when the run pauses for an approval and False when it resumes.
    on_wait: Callable[[bool], Awaitable[None]] | None = None
    # Set when the user pauses the run; an approval wait is interrupted and resumes later.
    pause: asyncio.Event | None = None


@dataclass
class ToolOutcome:
    call_id: str
    name: str
    ok: bool
    result: Any = None
    error: str | None = None
    error_type: str | None = None
    policy: dict[str, Any] = field(default_factory=dict)

    def message_content(self) -> str:
        if self.ok:
            return json.dumps(self.result, ensure_ascii=False, default=str)
        return json.dumps({"error": self.error, "error_type": self.error_type}, ensure_ascii=False)

    def as_dict(self) -> dict[str, Any]:
        return {"call_id": self.call_id, "name": self.name, "ok": self.ok, "result": self.result,
                "error": self.error, "error_type": self.error_type, "policy": self.policy}


class ToolExecutor:
    def __init__(self, *, registry: ToolRegistry, rules: RuleStore, sentinel: Sentinel, approvals: ApprovalQueue,
                 vault: Vault, audit: AuditLog, bus: EventBus, context_factory: Callable[[CallContext], ToolContext],
                 always_redact: dict[str, str] | None = None):
        self.registry = registry
        # Values redacted from every tool result and error even when no secret was referenced (model API keys).
        self.always_redact = always_redact or {}
        self.rules = rules
        self.sentinel = sentinel
        self.approvals = approvals
        self.vault = vault
        self.audit = audit
        self.bus = bus
        self.context_factory = context_factory

    async def execute(self, call: ToolCall, ctx: CallContext) -> ToolOutcome:
        policy: dict[str, Any] = {}
        try:
            result = await self._execute(call, ctx, policy)
            outcome = ToolOutcome(call.id, call.name, True, result=result, policy=policy)
            self.audit.record("tool.result", f"{call.name} succeeded", task_id=ctx.task_id, run_id=ctx.run_id,
                              tool=call.name, call_id=call.id, result_preview=outcome.message_content()[:500])
        except asyncio.CancelledError:
            raise
        except (JigError, OSError) as exc:
            outcome = ToolOutcome(call.id, call.name, False, error=str(exc), error_type=type(exc).__name__,
                                  policy=policy)
            self.audit.record("tool.error", f"{call.name} failed: {type(exc).__name__}", task_id=ctx.task_id,
                              run_id=ctx.run_id, tool=call.name, call_id=call.id, error=str(exc),
                              error_type=type(exc).__name__)
        return outcome

    async def _execute(self, call: ToolCall, ctx: CallContext, policy: dict[str, Any]) -> Any:
        try:
            spec = self.registry.get(call.name)
        except ToolNotFound:
            self.audit.record("tool.call", f"unknown tool {call.name}", task_id=ctx.task_id, run_id=ctx.run_id,
                              tool=call.name, call_id=call.id)
            raise
        try:
            args = call.arguments()
        except ModelError as exc:
            raise ToolArgumentError(str(exc)) from exc
        self.audit.record("tool.call", f"{spec.name} requested", task_id=ctx.task_id, run_id=ctx.run_id,
                          tool=spec.name, call_id=call.id, args=args, mode=ctx.mode.value)
        url_notes: list[dict[str, Any]] = []
        if spec.outbound:
            args, completed = complete_url_args(args)
            if completed:
                policy["url_normalised"] = completed
                self.audit.record("policy.url_normalised",
                                  f"{spec.name}: " + "; ".join(f"{c['original']} -> {c['normalised']}" for c in completed),
                                  task_id=ctx.task_id, run_id=ctx.run_id, tool=spec.name, call_id=call.id,
                                  normalised=completed)
                url_notes = [{"rule": "url-normalised", "decision": "info",
                              "reason": f"{c['original']!r} has no scheme, so Jig will use {c['normalised']}"}
                             for c in completed]
        spec.validate(args)

        if not spec.allowed_in(ctx.mode):
            policy["mode"] = "refused"
            self.audit.record("policy.mode_violation", f"{spec.name} refused in {ctx.mode} mode",
                              task_id=ctx.task_id, run_id=ctx.run_id, tool=spec.name, effect=spec.effect.value)
            raise ModeViolation(
                f"core rule research-read-only: {spec.name!r} ({spec.effect.value}) is not permitted in "
                f"{ctx.mode.value} mode"
            )

        findings = await evaluate_core(spec, args, self.vault)
        rule = self.rules.match(spec.name, args)
        policy["core"] = [f.as_dict() for f in findings]
        policy["rule"] = {"id": rule["id"], "decision": rule["decision"]} if rule else None
        rule_decision = Decision(rule["decision"]) if rule else spec.default_decision
        self.audit.record("policy.decision", f"{spec.name}: rule={rule_decision.value}, core={len(findings)} findings",
                          task_id=ctx.task_id, run_id=ctx.run_id, tool=spec.name, core=policy["core"],
                          rule=policy["rule"], default_decision=spec.default_decision.value)

        blocks = [f for f in findings if f.decision == Decision.BLOCK]
        if blocks:
            raise PolicyBlocked("; ".join(f"core rule {f.rule_id}: {f.reason}" for f in blocks))
        if rule_decision == Decision.BLOCK:
            raise PolicyBlocked(f"custom rule {rule['id'] if rule else 'default'} blocks {spec.name!r}")

        reasons = [f.as_dict() for f in findings if f.decision == Decision.ASK]
        if rule_decision == Decision.ASK:
            reasons.append({"rule": rule["id"] if rule else "default", "decision": "ask",
                            "reason": f"custom rule requires approval for {spec.name!r}"})

        verdict = None
        if spec.outbound or spec.effect == Effect.SIDE_EFFECT:
            try:
                verdict = await self.sentinel.review(
                    intent=ctx.intent, mode=ctx.mode.value, spec=spec, args=args,
                    policy={"core": policy["core"], "custom_rule": rule_decision.value},
                )
            except SentinelError as exc:
                self.audit.record("sentinel.error", f"Sentinel failed on {spec.name}", actor="sentinel",
                                  task_id=ctx.task_id, run_id=ctx.run_id, tool=spec.name, error=str(exc))
                raise
            policy["sentinel"] = verdict.as_dict()
            self.audit.record("sentinel.verdict", f"{spec.name}: {verdict.verdict.value} ({verdict.risk})",
                              actor="sentinel", task_id=ctx.task_id, run_id=ctx.run_id, tool=spec.name,
                              args=args, **verdict.as_dict())
            self.bus.publish(EventType.SENTINEL_VERDICT, run_id=ctx.run_id, task_id=ctx.task_id, tool=spec.name,
                             **verdict.as_dict())
            if verdict.verdict == Verdict.DENY:
                raise PolicyBlocked(f"Sentinel denied {spec.name!r}: {verdict.reason}")
            if verdict.verdict == Verdict.ASK_USER:
                reasons.append({"rule": "sentinel", "decision": "ask", "reason": verdict.reason})

        if reasons:
            approval = self.approvals.request(
                run_id=ctx.run_id, task_id=ctx.task_id, tool_call_id=call.id, tool=spec.name, args=args,
                reasons=reasons + url_notes, sentinel=verdict.as_dict() if verdict else None,
            )
            if approval["status"] == ApprovalStatus.PENDING:
                if ctx.on_wait:
                    await ctx.on_wait(True)
                try:
                    approval = await until_paused(self.approvals.wait(approval["id"]), ctx.pause,
                                                  f"the approval wait for {spec.name}")
                finally:
                    if ctx.on_wait:
                        await ctx.on_wait(False)
            policy["approval"] = {"id": approval["id"], "status": approval["status"], "note": approval["note"]}
            if approval["status"] != ApprovalStatus.APPROVED:
                note = f" ({approval['note']})" if approval["note"] else ""
                raise ApprovalDenied(f"the user denied {spec.name!r}{note}")

        used: dict[str, str] = {}
        real_args = self.vault.resolve(args, used)
        if used:
            self.audit.record("vault.use", f"{spec.name} used secrets {sorted(used)}", task_id=ctx.task_id,
                              run_id=ctx.run_id, tool=spec.name, secrets=sorted(used))
        tool_ctx = self.context_factory(ctx)
        self.bus.publish(EventType.TOOL_START, run_id=ctx.run_id, task_id=ctx.task_id, tool=spec.name,
                         category=spec.category.value, variant=spec.avatar_variant.value, effect=spec.effect.value,
                         call_id=call.id)
        redact = {**self.always_redact, **used}
        ok = False
        try:
            async with asyncio.timeout(TOOL_TIMEOUT_S):
                result = await spec.fn(tool_ctx, **real_args)
            ok = True
        except TimeoutError as exc:
            raise ToolError(f"{spec.name} timed out after {TOOL_TIMEOUT_S:.0f}s") from exc
        except (JigError, OSError):
            message = str(sys.exc_info()[1])
            if not used and Vault.redact(message, redact) == message:
                raise
            raise ToolError(Vault.redact(message, redact)) from None
        except Exception as exc:
            raise ToolError(Vault.redact(f"{type(exc).__name__}: {exc}", redact)) from None
        finally:
            self.bus.publish(EventType.TOOL_END, run_id=ctx.run_id, task_id=ctx.task_id, tool=spec.name,
                             call_id=call.id, ok=ok)
        return Vault.redact(result, redact)
