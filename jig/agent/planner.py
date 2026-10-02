"""Turns a goal into a plan of tasks, using the model with a strict JSON schema."""

from __future__ import annotations

import json
from typing import Any

from ..audit import AuditLog
from ..constants import EventType, GoalStatus, Mode, RunStatus
from ..errors import JigError, ModelError
from ..events import EventBus
from ..model import ModelClient
from ..store import Store
from ..tools.registry import ToolRegistry
from .prompts import PLAN_SCHEMA, PLANNER_SYSTEM_PROMPT


class Planner:
    def __init__(self, *, model: ModelClient, registry: ToolRegistry, store: Store, bus: EventBus, audit: AuditLog):
        self.model = model
        self.registry = registry
        self.store = store
        self.bus = bus
        self.audit = audit

    async def plan(self, goal_id: str) -> dict[str, Any]:
        goal = self.store.get_goal(goal_id)
        run_id = self.store.create_run(kind="plan", mode=Mode.RESEARCH, goal_id=goal_id)
        ids = {"run_id": run_id, "task_id": None, "goal_id": goal_id}
        self.bus.publish(EventType.RUN_START, kind="plan", mode=Mode.ACTION.value, **ids)
        tools = ", ".join(f"{t.name} ({t.effect.value})" for t in self.registry.available())
        messages = [
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT.format(tools=tools)},
            {"role": "user", "content": f"Goal: {goal['title']}\n\n{goal['description']}"},
        ]
        step_id = self.store.start_step(run_id, 1, "model_call", self.model.model_name, {"goal_id": goal_id})
        try:
            result = await self.model.chat(messages, response_schema=PLAN_SCHEMA)
            plan = self._validate(json.loads(result.content))
        except (JigError, json.JSONDecodeError, ValueError) as exc:
            error = f"{type(exc).__name__}: {exc}"
            self.store.finish_step(step_id, status="error", error=error)
            self.store.finish_run(run_id, status=RunStatus.FAILED, error=error)
            self.store.update_goal(goal_id, status=GoalStatus.FAILED, error=f"planning failed: {error}")
            self.audit.record("goal.plan_failed", "planning failed", **ids, error_type=type(exc).__name__,
                              error_chars=len(error))
            self.bus.publish(EventType.GOAL_STATUS, goal_id=goal_id, status=GoalStatus.FAILED.value, error=error)
            self.bus.publish(EventType.RUN_END, status="failed", error=error, **ids)
            raise
        self.store.finish_step(step_id, status="ok", output={"plan": plan, "reasoning": result.reasoning,
                                                             **result.summary()})
        task_ids: list[str] = []
        for item in plan["tasks"]:
            task = self.store.create_task(
                title=item["title"], description=item["description"], mode=Mode(item["mode"]), goal_id=goal_id,
                depends_on=[task_ids[i] for i in item["depends_on"]],
            )
            task_ids.append(task["id"])
        plan["task_ids"] = task_ids
        self.store.update_goal(goal_id, status=GoalStatus.ACTIVE, plan=plan)
        self.store.finish_run(run_id, status=RunStatus.DONE, final=json.dumps(plan))
        self.audit.record("goal.planned", f"goal planned into {len(task_ids)} tasks", **ids, task_ids=task_ids)
        self.bus.publish(EventType.GOAL_STATUS, goal_id=goal_id, status=GoalStatus.ACTIVE.value, plan=plan)
        self.bus.publish(EventType.RUN_END, status="done", **ids)
        return plan

    @staticmethod
    def _validate(plan: dict[str, Any]) -> dict[str, Any]:
        tasks = plan.get("tasks")
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= 5:
            raise ModelError("plan must contain between 1 and 5 tasks")
        for i, t in enumerate(tasks):
            if not t.get("title", "").strip() or not t.get("description", "").strip():
                raise ModelError(f"plan task {i} has an empty title or description")
            bad = [d for d in t["depends_on"] if not isinstance(d, int) or not 0 <= d < i]
            if bad:
                raise ModelError(f"plan task {i} depends on invalid tasks {bad}")
        return plan
