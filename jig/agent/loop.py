"""The multi-step tool-calling loop, with structured run and step records."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .. import claims
from ..audit import AuditLog
from ..constants import EventType, Mode, RunStatus
from ..errors import JigError, ModelError, ModelStopped, RefusedActions, RepeatedActions, StepLimitExceeded
from ..events import EventBus
from ..model import ModelClient, ToolCall
from ..pause import RunPaused, until_paused
from ..policy.gate import CallContext, ToolExecutor
from ..store import Store
from ..tools.registry import ToolRegistry
from .prompts import CONTINUE_KEY, STOPPED_KEY, STEP_LIMIT_PROMPT, budget_line
from .refusals import REFUSED_ACTION_LIMIT, refusal_kind, stop_message, stop_record

DeltaSink = Callable[[str, str], Awaitable[None]]
# A run that makes the same tool call and gets the same result this many times is stopped.
REPEATED_ACTION_LIMIT = 3


@dataclass
class RunSpec:
    kind: str  # chat | task
    mode: Mode
    intent: str
    task_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None  # use an existing run record (created by the caller, or being resumed)
    resume: bool = False
    on_delta: DeltaSink | None = None
    on_wait: Callable[[bool], Awaitable[None]] | None = None
    is_shutdown: Callable[[], bool] = lambda: False
    # Set to pause the run at the next safe point (see jig.pause); the run stays resumable.
    pause: asyncio.Event | None = None


@dataclass
class RunResult:
    run_id: str
    status: RunStatus
    final: str
    steps: int
    messages: list[dict[str, Any]]
    # The run used every model call it was allowed; ``final`` is its own account of what it did and what is left,
    # from one more call with no tools offered.
    limit_reached: bool = False
    # What ``final`` claims Jig did, compared with the run's tool records (jig.claims.check).
    claim_check: dict[str, Any] | None = None
    # A continuation came back empty. ``final`` is the reply that was already written; it is not replaced.
    nothing_more: bool = False


def _steps_taken(messages: list[dict[str, Any]]) -> int:
    """Model calls already made for the newest user message (a resumed run's own steps), not earlier turns'."""
    last_user = max((i for i, m in enumerate(messages) if m["role"] == "user"), default=-1)
    return sum(1 for m in messages[last_user + 1:] if m["role"] == "assistant")


def _pending_tool_calls(messages: list[dict[str, Any]]) -> list[ToolCall]:
    """Tool calls in the last assistant message that have no result yet (used on resume)."""
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg["role"] == "assistant":
            answered = {m.get("tool_call_id") for m in messages[i + 1:] if m["role"] == "tool"}
            return [
                ToolCall(id=tc["id"], name=tc["function"]["name"], arguments_raw=tc["function"]["arguments"])
                for tc in msg.get("tool_calls") or []
                if tc["id"] not in answered
            ]
        if msg["role"] == "user":
            return []
    return []


def _continuing(messages: list[dict[str, Any]]) -> bool:
    """Whether this turn continues a reply that was stopped for repeating itself (the user chose "Continue
    anyway"), so the progress check is relaxed for it."""
    last_user = next((m for m in reversed(messages) if m["role"] == "user"), None)
    return bool(last_user and last_user.get(CONTINUE_KEY))


def _reply_so_far(messages: list[dict[str, Any]]) -> str:
    """Text of the stopped reply a continuation is carrying on, when that reply already has some."""
    last_user = next((i for i in range(len(messages) - 1, -1, -1) if messages[i]["role"] == "user"), None)
    if last_user is None or not messages[last_user].get(CONTINUE_KEY) or last_user == 0:
        return ""
    prev = messages[last_user - 1]
    if prev.get("role") != "assistant":
        return ""
    text = prev.get("content") or ""
    return text if isinstance(text, str) and text.strip() else ""


def _action_key(call: ToolCall, result: str) -> str:
    """The same call (tool and arguments) with the same result, as a digest: no content is kept."""
    try:
        args = json.dumps(json.loads(call.arguments_raw or "{}"), sort_keys=True)
    except ValueError:
        args = call.arguments_raw
    return hashlib.sha256(f"{call.name}\0{args}\0{result}".encode()).hexdigest()


class Agent:
    def __init__(self, *, model: ModelClient, registry: ToolRegistry, executor: ToolExecutor, store: Store,
                 bus: EventBus, audit: AuditLog, max_steps: int,
                 context_tokens: Callable[[], int | None] | None = None):
        self.model = model
        self.registry = registry
        self.executor = executor
        self.store = store
        self.bus = bus
        self.audit = audit
        self.max_steps = max_steps
        # The model's context window, for the budget line (None when unknown).
        self.context_tokens = context_tokens or (lambda: model.server_info.get("context_tokens"))
        # Turns attached pictures into image parts on a copy of the messages, just before they are sent.
        # The stored conversation keeps only the file ids (jig.attachments.expand_message).
        self.prepare_outgoing: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None

    async def run(self, messages: list[dict[str, Any]], spec: RunSpec, *, max_steps: int | None = None) -> RunResult:
        limit = max_steps or self.max_steps
        resuming = spec.resume
        run_id = spec.run_id or self.store.create_run(kind=spec.kind, mode=spec.mode, task_id=spec.task_id,
                                                      session_id=spec.session_id)
        ids = {"run_id": run_id, "task_id": spec.task_id}
        self.bus.publish(EventType.RUN_START, kind=spec.kind, mode=spec.mode.value, resumed=resuming, **ids)
        self.audit.record("run.start", f"{spec.kind} run in {spec.mode} mode{' (resumed)' if resuming else ''}",
                          **ids, run_kind=spec.kind, mode=spec.mode.value)
        call_ctx = CallContext(run_id=run_id, task_id=spec.task_id, mode=spec.mode, intent=spec.intent,
                               on_wait=spec.on_wait, pause=spec.pause, session_id=spec.session_id)
        steps = _steps_taken(messages)
        limit_reached = False
        relaxed = _continuing(messages)
        nothing_more = False
        actions: dict[str, int] = {}
        try:
            if pending := _pending_tool_calls(messages):
                await self._run_tools(pending, messages, call_ctx, steps)
                self._check_refusals(run_id, steps, ids)
                self._add_budget(messages, steps, limit, None)
                self.store.checkpoint_run(run_id, messages, steps)
            while True:
                if steps >= limit:
                    final = await self._finish_at_limit(messages, spec, run_id, steps, limit)
                    steps += 1
                    limit_reached = True
                    self.store.checkpoint_run(run_id, messages, steps)
                    break
                result = await until_paused(self._model_step(messages, spec, run_id, steps + 1, relaxed=relaxed),
                                            spec.pause, f"model call {steps + 1}")
                steps += 1
                if not result.tool_calls and not result.content.strip():
                    # A continuation that adds nothing must not replace the reply already written, or be reported
                    # as a bare model error. The continue note is dropped so the stopped reply stays last.
                    if kept := _reply_so_far(messages):
                        if messages[-1].get("role") == "user" and messages[-1].get(CONTINUE_KEY):
                            messages.pop()
                        self.store.checkpoint_run(run_id, messages, steps)
                        final = kept
                        nothing_more = True
                        break
                    messages.append(result.assistant_message())
                    self.store.checkpoint_run(run_id, messages, steps)
                    raise ModelError("model returned an empty final answer")
                messages.append(result.assistant_message())
                self.store.checkpoint_run(run_id, messages, steps)
                if not result.tool_calls:
                    final = result.content
                    break
                await self._run_tools(result.tool_calls, messages, call_ctx, steps)
                self._check_refusals(run_id, steps, ids)
                self._check_repeats(result.tool_calls, messages, actions, ids)
                self._add_budget(messages, steps, limit, result)
                self.store.checkpoint_run(run_id, messages, steps)
        except ModelStopped as exc:
            # What arrived is kept, marked as stopped, so the reply can be continued or tried again.
            messages.append({"role": "assistant", "content": exc.partial.content, STOPPED_KEY: exc.stop.record()})
            self.store.checkpoint_run(run_id, messages, steps)
            self.store.finish_run(run_id, status=RunStatus.FAILED, error=f"ModelStopped: {exc}")
            self.audit.record("run.end", "run stopped: the model was repeating itself", **ids, status="stopped",
                              stop=exc.stop.record())
            self.bus.publish(EventType.RUN_END, status="stopped", reason=exc.stop.kind, **ids)
            raise
        except RefusedActions as exc:
            self.store.checkpoint_run(run_id, messages, steps)
            self.store.finish_run(run_id, status=RunStatus.FAILED, error=f"RefusedActions: {exc}")
            self.audit.record("run.end", "run stopped: its actions kept being refused", **ids, status="stopped",
                              reason="refused", **exc.record)
            self.bus.publish(EventType.RUN_END, status="stopped", reason="refused", **ids)
            raise
        except RunPaused as exc:
            # The run record stays 'running' with its checkpoint, so resuming the task picks it up.
            self.store.checkpoint_run(run_id, messages, steps)
            self.audit.record("run.end", f"run paused: {exc}", **ids, status="paused")
            self.bus.publish(EventType.RUN_END, status="paused", **ids)
            raise
        except asyncio.CancelledError:
            status = "interrupted" if spec.is_shutdown() else RunStatus.CANCELLED.value
            if status == RunStatus.CANCELLED.value:
                self.store.finish_run(run_id, status=RunStatus.CANCELLED, error="cancelled")
            self.audit.record("run.end", f"run {status}", **ids, status=status)
            self.bus.publish(EventType.RUN_END, status=status, **ids)
            raise
        except (JigError, OSError) as exc:
            error = f"{type(exc).__name__}: {exc}"
            self.store.finish_run(run_id, status=RunStatus.FAILED, error=error)
            self.audit.record("run.end", "run failed", **ids, status="failed", error_type=type(exc).__name__,
                              error_chars=len(error))
            self.bus.publish(EventType.RUN_END, status="failed", error=error, **ids)
            raise
        claim_check = self._check_claims(final, spec, run_id, steps, ids)
        if limit_reached:
            note = f"stopped at the step limit of {limit} model calls; the final answer says what is left"
            self.store.finish_run(run_id, status=RunStatus.DONE, final=final, error=note)
            self.audit.record("run.end", f"run {note}", **ids, status="step_limit", steps=steps,
                              final_chars=len(final))
            self.bus.publish(EventType.RUN_END, status="step_limit", steps=steps, **ids)
        else:
            self.store.finish_run(run_id, status=RunStatus.DONE, final=final)
            self.audit.record("run.end", "run done", **ids, status="done", steps=steps, final_chars=len(final))
            self.bus.publish(EventType.RUN_END, status="done", steps=steps, **ids)
        return RunResult(run_id=run_id, status=RunStatus.DONE, final=final, steps=steps, messages=messages,
                         limit_reached=limit_reached, claim_check=claim_check, nothing_more=nothing_more)

    def _check_claims(self, final: str, spec: RunSpec, run_id: str, idx: int, ids: dict[str, Any]) -> dict[str, Any]:
        """Compare what the reply says Jig did with the run's tool records; kept as a run step, and in the audit
        log as counts only."""
        result = claims.check(final, self.store.list_steps(run_id), mode=spec.mode.value, request=spec.intent)
        step_id = self.store.start_step(run_id, idx, "claim_check", "claims", {"reply_chars": len(final)})
        self.store.finish_step(step_id, status="flagged" if result["notes"] else "ok", output=result)
        self.audit.record("run.claim_check", f"{len(result['claims'])} claims checked, {len(result['notes'])} "
                          "not done", **ids, **claims.record(result))
        return result

    def _check_refusals(self, run_id: str, idx: int, ids: dict[str, Any]) -> None:
        """Stop the run once REFUSED_ACTION_LIMIT of its tool calls in a row were refused. Counted from the run's own
        step records, so a run that paused (for the user, or a restart) carries on counting where it was."""
        refused: list[tuple[str, str]] = []
        for step in reversed(self.store.list_steps(run_id)):
            if step["type"] != "tool_call" or step["status"] not in ("ok", "error"):
                continue
            if step["status"] == "ok":
                break
            if kind := refusal_kind(step["output"]):
                refused.append((step["name"], kind))
                if len(refused) == REFUSED_ACTION_LIMIT:
                    break
        if len(refused) < REFUSED_ACTION_LIMIT:
            return
        refused.reverse()
        record = stop_record(refused)
        step_id = self.store.start_step(run_id, idx, "stop", "refused_actions", {"limit": REFUSED_ACTION_LIMIT})
        self.store.finish_step(step_id, status="stopped", output=record)
        self.audit.record("run.refused_actions", f"stopped after {len(refused)} refused actions in a row", **ids,
                          **record)
        raise RefusedActions(stop_message(refused), record=record)

    def _check_repeats(self, calls: list[ToolCall], messages: list[dict[str, Any]], actions: dict[str, int],
                       ids: dict[str, Any]) -> None:
        """Stop the run once it has made the same call and got the same result REPEATED_ACTION_LIMIT times."""
        last = max(i for i, m in enumerate(messages) if m["role"] == "assistant")
        results = {m.get("tool_call_id"): m["content"] for m in messages[last + 1:] if m["role"] == "tool"}
        for call in calls:
            if call.id not in results:
                continue
            key = _action_key(call, results[call.id])
            actions[key] = actions.get(key, 0) + 1
            if actions[key] >= REPEATED_ACTION_LIMIT:
                self.audit.record("run.repeated_actions", f"{call.name} made with the same arguments and the same "
                                  f"result {actions[key]} times", **ids, tool=call.name, times=actions[key])
                raise RepeatedActions(
                    f"Jig stopped this run because it made the same call ({call.name}, with the same arguments) and "
                    f"got the same result {actions[key]} times, so it was not getting anywhere. What it did up to "
                    "then is kept.")

    def _add_budget(self, messages: list[dict[str, Any]], step: int, limit: int, result: Any) -> None:
        """End the step's last tool result with the budget line (kept, so the prompt stays a stable prefix)."""
        if not messages or messages[-1]["role"] != "tool":
            return
        used = None
        if result is not None:
            usage, timings = result.usage or {}, result.timings or {}
            prompt = usage.get("prompt_tokens") or (timings.get("prompt_n", 0) + timings.get("cache_n", 0))
            used = (prompt + (usage.get("completion_tokens") or timings.get("predicted_n", 0))) or None
        messages[-1]["content"] += budget_line(step=step, max_steps=limit, context_used=used,
                                               context_size=self.context_tokens())

    async def _finish_at_limit(self, messages: list[dict[str, Any]], spec: RunSpec, run_id: str, steps: int,
                               limit: int) -> str:
        """One more model call, with no tools offered, for the run's own account of what it did and what is left.
        The instruction is sent with that call only; the answer joins the conversation as the final answer."""
        ask = [*messages, {"role": "user", "content": STEP_LIMIT_PROMPT.format(limit=limit)}]
        result = await until_paused(self._model_step(ask, spec, run_id, steps + 1, offer_tools=False), spec.pause,
                                    f"model call {steps + 1} (at the step limit)")
        if result.tool_calls:
            self.audit.record("run.step_limit_calls_ignored", "the model asked for tools after the step limit; "
                              "none were run", run_id=run_id, task_id=spec.task_id,
                              tools=[c.name for c in result.tool_calls])
        if not result.content.strip():
            raise StepLimitExceeded(f"run stopped after reaching the step limit of {limit} model calls, and the "
                                    "model gave no account of what it did")
        messages.append({"role": "assistant", "content": result.content})
        return result.content

    async def _model_step(self, messages: list[dict[str, Any]], spec: RunSpec, run_id: str, idx: int, *,
                          offer_tools: bool = True, relaxed: bool = False):
        ids = {"run_id": run_id, "task_id": spec.task_id}
        tools = self.registry.schemas_for_mode(spec.mode, task=spec.kind == "task") if offer_tools else []
        step_id = self.store.start_step(run_id, idx, "model_call", self.model.model_name,
                                        {"messages": len(messages), "tools": [t["function"]["name"] for t in tools]})
        self.bus.publish(EventType.MODEL_START, step=idx, **ids)

        on_delta = None
        if spec.on_delta is not None:
            sink = spec.on_delta

            async def on_delta(kind: str, text: str) -> None:
                if kind == "content":
                    self.bus.publish(EventType.CHAT_DELTA, kind=kind, **ids)
                await sink(kind, text)

        try:
            outgoing = self.prepare_outgoing(messages) if self.prepare_outgoing else messages
            result = await self.model.chat(outgoing, tools=tools or None, on_delta=on_delta, relaxed=relaxed)
        except asyncio.CancelledError:
            self.store.finish_step(step_id, status="cancelled", error="interrupted")
            self.bus.publish(EventType.MODEL_END, step=idx, ok=False, **ids)
            raise
        except ModelStopped as exc:
            # Only where and why it stopped is recorded, never the repeated text.
            self.store.finish_step(step_id, status="stopped", error=str(exc), output={
                "stop": exc.stop.record(), "content_chars": len(exc.partial.content),
                "reasoning_chars": len(exc.partial.reasoning), "relaxed": relaxed,
                "elapsed_s": round(exc.partial.elapsed_s, 3)})
            self.audit.record("model.stopped", f"step {idx}: stopped as it streamed, repeating itself", **ids,
                              step=idx, stop=exc.stop.record(), relaxed=relaxed)
            self.bus.publish(EventType.MODEL_END, step=idx, ok=False, stopped=exc.stop.kind, **ids)
            raise
        except JigError as exc:
            self.store.finish_step(step_id, status="error", error=str(exc))
            self.audit.record("model.call", f"model call failed: {type(exc).__name__}", **ids, step=idx,
                              error_type=type(exc).__name__, error_chars=len(str(exc)))
            self.bus.publish(EventType.MODEL_END, step=idx, ok=False, **ids)
            raise
        self.store.finish_step(step_id, status="ok", output={
            "content": result.content, "reasoning": result.reasoning,
            "tool_calls": [tc.as_message_part() for tc in result.tool_calls], **result.summary(),
        })
        self.audit.record("model.call", f"step {idx}: {len(result.tool_calls)} tool calls, "
                          f"{len(result.content)} chars", **ids, step=idx, **result.summary())
        self.bus.publish(EventType.MODEL_END, step=idx, ok=True, tool_calls=[tc.name for tc in result.tool_calls],
                         **ids)
        return result

    async def _run_tools(self, calls: list[ToolCall], messages: list[dict[str, Any]], ctx: CallContext,
                         idx: int) -> None:
        step_ids = [self.store.start_step(ctx.run_id, idx, "tool_call", c.name, {"id": c.id, "arguments": c.arguments_raw})
                    for c in calls]
        # Wait for every call, so a pause in one (an approval wait) never abandons another that is executing.
        outcomes = await asyncio.gather(*(self.executor.execute(c, ctx) for c in calls), return_exceptions=True)
        paused: RunPaused | None = None
        failure: BaseException | None = None
        for step_id, outcome in zip(step_ids, outcomes, strict=True):
            if isinstance(outcome, RunPaused):
                self.store.finish_step(step_id, status="paused", error=str(outcome))
                paused = outcome
                continue
            if isinstance(outcome, BaseException):
                self.store.finish_step(step_id, status="error", error=f"{type(outcome).__name__}: {outcome}")
                failure = failure or outcome
                continue
            self.store.finish_step(step_id, status="ok" if outcome.ok else "error", output=outcome.as_dict(),
                                   error=outcome.error)
            messages.append({"role": "tool", "tool_call_id": outcome.call_id, "content": outcome.message_content()})
        if failure is not None:
            raise failure
        if paused is not None:
            # Keep the finished results; the paused calls have none yet, so they run again on resume.
            self.store.checkpoint_run(ctx.run_id, messages, idx)
            raise paused
