"""The always-on heartbeat: turns due schedules into tasks and runs queued tasks concurrently."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .constants import TERMINAL_TASK_STATUSES, EventType, GoalStatus, Mode, TaskStatus
from .db import later_iso, now_iso

if TYPE_CHECKING:
    from .runtime import Jig

log = logging.getLogger(__name__)

_DEAD = {TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.BLOCKED}


class Scheduler:
    def __init__(self, jig: Jig, *, max_concurrent: int, heartbeat_s: float):
        self.jig = jig
        self.max_concurrent = max_concurrent
        self.heartbeat_s = heartbeat_s
        self.stopping = False
        # The agent-wide pause survives restarts; it is stored in the database.
        self.paused = jig.store.get_meta("agent_paused") == "1"
        self._running: dict[str, asyncio.Task[None]] = {}
        self._pause: dict[str, asyncio.Event] = {}
        self._pause_reason: dict[str, str] = {}
        self._wake = asyncio.Event()
        self._loop_task: asyncio.Task[None] | None = None
        self.last_tick: str | None = None

    @property
    def running_task_ids(self) -> list[str]:
        return list(self._running)

    @property
    def running_tasks(self) -> list[asyncio.Task[None]]:
        return list(self._running.values())

    def set_paused(self, paused: bool) -> None:
        """Pause or resume the whole agent: no schedules fire and no tasks start while paused."""
        self.paused = paused
        self.jig.store.set_meta("agent_paused", "1" if paused else "0")
        if paused:
            for task_id in list(self._running):
                self.request_pause(task_id, reason="agent")
        self.wake()

    def pause_event(self, task_id: str) -> asyncio.Event | None:
        return self._pause.get(task_id)

    def pause_reason(self, task_id: str) -> str | None:
        return self._pause_reason.get(task_id)

    def request_pause(self, task_id: str, *, reason: str) -> asyncio.Task[None]:
        """Ask a running task to stop at its next safe point. ``reason`` is 'task' or 'agent'."""
        t = self._running.get(task_id)
        if t is None:
            raise ValueError(f"task {task_id} is not running")
        if self._pause_reason.get(task_id) != "task":  # an explicit task pause wins over an agent pause
            self._pause_reason[task_id] = reason
        self._pause[task_id].set()
        return t

    def start(self) -> None:
        self._loop_task = asyncio.create_task(self._loop(), name="jig-heartbeat")

    def wake(self) -> None:
        self._wake.set()

    async def stop(self) -> None:
        self.stopping = True
        if self._loop_task:
            self._loop_task.cancel()
        tasks = list(self._running.values())
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, *([self._loop_task] if self._loop_task else []), return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as exc:
                # Keep the heartbeat alive but make the failure visible in the log and the audit trail.
                log.exception("scheduler tick failed")
                self.jig.audit.record("scheduler.error", f"heartbeat tick failed: {type(exc).__name__}",
                                      actor="scheduler", error=str(exc))
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.heartbeat_s)
            except TimeoutError:
                pass

    def tick(self) -> None:
        store, audit = self.jig.store, self.jig.audit
        self.last_tick = now_iso()

        if self.paused:
            self._update_goals()
            return

        for s in store.due_schedules():
            task = store.create_task(title=s["name"], description=s["prompt"], mode=Mode(s["mode"]),
                                     schedule_id=s["id"])
            store.update_schedule(s["id"], next_run_at=later_iso(s["interval_s"]), last_task_id=task["id"])
            audit.record("schedule.fired", f"schedule {s['name']!r} queued a task", actor="scheduler",
                         task_id=task["id"], schedule_id=s["id"])
            self.jig.publish_task(task)

        self.jig.tracker.set_monitoring(
            any(s["enabled"] and s["mode"] == Mode.RESEARCH for s in store.list_schedules())
        )

        for task in store.queued_due_tasks():
            deps = [store.get_task(d) for d in task["depends_on"]]
            if any(d["status"] in _DEAD for d in deps):
                self.jig.set_task_status(task["id"], TaskStatus.BLOCKED, error="a task it depends on did not complete")
                continue
            if not all(d["status"] == TaskStatus.DONE for d in deps):
                continue
            if len(self._running) >= self.max_concurrent:
                break
            self._launch(task["id"])

        self._update_goals()

    def _launch(self, task_id: str) -> None:
        self.jig.set_task_status(task_id, TaskStatus.RUNNING)
        self._pause[task_id] = asyncio.Event()
        t = asyncio.create_task(self.jig.run_task(task_id), name=f"jig-task-{task_id}")
        self._running[task_id] = t

        def done(_: asyncio.Task[None]) -> None:
            self._running.pop(task_id, None)
            self._pause.pop(task_id, None)
            self._pause_reason.pop(task_id, None)
            self.wake()

        t.add_done_callback(done)

    def cancel(self, task_id: str) -> bool:
        t = self._running.get(task_id)
        if t is None:
            return False
        t.cancel()
        return True

    def _update_goals(self) -> None:
        store = self.jig.store
        for goal in store.list_goals(status=GoalStatus.ACTIVE):
            tasks = store.list_tasks(goal_id=goal["id"])
            if not tasks or not all(t["status"] in TERMINAL_TASK_STATUSES for t in tasks):
                continue
            ok = all(t["status"] == TaskStatus.DONE for t in tasks)
            status = GoalStatus.DONE if ok else GoalStatus.FAILED
            result = tasks[-1]["result"] if ok else None
            error = None if ok else "; ".join(f"{t['title']}: {t['status']} {t['error'] or ''}".strip()
                                              for t in tasks if t["status"] != TaskStatus.DONE)
            store.update_goal(goal["id"], status=status, result=result, error=error)
            self.jig.audit.record("goal.status", f"goal {status}", actor="scheduler", goal_id=goal["id"],
                                  status=status.value, error=error)
            self.jig.bus.publish(EventType.GOAL_STATUS, goal_id=goal["id"], status=status.value, error=error)
