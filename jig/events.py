"""In-process event bus and the derived avatar state.

Every interesting thing the runtime does is published here. WebSocket and SSE
clients at ``/events`` receive them, and ``AvatarStateTracker`` folds them into
a single avatar state for the avatar component.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .constants import TRANSIENT_STATE_SECONDS, AvatarState, Effect, EventType, Mode, TaskVariant
from .db import now_iso

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Event:
    seq: int
    type: str
    ts: str
    data: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "type": self.type, "ts": self.ts, "data": self.data}


class SubscriberOverflow(Exception):
    """A subscriber fell too far behind and was disconnected."""


class Subscription:
    def __init__(self, bus: EventBus, maxsize: int):
        self._bus = bus
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue(maxsize=maxsize)
        self.overflowed = False

    def _offer(self, event: Event) -> None:
        if self.overflowed:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            # Do not silently drop events: mark the subscriber as broken so
            # its consumer is disconnected with an explicit error.
            self.overflowed = True
            self._queue = asyncio.Queue(maxsize=1)
            self._queue.put_nowait(None)

    async def get(self) -> Event:
        event = await self._queue.get()
        if event is None:
            raise SubscriberOverflow("event subscriber fell behind and was disconnected")
        return event

    def close(self) -> None:
        self._bus._subscribers.discard(self)


class EventBus:
    def __init__(self, history: int = 500):
        self._seq = itertools.count(1)
        self._subscribers: set[Subscription] = set()
        self._listeners: list[Callable[[Event], None]] = []
        self.recent: deque[Event] = deque(maxlen=history)

    def subscribe(self, maxsize: int = 2000) -> Subscription:
        sub = Subscription(self, maxsize)
        self._subscribers.add(sub)
        return sub

    def add_listener(self, fn: Callable[[Event], None]) -> None:
        self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[Event], None]) -> None:
        self._listeners.remove(fn)

    def publish(self, type_: str, **data: Any) -> Event:
        event = Event(seq=next(self._seq), type=str(type_), ts=now_iso(), data=data)
        self.recent.append(event)
        for fn in self._listeners:
            fn(event)
        for sub in list(self._subscribers):
            sub._offer(event)
        return event


@dataclass
class _Activity:
    run_id: str
    kind: str  # chat | task | plan
    mode: str
    task_id: str | None
    phase: AvatarState = AvatarState.THINKING
    variant: str | None = None
    pending_approvals: set[str] = field(default_factory=set)
    updated: float = field(default_factory=time.monotonic)

    @property
    def background(self) -> bool:
        return self.mode == Mode.RESEARCH and self.kind == "task"


_FOREGROUND_PRIORITY = {
    AvatarState.TALKING: 3,
    AvatarState.WORKING: 2,
    AvatarState.THINKING: 1,
}


class AvatarStateTracker:
    """Derives one avatar state from all concurrent activity.

    Precedence: approval, then a transient success/error, then the most salient
    foreground activity (talking > working > thinking), then paused (the agent
    or a task is paused), then running background research (``working`` with
    ``background: true``, as ``browsing`` unless it is using a tool of another
    kind), then monitoring (active research schedules), then idle.
    """

    def __init__(self, bus: EventBus):
        self.bus = bus
        self._activities: dict[str, _Activity] = {}
        self._transient: tuple[AvatarState, float, dict[str, Any]] | None = None
        self._monitoring = False
        self._agent_paused = False
        self._paused_tasks = 0
        self._current: dict[str, Any] = {"state": AvatarState.IDLE.value, "variant": None, "background": False}
        self._timer: asyncio.TimerHandle | None = None
        bus.add_listener(self._on_event)

    @property
    def current(self) -> dict[str, Any]:
        return dict(self._current)

    def set_monitoring(self, monitoring: bool) -> None:
        if monitoring != self._monitoring:
            self._monitoring = monitoring
            self._recompute()

    def set_paused(self, *, agent: bool, tasks: int) -> None:
        if (agent, tasks) != (self._agent_paused, self._paused_tasks):
            self._agent_paused, self._paused_tasks = agent, tasks
            self._recompute()

    def _on_event(self, event: Event) -> None:
        if event.type == EventType.AVATAR_STATE:
            return
        d = event.data
        run_id = d.get("run_id")
        act = self._activities.get(run_id) if run_id else None
        t = event.type
        if t == EventType.RUN_START and run_id:
            self._activities[run_id] = _Activity(
                run_id=run_id, kind=d.get("kind", "task"), mode=d.get("mode", Mode.ACTION),
                task_id=d.get("task_id"),
            )
        elif act is None:
            return
        elif t == EventType.MODEL_END and act.phase == AvatarState.TALKING and not d.get("tool_calls"):
            return  # the final answer has just been spoken; stay talking until the run ends
        elif t in (EventType.MODEL_START, EventType.TOOL_END, EventType.MODEL_END):
            act.phase, act.variant = AvatarState.THINKING, None
        elif t == EventType.CHAT_DELTA and d.get("kind") == "content":
            if act.phase != AvatarState.TALKING:
                act.phase, act.variant = AvatarState.TALKING, None
            else:
                return
        elif t in (EventType.TOOL_START, EventType.TOOL_CHECKOUT):
            variant = d.get("variant")
            if act.background and d.get("effect") == Effect.READ:
                variant = TaskVariant.BROWSING.value  # background fetching or reading shows dimmed browsing
            act.phase, act.variant = AvatarState.WORKING, variant
        elif t == EventType.APPROVAL_REQUESTED:
            act.pending_approvals.add(d["approval_id"])
        elif t == EventType.APPROVAL_RESOLVED:
            act.pending_approvals.discard(d["approval_id"])
        elif t == EventType.RUN_END:
            del self._activities[run_id]
            status = d.get("status")
            if status == "failed":
                self._set_transient(AvatarState.ERROR, d)
            elif status == "done" and not act.background and act.kind != "plan":
                self._set_transient(AvatarState.SUCCESS, d)
        else:
            return
        if act is not None:
            act.updated = time.monotonic()
        self._recompute()

    def _set_transient(self, state: AvatarState, d: dict[str, Any]) -> None:
        self._transient = (
            state,
            time.monotonic() + TRANSIENT_STATE_SECONDS,
            {"run_id": d.get("run_id"), "task_id": d.get("task_id")},
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._timer:
            self._timer.cancel()
        self._timer = loop.call_later(TRANSIENT_STATE_SECONDS + 0.05, self._recompute)

    def _derive(self) -> dict[str, Any]:
        acts = list(self._activities.values())
        waiting = [a for a in acts if a.pending_approvals]
        if waiting:
            a = max(waiting, key=lambda a: a.updated)
            return self._state(AvatarState.APPROVAL, None, a, pending=sum(len(w.pending_approvals) for w in waiting))
        if self._transient and self._transient[1] > time.monotonic():
            state, _, ctx = self._transient
            return {"state": state.value, "variant": None, "background": False, **ctx}
        self._transient = None
        foreground = [a for a in acts if not a.background]
        if foreground:
            a = max(foreground, key=lambda a: (_FOREGROUND_PRIORITY[a.phase], a.updated))
            return self._state(a.phase, a.variant, a)
        if self._agent_paused or self._paused_tasks:
            return self._idle(AvatarState.PAUSED, agent_paused=self._agent_paused, paused_tasks=self._paused_tasks)
        if acts:
            a = max(acts, key=lambda a: (a.phase == AvatarState.WORKING, a.updated))
            variant = a.variant if a.phase == AvatarState.WORKING else TaskVariant.BROWSING.value
            return self._state(AvatarState.WORKING, variant, a, background=True)
        if self._monitoring:
            return self._idle(AvatarState.MONITORING)
        return self._idle(AvatarState.IDLE)

    def _state(self, state: AvatarState, variant: str | None, a: _Activity, *, background: bool = False,
               **extra: Any) -> dict[str, Any]:
        return {"state": state.value, "variant": variant, "background": background, "run_id": a.run_id,
                "task_id": a.task_id, **extra}

    @staticmethod
    def _idle(state: AvatarState, **extra: Any) -> dict[str, Any]:
        return {"state": state.value, "variant": None, "background": False, "run_id": None, "task_id": None, **extra}

    def _recompute(self) -> None:
        derived = self._derive()
        derived["active"] = len(self._activities)
        key = (derived["state"], derived["variant"], derived["background"])
        if key != (self._current["state"], self._current["variant"], self._current["background"]):
            self._current = derived
            self.bus.publish(EventType.AVATAR_STATE, **derived)
        else:
            self._current = derived
