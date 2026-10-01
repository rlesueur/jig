"""The Jig runtime: wires the model, stores, policy, agent and scheduler together."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from .agent.loop import Agent, RunSpec
from .agent.planner import Planner
from .agent.prompts import agent_system_prompt
from .audit import AuditLog
from .config import Config
from .constants import EventType, GoalStatus, Mode, RunStatus, TaskStatus
from .db import Database, new_id, now_iso
from .errors import JigError, NotFound
from .events import Event, EventBus, AvatarStateTracker
from .memory import MemoryStore
from .model import ModelClient
from .policy.approvals import ApprovalQueue
from .policy.gate import CallContext, ToolExecutor
from .policy.rules import RuleStore
from .policy.sentinel import Sentinel
from .sandbox import Sandbox
from .scheduler import Scheduler
from .store import Store
from .tools.builtin import build_registry, http_client
from .tools.registry import ToolContext
from .vault import Vault
from .vision import VisionService

log = logging.getLogger(__name__)

_CHAT_EVENT_TYPES = {EventType.TOOL_START, EventType.TOOL_END, EventType.SENTINEL_VERDICT,
                     EventType.APPROVAL_REQUESTED, EventType.APPROVAL_RESOLVED}


class Jig:
    def __init__(self, config: Config):
        self.config = config
        self.db = Database(config.db_path)
        self.bus = EventBus()
        self.tracker = AvatarStateTracker(self.bus)
        self.audit = AuditLog(self.db)
        self.store = Store(self.db)
        self.memory = MemoryStore(self.db)
        self.vault = Vault(self.db)
        self.sandbox = Sandbox(config.sandbox_dir, config.runtime.agent_id)
        self.model = ModelClient(config.model, label="agent model")
        self.sentinel_model = ModelClient(config.sentinel, label="Sentinel model")
        self.http = http_client()
        self.registry = build_registry()
        self.vision = VisionService(self.model, config.vision)
        self.rules = RuleStore(self.db)
        self.sentinel = Sentinel(self.sentinel_model)
        self.approvals = ApprovalQueue(self.db, self.bus, self.audit)
        self.executor = ToolExecutor(registry=self.registry, rules=self.rules, sentinel=self.sentinel,
                                     approvals=self.approvals, vault=self.vault, audit=self.audit, bus=self.bus,
                                     context_factory=self._tool_context)
        self.agent = Agent(model=self.model, registry=self.registry, executor=self.executor, store=self.store,
                           bus=self.bus, audit=self.audit, max_steps=config.runtime.max_steps)
        self.planner = Planner(model=self.model, registry=self.registry, store=self.store, bus=self.bus,
                               audit=self.audit)
        self.scheduler = Scheduler(self, max_concurrent=config.runtime.max_concurrent_tasks,
                                   heartbeat_s=config.runtime.heartbeat_s)
        self._background: set[asyncio.Task[Any]] = set()
        self.capabilities: dict[str, Any] = {}

    def _tool_context(self, ctx: CallContext) -> ToolContext:
        return ToolContext(sandbox=self.sandbox, memory=self.memory, store=self.store, config=self.config,
                           http=self.http, mode=ctx.mode, run_id=ctx.run_id, task_id=ctx.task_id,
                           vision=self.vision)

    # Lifecycle -------------------------------------------------------------
    async def start(self, *, run_scheduler: bool = True, check_capabilities: bool = True) -> None:
        """Check the model servers and capabilities (failing loudly), recover interrupted work, start the heartbeat."""
        await self.model.health()
        await self.sentinel_model.health()
        if check_capabilities:
            self.capabilities = await self.check_capabilities()
        for client in (self.model, self.sentinel_model):
            ctx = client.server_info.get("context_tokens")
            if ctx is not None and ctx < self.config.runtime.min_context_tokens:
                log.warning("%s %s has a %d-token context; %d or more is advisable", client.label,
                            client.model_name, ctx, self.config.runtime.min_context_tokens)
        self.audit.record("runtime.start", "Jig started", actor="runtime", model=self.model.server_info,
                          sentinel=self.sentinel_model.server_info, capabilities=self.capabilities,
                          vault_backend=self.vault.backend, sandbox=str(self.sandbox.root))
        self._recover()
        if run_scheduler:
            self.scheduler.start()

    async def check_capabilities(self) -> dict[str, Any]:
        """Real probes: the agent model must make well-formed tool calls and produce JSON-schema output
        (used by the planner); the Sentinel model must produce JSON-schema output."""
        caps: dict[str, Any] = {
            "agent": {**await self.model.probe_tool_calling(), **await self.model.probe_structured_output()},
        }
        same = (self.config.sentinel.base_url.rstrip("/") == self.config.model.base_url.rstrip("/")
                and self.sentinel_model.model_name == self.model.model_name)
        caps["sentinel"] = {"same_as_agent": True} if same else await self.sentinel_model.probe_structured_output()
        if self.vision.enabled:
            caps["agent"] |= await self.vision.probe()
        return caps

    async def stop(self) -> None:
        await self.scheduler.stop()
        for t in list(self._background):
            t.cancel()
        await asyncio.gather(*self._background, return_exceptions=True)
        self.audit.record("runtime.stop", "Jig stopped", actor="runtime")
        await self.http.aclose()
        await self.model.aclose()
        await self.sentinel_model.aclose()
        self.db.close()

    def _recover(self) -> None:
        for status in (TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL):
            for task in self.store.list_tasks(status=status):
                self.store.update_task(task["id"], status=TaskStatus.QUEUED)
                self.audit.record("task.recovered", "task re-queued after restart; it will resume from its "
                                  "checkpoint", actor="runtime", task_id=task["id"], previous=status.value)
        for run in self.db.query("SELECT id FROM runs WHERE status = ? AND kind != 'task'", (RunStatus.RUNNING,)):
            self.store.finish_run(run["id"], status=RunStatus.FAILED, error="interrupted by a restart")
        for goal in self.store.list_goals(status=GoalStatus.PLANNING):
            self._spawn(self._plan_goal(goal["id"]))

    def _spawn(self, coro: Any) -> asyncio.Task[Any]:
        t = asyncio.create_task(coro)
        self._background.add(t)
        t.add_done_callback(self._background.discard)
        return t

    # Tasks -----------------------------------------------------------------
    def publish_task(self, task: dict[str, Any]) -> None:
        self.bus.publish(EventType.TASK_STATUS, task_id=task["id"], goal_id=task["goal_id"], status=task["status"],
                         title=task["title"])

    def set_task_status(self, task_id: str, status: TaskStatus, **fields: Any) -> dict[str, Any]:
        if status == TaskStatus.RUNNING:
            fields.setdefault("started_at", now_iso())
        if status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.BLOCKED):
            fields.setdefault("finished_at", now_iso())
        before = self.store.get_task(task_id)["status"]
        task = self.store.update_task(task_id, status=status, **fields)
        if before != status:
            self.audit.record("task.status", f"{before} -> {status}", actor="runtime", task_id=task_id,
                              before=before, after=status.value, error=fields.get("error"))
            self.publish_task(task)
        return task

    def create_task(self, *, title: str, description: str, mode: Mode, delay_s: float = 0.0) -> dict[str, Any]:
        task = self.store.create_task(title=title, description=description, mode=mode, delay_s=delay_s)
        self.audit.record("task.created", f"task {title!r} created", actor="user", task_id=task["id"],
                          mode=task["mode"])
        self.publish_task(task)
        self.scheduler.wake()
        return task

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task["status"] in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.BLOCKED):
            raise ValueError(f"task {task_id} is already {task['status']}")
        self.scheduler.cancel(task_id)
        return self.set_task_status(task_id, TaskStatus.CANCELLED, error="cancelled by the user")

    def _task_prompt(self, task: dict[str, Any]) -> tuple[str, str]:
        if not task["goal_id"]:
            return task["description"], f"{task['title']}: {task['description']}"
        goal = self.store.get_goal(task["goal_id"])
        siblings = self.store.list_tasks(goal_id=goal["id"])
        position = next(i for i, t in enumerate(siblings, 1) if t["id"] == task["id"])
        parts = [f"Overall goal: {goal['description']}"]
        if goal["plan"]:
            parts.append(f"Plan: {goal['plan']['summary']}")
        parts.append(f"Your task ({position} of {len(siblings)}): {task['title']}\n{task['description']}")
        for dep_id in task["depends_on"]:
            dep = self.store.get_task(dep_id)
            parts.append(f"Result of earlier task '{dep['title']}':\n{(dep['result'] or '')[:4000]}")
        parts.append("Complete only your task, then reply with a concise result.")
        intent = f"User goal: {goal['description']}\nCurrent task: {task['title']}: {task['description']}"
        return "\n\n".join(parts), intent

    async def run_task(self, task_id: str) -> None:
        task = self.store.get_task(task_id)
        mode = Mode(task["mode"])
        prompt, intent = self._task_prompt(task)
        previous = self.store.resumable_run(task_id)
        if previous and previous["messages"]:
            messages, run_id, resume = previous["messages"], previous["id"], True
        else:
            if previous:
                self.store.finish_run(previous["id"], status=RunStatus.FAILED, error="interrupted before any step")
            messages = [{"role": "system", "content": agent_system_prompt(mode, self.config.runtime.timezone)},
                        {"role": "user", "content": prompt}]
            run_id, resume = None, False

        async def on_wait(waiting: bool) -> None:
            self.set_task_status(task_id, TaskStatus.WAITING_APPROVAL if waiting else TaskStatus.RUNNING)

        spec = RunSpec(kind="task", mode=mode, intent=intent, task_id=task_id, run_id=run_id, resume=resume,
                       on_wait=on_wait, is_shutdown=lambda: self.scheduler.stopping)
        try:
            result = await self.agent.run(messages, spec)
        except asyncio.CancelledError:
            raise
        except (JigError, OSError) as exc:
            self.set_task_status(task_id, TaskStatus.FAILED, error=f"{type(exc).__name__}: {exc}")
            return
        except Exception as exc:
            log.exception("task %s crashed", task_id)
            self.set_task_status(task_id, TaskStatus.FAILED, error=f"internal error: {type(exc).__name__}: {exc}")
            return
        self.set_task_status(task_id, TaskStatus.DONE, result=result.final)

    # Goals -----------------------------------------------------------------
    def create_goal(self, *, description: str, title: str | None = None) -> dict[str, Any]:
        goal = self.store.create_goal(title=title or description[:80], description=description)
        self.audit.record("goal.created", f"goal {goal['title']!r} created", actor="user", goal_id=goal["id"])
        self.bus.publish(EventType.GOAL_STATUS, goal_id=goal["id"], status=goal["status"])
        self._spawn(self._plan_goal(goal["id"]))
        return goal

    async def _plan_goal(self, goal_id: str) -> None:
        try:
            await self.planner.plan(goal_id)
        except (JigError, ValueError) as exc:
            log.error("planning goal %s failed: %s", goal_id, exc)
            return
        self.scheduler.wake()

    def cancel_goal(self, goal_id: str) -> dict[str, Any]:
        goal = self.store.get_goal(goal_id)
        if goal["status"] in (GoalStatus.DONE, GoalStatus.FAILED, GoalStatus.CANCELLED):
            raise ValueError(f"goal {goal_id} is already {goal['status']}")
        for task in self.store.list_tasks(goal_id=goal_id):
            if task["status"] not in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.BLOCKED):
                self.cancel_task(task["id"])
        goal = self.store.update_goal(goal_id, status=GoalStatus.CANCELLED, error="cancelled by the user")
        self.audit.record("goal.status", "goal cancelled", actor="user", goal_id=goal_id, status="cancelled")
        self.bus.publish(EventType.GOAL_STATUS, goal_id=goal_id, status="cancelled")
        return goal

    # Chat ------------------------------------------------------------------
    async def chat(self, message: str, *, session_id: str | None = None,
                   mode: Mode = Mode.ACTION) -> AsyncIterator[dict[str, Any]]:
        """Stream one chat turn: reasoning/content deltas, tool and approval events, then ``done``."""
        if not message.strip():
            raise ValueError("message must not be empty")
        session_id = session_id or new_id("sess")
        try:
            history = self.store.get_session(session_id)
        except NotFound:
            history = []
        messages = [{"role": "system", "content": agent_system_prompt(mode, self.config.runtime.timezone)},
                    *history, {"role": "user", "content": message}]
        run_id = self.store.create_run(kind="chat", mode=mode, session_id=session_id)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def on_delta(kind: str, text: str) -> None:
            await queue.put({"type": kind, "text": text})

        def listener(event: Event) -> None:
            if event.data.get("run_id") == run_id and event.type in _CHAT_EVENT_TYPES:
                queue.put_nowait({"type": "event", "event": event.as_dict()})

        spec = RunSpec(kind="chat", mode=mode, intent=f"User message: {message}", session_id=session_id,
                       run_id=run_id, on_delta=on_delta)

        async def drive() -> None:
            try:
                result = await self.agent.run(messages, spec)
            except Exception as exc:
                if not isinstance(exc, (JigError, OSError)):
                    log.exception("chat run %s crashed", run_id)
                await queue.put({"type": "error", "error": f"{type(exc).__name__}: {exc}", "run_id": run_id,
                                 "session_id": session_id})
                return
            self.store.save_session(session_id, [m for m in result.messages if m["role"] != "system"])
            await queue.put({"type": "done", "final": result.final, "run_id": run_id, "session_id": session_id,
                             "steps": result.steps})

        self.bus.add_listener(listener)
        runner = asyncio.create_task(drive())
        try:
            yield {"type": "start", "run_id": run_id, "session_id": session_id}
            while True:
                item = await queue.get()
                yield item
                if item["type"] in ("done", "error"):
                    break
        finally:
            self.bus.remove_listener(listener)
            if not runner.done():
                runner.cancel()
                await asyncio.gather(runner, return_exceptions=True)
