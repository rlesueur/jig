"""The always-on heartbeat: turns due schedules into tasks and runs queued tasks concurrently."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING

from .constants import TERMINAL_TASK_STATUSES, EventType, GoalStatus, Mode, TaskStatus
from .db import iso, now, now_iso

if TYPE_CHECKING:
    from .runtime import Jig

log = logging.getLogger(__name__)

_DEAD = {TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.BLOCKED}
# A gap between heartbeats larger than this (or 5 heartbeats, if longer) counts as a clock jump.
CLOCK_JUMP_S = 60.0


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


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
        self._last_clock: tuple[float, float] | None = None

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
                                      actor="scheduler", error_chars=len(str(exc)))
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.heartbeat_s)
            except TimeoutError:
                pass

    def _check_clock(self) -> None:
        """Detect sleep, hibernation or a clock change between ticks, and keep schedules sane afterwards.

        Overdue schedules are not replayed once per missed run: each fires once on the next tick
        (with the number of missed runs in its audit entry) and then continues from now. If the clock
        went backwards, schedules that now look far in the future are brought back to their next run from
        now (one interval away, or the next calendar time).
        """
        wall, mono = time.time(), time.monotonic()
        last, self._last_clock = self._last_clock, (wall, mono)
        if last is None:
            return
        wall_gap, mono_gap = wall - last[0], mono - last[1]
        if -CLOCK_JUMP_S < wall_gap < max(CLOCK_JUMP_S, 5 * self.heartbeat_s):
            return
        store = self.jig.store
        overdue = [s["name"] for s in store.due_schedules()]
        pulled_back = []
        for s in store.list_schedules():
            if not s["enabled"]:
                continue
            soonest = store.recurrence(s).next_after(now())
            if _parse(s["next_run_at"]) > soonest:
                store.update_schedule(s["id"], next_run_at=iso(soonest))
                pulled_back.append(s["name"])
        # Wall time far ahead of monotonic time means the clock was changed rather than the machine sleeping;
        # on some platforms the monotonic clock also runs during sleep, so this is a hint, not a certainty.
        cause = "clock moved backwards" if wall_gap < 0 else (
            "clock changed" if wall_gap - mono_gap > CLOCK_JUMP_S else "sleep, hibernation or a stall")
        log.warning("clock jump of %.0fs between heartbeats (%s); %d schedule(s) overdue", wall_gap, cause,
                    len(overdue))
        self.jig.audit.record("scheduler.clock_jump", f"{wall_gap:.0f}s gap between heartbeats ({cause})",
                              actor="scheduler", wall_gap_s=round(wall_gap, 1), monotonic_gap_s=round(mono_gap, 1),
                              cause=cause, overdue_schedules=overdue, rescheduled=pulled_back)

    def tick(self) -> None:
        store, audit = self.jig.store, self.jig.audit
        self.last_tick = now_iso()
        self._check_clock()

        if self.paused:
            self._update_goals()
            return

        for s in store.due_schedules():
            task = store.create_task(title=s["name"], description=s["prompt"], mode=Mode(s["mode"]),
                                     schedule_id=s["id"])
            rec, at = store.recurrence(s), now()
            missed = rec.missed_between(_parse(s["next_run_at"]), at)
            store.update_schedule(s["id"], next_run_at=iso(rec.next_after(at)), last_task_id=task["id"])
            audit.record("schedule.fired", f"schedule {s['name']!r} queued a task", actor="scheduler",
                         task_id=task["id"], schedule_id=s["id"], missed_runs=missed)
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
                                  status=status.value, error_chars=len(error) if error else None)
            self.jig.bus.publish(EventType.GOAL_STATUS, goal_id=goal["id"], status=status.value, error=error)
