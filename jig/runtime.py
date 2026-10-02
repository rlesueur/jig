"""The Jig runtime: wires the model, stores, policy, agent and scheduler together."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from .agent.loop import Agent, RunSpec
from .agent.planner import Planner
from .agent.prompts import MEMORY_PROMPT_LIMIT, agent_system_prompt, memory_prompt
from .audit import AuditLog, text_size
from .cloud import connection_summary, require_consent, resolve_api_key
from .config import Config
from .connectors import ConnectionStore, Connectors
from .connectors import register_tools as register_connector_tools
from .constants import TERMINAL_TASK_STATUSES, EventType, GoalStatus, Mode, RunStatus, TaskStatus
from .db import Database, new_id, now_iso
from .errors import CannotDelete, JigError, NotFound
from .events import Event, EventBus, AvatarStateTracker
from .memory import MemoryStore
from .instance import InstanceLock
from .model import ModelClient
from .model_server import ModelServerSupervisor
from .pause import RunPaused
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

# How much of an earlier task's result a dependent task is given in its prompt.
DEPENDENCY_RESULT_CHARS = 4000

_CHAT_EVENT_TYPES = {EventType.TOOL_START, EventType.TOOL_END, EventType.SENTINEL_VERDICT,
                     EventType.APPROVAL_REQUESTED, EventType.APPROVAL_RESOLVED}


class Jig:
    def __init__(self, config: Config, *, start_reason: str = "manual"):
        self.config = config
        self.start_reason = start_reason
        # Taken first: two runtimes must never open the same data directory.
        self.instance_lock = InstanceLock(config.data_dir)
        self.instance_lock.acquire(start_reason=start_reason, host=config.server.host, port=config.server.port,
                                   config=str(config.source))
        try:
            self._init_components(config)
        except BaseException:
            self.instance_lock.release()
            raise

    def _init_components(self, config: Config) -> None:
        self.db = Database(config.db_path)
        self.bus = EventBus()
        self.tracker = AvatarStateTracker(self.bus)
        self.audit = AuditLog(self.db)
        self.store = Store(self.db, on_schedule_change=lambda schedule_id, action: self.bus.publish(
            EventType.SCHEDULE_CHANGED, schedule_id=schedule_id, action=action),
            on_note_change=lambda note_id, action: self.bus.publish(EventType.NOTE_CHANGED, note_id=note_id,
                                                                    action=action),
            on_history_change=self._history_changed)
        self.memory = MemoryStore(self.db, on_change=lambda memory_id, action: self.bus.publish(
            EventType.MEMORY_CHANGED, memory_id=memory_id, action=action))
        self.vault = Vault(self.db, config.vault)
        # Before any client exists, so nothing is sent to a cloud endpoint without consent.
        self.cloud = require_consent(config, self.audit)
        self.sandbox = Sandbox(config.sandbox_dir, config.runtime.agent_id)
        agent_key = resolve_api_key(config.model, self.vault, role="agent")
        sentinel_key = resolve_api_key(config.sentinel, self.vault, role="sentinel")
        self.model = ModelClient(config.model, label="agent model", api_key=agent_key)
        self.sentinel_model = ModelClient(config.sentinel, label="Sentinel model", api_key=sentinel_key)
        # Redacted from every tool result and error. The connectors add each token they use to this same dict.
        self.redactions = {name: key for name, key in (("model API key", agent_key), ("Sentinel API key", sentinel_key))
                           if key}
        self.http = http_client()
        self.registry = build_registry()
        self.connections = ConnectionStore(self.db, self.vault, self.audit, config.connectors)
        self.connectors = Connectors(self.connections, self.http, self.redactions)
        register_connector_tools(self.registry, self.connectors)
        self.vision = VisionService(self.model, config.vision)
        self.rules = RuleStore(self.db)
        self.container = self._container_backend()
        self.sentinel = Sentinel(self.sentinel_model)
        self.approvals = ApprovalQueue(self.db, self.bus, self.audit)
        self.executor = ToolExecutor(registry=self.registry, rules=self.rules, sentinel=self.sentinel,
                                     approvals=self.approvals, vault=self.vault, audit=self.audit, bus=self.bus,
                                     context_factory=self._tool_context, always_redact=self.redactions,
                                     config=config)
        self.agent = Agent(model=self.model, registry=self.registry, executor=self.executor, store=self.store,
                           bus=self.bus, audit=self.audit, max_steps=config.runtime.max_steps)
        self.planner = Planner(model=self.model, registry=self.registry, store=self.store, bus=self.bus,
                               audit=self.audit)
        self.scheduler = Scheduler(self, max_concurrent=config.runtime.max_concurrent_tasks,
                                   heartbeat_s=config.runtime.heartbeat_s)
        self._background: set[asyncio.Task[Any]] = set()
        self.capabilities: dict[str, Any] = {}
        self.model_server = ModelServerSupervisor(
            config.model_launch, config.data_dir / "logs", state_dir=config.data_dir,
            audit=lambda kind, summary, **data: self.audit.record(kind, summary, actor="runtime", **data))
        self._closed = False

    def connection(self) -> dict[str, Any]:
        """Where the agent and the safety checker run (local or cloud), for /status and the audit log."""
        return connection_summary(self.config, self.audit)

    def _system_prompt(self, mode: Mode) -> str:
        return (agent_system_prompt(mode, self.config.runtime.timezone, can_run_code=self.container is not None)
                + memory_prompt(self.memory.list(limit=MEMORY_PROMPT_LIMIT)))

    def _tool_context(self, ctx: CallContext) -> ToolContext:
        return ToolContext(sandbox=self.sandbox, memory=self.memory, store=self.store, config=self.config,
                           http=self.http, mode=ctx.mode, run_id=ctx.run_id, task_id=ctx.task_id,
                           vision=self.vision, container=self.container, connectors=self.connectors)

    def _container_backend(self) -> Any:
        """With ``[sandbox] backend = "container"`` (per-agent Docker) or ``"compose"`` (sandbox services next
        to a containerised Jig), build the backend and register its tools."""
        if self.config.sandbox.backend not in ("container", "compose"):
            return None
        from .sandbox_container import ContainerSandbox, EgressProxy
        from .tools.browser import register_browser_tools
        from .tools.sandbox_exec import register_exec_tools

        egress = EgressProxy(bind=self.config.sandbox.egress_bind, ports=self.config.sandbox.egress_ports,
                             rules=self.rules, audit=self.audit)
        register_exec_tools(self.registry)
        register_browser_tools(self.registry)
        if self.config.sandbox.backend == "compose":
            from .sandbox_compose import ComposeSandbox

            return ComposeSandbox(self.config.sandbox, self.sandbox.root, egress)
        return ContainerSandbox(self.config.sandbox, self.sandbox.root, self.config.runtime.agent_id, egress)

    # Lifecycle -------------------------------------------------------------
    async def start(self, *, run_scheduler: bool = True, check_capabilities: bool = True) -> None:
        """Check the model servers and capabilities (failing loudly), recover interrupted work, start the heartbeat."""
        try:
            await self._start(run_scheduler=run_scheduler, check_capabilities=check_capabilities)
        except BaseException:
            try:
                if self.container:
                    await self.container.stop()
            finally:
                await self._close_resources()
            raise

    async def _start(self, *, run_scheduler: bool, check_capabilities: bool) -> None:
        clients = [self.model]
        if self.config.sentinel.base_url.rstrip("/") != self.config.model.base_url.rstrip("/"):
            clients.append(self.sentinel_model)
        model_server = await self.model_server.ensure_ready(clients)
        await self.model.health()
        await self.sentinel_model.health()
        if check_capabilities:
            self.capabilities = await self.check_capabilities()
        for client in (self.model, self.sentinel_model):
            ctx = client.server_info.get("context_tokens")
            if ctx is not None and ctx < self.config.runtime.min_context_tokens:
                log.warning("%s %s has a %d-token context; %d or more is advisable", client.label,
                            client.model_name, ctx, self.config.runtime.min_context_tokens)
        if self.container:
            self.capabilities["sandbox"] = await self.container.start()
        if self.model_server.configured:
            if model_server["adopted"]:
                self.audit.record("model_server.adopted", f"supervising the model server an earlier Jig launched "
                                  f"and left running (pid {model_server['pid']})", actor="runtime",
                                  base_url=self.config.model.base_url, **model_server)
            elif model_server["already_running"]:
                self.audit.record("model_server.already_running", "model server already running; not launching",
                                  actor="runtime", base_url=self.config.model.base_url, model=self.model.model_name,
                                  capabilities_checked=check_capabilities)
            else:
                self.audit.record("model_server.launched", f"launched the model server (pid {model_server['pid']})",
                                  actor="runtime", base_url=self.config.model.base_url, **model_server)
        self.audit.record("runtime.start", "Jig started", actor="runtime", model=self.model.server_info,
                          sentinel=self.sentinel_model.server_info, capabilities=self.capabilities,
                          connection=self.connection(),
                          vault_backend=self.vault.backend, sandbox=str(self.sandbox.root),
                          start_reason=self.start_reason, model_server=model_server)
        self._recover()
        self._refresh_paused()
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

    async def stop(self, *, stop_request: dict[str, Any] | None = None) -> None:
        """Graceful shutdown. ``stop_request`` comes from 'Turn Jig off' (``POST /power/stop`` or ``jig stop``):
        with ``scope = "jig"`` a model server that Jig launched is left running for the next start to adopt;
        with ``"jig_and_model"``, or without a request (Ctrl+C, logoff), it is stopped."""
        if self._closed:
            return
        scope = (stop_request or {}).get("scope")
        if stop_request and stop_request.get("via") == "cli":  # the API records its own request before replying
            self.audit.record("power.stop", f"turn off requested ({scope})", actor="user", scope=scope, via="cli")
        await self.scheduler.stop()
        for t in list(self._background):
            t.cancel()
        await asyncio.gather(*self._background, return_exceptions=True)
        interrupted = self._mark_interrupted("Jig shut down")
        if self.container:
            await self.container.stop()
        if self.model_server.managed:
            pid = self.model_server.process.pid  # type: ignore[union-attr]
            if scope == "jig":
                await self.model_server.detach()
                self.audit.record("model_server.left_running", f"left the model server (pid {pid}) running, as asked; "
                                  "the next Jig start supervises it again", actor="runtime", pid=pid)
            else:
                await self.model_server.stop()
                self.audit.record("model_server.stopped", f"stopped the model server Jig launched (pid {pid})",
                                  actor="runtime", pid=pid, scope=scope)
        self.audit.record("runtime.stop", "Jig stopped", actor="runtime", interrupted_tasks=interrupted, scope=scope)
        await self._close_resources()

    async def _close_resources(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.model_server.stop()
            await self.http.aclose()
            await self.model.aclose()
            await self.sentinel_model.aclose()
            self.db.close()
        finally:
            self.instance_lock.release()

    def _mark_interrupted(self, why: str) -> list[str]:
        """Put tasks that were mid-run back in the queue (they resume from their checkpoint) and close the
        steps and chat runs that cannot resume, so nothing is left marked 'running'."""
        ids = []
        for status in (TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL):
            for task in self.store.list_tasks(status=status):
                self.set_task_status(task["id"], TaskStatus.QUEUED)
                self.audit.record("task.interrupted", f"{why}; the task is queued and resumes from its checkpoint",
                                  actor="runtime", task_id=task["id"], previous=status.value)
                ids.append(task["id"])
        self.db.execute("UPDATE run_steps SET status = 'interrupted', error = ?, finished_at = ? "
                        "WHERE status = 'running'", (why, now_iso()))
        for run in self.db.query("SELECT id FROM runs WHERE status = ? AND kind != 'task'", (RunStatus.RUNNING,)):
            self.store.finish_run(run["id"], status=RunStatus.FAILED, error=why)
        return ids

    def _recover(self) -> None:
        self.db.execute("UPDATE run_steps SET status = 'interrupted', error = 'interrupted by a restart', "
                        "finished_at = ? WHERE status = 'running'", (now_iso(),))
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
                              before=before, after=status.value, error_chars=text_size(fields.get("error")))
            self.publish_task(task)
            if TaskStatus.PAUSED in (before, status):
                self._refresh_paused()
        return task

    def _refresh_paused(self) -> None:
        self.tracker.set_paused(agent=self.scheduler.paused,
                                tasks=len(self.store.list_tasks(status=TaskStatus.PAUSED)))

    # Pause and resume ------------------------------------------------------
    async def _await_pause(self, runner: asyncio.Task[None], timeout: float) -> None:
        """Give a running task a moment to reach a safe point, so the reply usually shows the new status.
        A task in the middle of a tool call pauses as soon as that call finishes."""
        await asyncio.wait({runner}, timeout=timeout)

    async def pause_task(self, task_id: str, *, wait_s: float = 5.0) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        status = task["status"]
        if status == TaskStatus.QUEUED:
            self.audit.record("task.paused", "task paused by the user", actor="user", task_id=task_id)
            return self.set_task_status(task_id, TaskStatus.PAUSED)
        if status in (TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL):
            runner = self.scheduler.request_pause(task_id, reason="task")
            self.audit.record("task.pause_requested", "pause requested by the user; the task stops at its next "
                              "safe point", actor="user", task_id=task_id)
            await self._await_pause(runner, wait_s)
            return {**self.store.get_task(task_id), "pause_requested": True}
        raise ValueError(f"task {task_id} is {status} and cannot be paused")

    def resume_task(self, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task["status"] != TaskStatus.PAUSED:
            raise ValueError(f"task {task_id} is {task['status']}, not paused")
        self.audit.record("task.resumed", "task resumed by the user", actor="user", task_id=task_id)
        task = self.set_task_status(task_id, TaskStatus.QUEUED, error=None)
        self.scheduler.wake()
        return task

    def agent_status(self) -> dict[str, Any]:
        return {"paused": self.scheduler.paused, "running": self.scheduler.running_task_ids,
                "paused_tasks": [t["id"] for t in self.store.list_tasks(status=TaskStatus.PAUSED)]}

    async def pause_agent(self, *, wait_s: float = 5.0) -> dict[str, Any]:
        """Pause everything: schedules stop firing, no task starts, and running tasks stop at a safe point
        and go back to the queue. Chat still works, because the user is driving it directly."""
        if self.scheduler.paused:
            raise ValueError("the agent is already paused")
        runners = self.scheduler.running_tasks
        self.scheduler.set_paused(True)
        self.audit.record("agent.paused", "agent paused by the user", actor="user", interrupted=len(runners))
        self.bus.publish(EventType.AGENT_STATUS, paused=True)
        self._refresh_paused()
        if runners:
            await asyncio.wait(runners, timeout=wait_s)
        return self.agent_status()

    def resume_agent(self) -> dict[str, Any]:
        if not self.scheduler.paused:
            raise ValueError("the agent is not paused")
        self.scheduler.set_paused(False)
        self.audit.record("agent.resumed", "agent resumed by the user", actor="user")
        self.bus.publish(EventType.AGENT_STATUS, paused=False)
        self._refresh_paused()
        return self.agent_status()

    def create_task(self, *, title: str, description: str, mode: Mode, delay_s: float = 0.0) -> dict[str, Any]:
        task = self.store.create_task(title=title, description=description, mode=mode, delay_s=delay_s)
        self.audit.record("task.created", "task created", actor="user", task_id=task["id"], mode=task["mode"],
                          title_chars=len(title), description_chars=len(task["description"]))
        self.publish_task(task)
        self.scheduler.wake()
        return task

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task["status"] in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.BLOCKED):
            raise ValueError(f"task {task_id} is already {task['status']}")
        if not self.scheduler.cancel(task_id) and (run := self.store.resumable_run(task_id)):
            # A paused or re-queued task keeps a resumable run; close it so it is never picked up again.
            self.store.finish_run(run["id"], status=RunStatus.CANCELLED, error="cancelled by the user")
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
            result = dep["result"] or ""
            cut = (f"\n[Cut here: this is the first {DEPENDENCY_RESULT_CHARS} of {len(result)} characters of that "
                   "result.]" if len(result) > DEPENDENCY_RESULT_CHARS else "")
            parts.append(f"Result of earlier task '{dep['title']}':\n{result[:DEPENDENCY_RESULT_CHARS]}{cut}")
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
            messages = [{"role": "system", "content": self._system_prompt(mode)},
                        {"role": "user", "content": prompt}]
            run_id, resume = None, False

        async def on_wait(waiting: bool) -> None:
            self.set_task_status(task_id, TaskStatus.WAITING_APPROVAL if waiting else TaskStatus.RUNNING)

        spec = RunSpec(kind="task", mode=mode, intent=intent, task_id=task_id, run_id=run_id, resume=resume,
                       on_wait=on_wait, is_shutdown=lambda: self.scheduler.stopping,
                       pause=self.scheduler.pause_event(task_id))
        try:
            result = await self.agent.run(messages, spec)
        except RunPaused:
            # An explicit task pause parks the task; an agent-wide pause puts it back in the queue.
            if self.scheduler.pause_reason(task_id) == "task":
                self.set_task_status(task_id, TaskStatus.PAUSED)
            else:
                self.set_task_status(task_id, TaskStatus.QUEUED)
            return
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
        self.audit.record("goal.created", "goal created", actor="user", goal_id=goal["id"],
                          description_chars=len(goal["description"]))
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

    # Deleting job results ----------------------------------------------------
    def _still_stopping(self, task_ids: list[str]) -> None:
        """Refuse while a task marked finished (just stopped, say) still has its runner winding down."""
        running = set(self.scheduler.running_task_ids)
        if busy := sorted(t for t in task_ids if t in running
                          and self.store.get_task(t)["status"] in TERMINAL_TASK_STATUSES):
            raise CannotDelete(f"Jig is still stopping {', '.join(busy)}; try again in a moment")

    def delete_task(self, task_id: str) -> dict[str, Any]:
        self._still_stopping([task_id])
        return self.store.delete_task(task_id)

    def delete_goal(self, goal_id: str) -> dict[str, Any]:
        self._still_stopping([t["id"] for t in self.store.list_tasks(goal_id=goal_id, limit=10_000)])
        return self.store.delete_goal(goal_id)

    def wipe_jobs(self, *, vacuum: bool = True) -> dict[str, Any]:
        self._still_stopping(list(self.scheduler.running_task_ids))
        return self.store.wipe_jobs(vacuum=vacuum)

    def _history_changed(self, action: str, ids: set[str]) -> None:
        self.bus.forget(ids)
        self.bus.publish(EventType.HISTORY_CHANGED, action=action)

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
        messages = [{"role": "system", "content": self._system_prompt(mode)},
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
