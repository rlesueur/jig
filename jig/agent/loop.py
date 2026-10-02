"""The multi-step tool-calling loop, with structured run and step records."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ..audit import AuditLog
from ..constants import EventType, Mode, RunStatus
from ..errors import JigError, ModelError, StepLimitExceeded
from ..events import EventBus
from ..model import ModelClient, ToolCall
from ..pause import RunPaused, until_paused
from ..policy.gate import CallContext, ToolExecutor
from ..store import Store
from ..tools.registry import ToolRegistry

DeltaSink = Callable[[str, str], Awaitable[None]]


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


class Agent:
    def __init__(self, *, model: ModelClient, registry: ToolRegistry, executor: ToolExecutor, store: Store,
                 bus: EventBus, audit: AuditLog, max_steps: int):
        self.model = model
        self.registry = registry
        self.executor = executor
        self.store = store
        self.bus = bus
        self.audit = audit
        self.max_steps = max_steps

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
                               on_wait=spec.on_wait, pause=spec.pause)
        steps = _steps_taken(messages)
        try:
            if pending := _pending_tool_calls(messages):
                await self._run_tools(pending, messages, call_ctx, steps)
                self.store.checkpoint_run(run_id, messages, steps)
            while True:
                if steps >= limit:
                    raise StepLimitExceeded(f"run stopped after reaching the step limit of {limit} model calls")
                result = await until_paused(self._model_step(messages, spec, run_id, steps + 1), spec.pause,
                                            f"model call {steps + 1}")
                steps += 1
                messages.append(result.assistant_message())
                self.store.checkpoint_run(run_id, messages, steps)
                if not result.tool_calls:
                    if not result.content.strip():
                        raise ModelError("model returned an empty final answer")
                    final = result.content
                    break
                await self._run_tools(result.tool_calls, messages, call_ctx, steps)
                self.store.checkpoint_run(run_id, messages, steps)
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
        self.store.finish_run(run_id, status=RunStatus.DONE, final=final)
        self.audit.record("run.end", "run done", **ids, status="done", steps=steps, final_chars=len(final))
        self.bus.publish(EventType.RUN_END, status="done", steps=steps, **ids)
        return RunResult(run_id=run_id, status=RunStatus.DONE, final=final, steps=steps, messages=messages)

    async def _model_step(self, messages: list[dict[str, Any]], spec: RunSpec, run_id: str, idx: int):
        ids = {"run_id": run_id, "task_id": spec.task_id}
        tools = self.registry.schemas_for_mode(spec.mode)
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
            result = await self.model.chat(messages, tools=tools, on_delta=on_delta)
        except asyncio.CancelledError:
            self.store.finish_step(step_id, status="cancelled", error="interrupted")
            self.bus.publish(EventType.MODEL_END, step=idx, ok=False, **ids)
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
