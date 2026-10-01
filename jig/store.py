"""Goals, tasks, schedules, runs, run steps, chat sessions and private notes."""

from __future__ import annotations

import json
from typing import Any

from .constants import GoalStatus, Mode, RunStatus, TaskStatus
from .db import Database, dumps, later_iso, new_id, now_iso
from .errors import NotFound


def _task_out(row: dict[str, Any]) -> dict[str, Any]:
    row["depends_on"] = json.loads(row.pop("depends_on_json"))
    return row


class Store:
    def __init__(self, db: Database):
        self.db = db

    # Goals -----------------------------------------------------------------
    def create_goal(self, *, title: str, description: str) -> dict[str, Any]:
        ts = now_iso()
        gid = new_id("g")
        self.db.execute(
            "INSERT INTO goals(id, title, description, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (gid, title, description, GoalStatus.PLANNING, ts, ts),
        )
        return self.get_goal(gid)

    def get_goal(self, goal_id: str) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM goals WHERE id = ?", (goal_id,))
        if row is None:
            raise NotFound(f"goal {goal_id} does not exist")
        row["plan"] = json.loads(row.pop("plan_json")) if row["plan_json"] else None
        return row

    def list_goals(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if status:
            rows = self.db.query("SELECT id FROM goals WHERE status = ? ORDER BY created_at DESC LIMIT ?", (status, limit))
        else:
            rows = self.db.query("SELECT id FROM goals ORDER BY created_at DESC LIMIT ?", (limit,))
        return [self.get_goal(r["id"]) for r in rows]

    def update_goal(self, goal_id: str, **fields: Any) -> dict[str, Any]:
        if "plan" in fields:
            fields["plan_json"] = dumps(fields.pop("plan"))
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE goals SET {cols} WHERE id = ?", (*fields.values(), goal_id))
        return self.get_goal(goal_id)

    # Tasks -----------------------------------------------------------------
    def create_task(
        self,
        *,
        title: str,
        description: str,
        mode: Mode,
        goal_id: str | None = None,
        schedule_id: str | None = None,
        depends_on: list[str] | None = None,
        delay_s: float = 0.0,
    ) -> dict[str, Any]:
        ts = now_iso()
        tid = new_id("t")
        self.db.execute(
            "INSERT INTO tasks(id, goal_id, schedule_id, title, description, mode, status, depends_on_json, "
            "run_after, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (tid, goal_id, schedule_id, title, description, Mode(mode), TaskStatus.QUEUED,
             dumps(depends_on or []), later_iso(delay_s) if delay_s else ts, ts, ts),
        )
        return self.get_task(tid)

    def get_task(self, task_id: str) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if row is None:
            raise NotFound(f"task {task_id} does not exist")
        return _task_out(row)

    def list_tasks(self, *, status: str | None = None, goal_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM tasks WHERE 1=1", []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if goal_id:
            sql += " AND goal_id = ?"
            params.append(goal_id)
        sql += " ORDER BY created_at, rowid LIMIT ?"
        params.append(limit)
        return [_task_out(r) for r in self.db.query(sql, tuple(params))]

    def update_task(self, task_id: str, **fields: Any) -> dict[str, Any]:
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE tasks SET {cols} WHERE id = ?", (*fields.values(), task_id))
        return self.get_task(task_id)

    def queued_due_tasks(self) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM tasks WHERE status = ? AND run_after <= ? ORDER BY created_at, rowid",
            (TaskStatus.QUEUED, now_iso()),
        )
        return [_task_out(r) for r in rows]

    # Schedules -------------------------------------------------------------
    def create_schedule(self, *, name: str, prompt: str, mode: Mode, interval_s: float,
                        start_in_s: float = 0.0) -> dict[str, Any]:
        if interval_s < 30:
            raise ValueError("interval_s must be at least 30 seconds")
        ts = now_iso()
        sid = new_id("s")
        self.db.execute(
            "INSERT INTO schedules(id, name, prompt, mode, interval_s, next_run_at, enabled, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (sid, name, prompt, Mode(mode), interval_s, later_iso(start_in_s), ts, ts),
        )
        return self.get_schedule(sid)

    def get_schedule(self, schedule_id: str) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM schedules WHERE id = ?", (schedule_id,))
        if row is None:
            raise NotFound(f"schedule {schedule_id} does not exist")
        row["enabled"] = bool(row["enabled"])
        return row

    def list_schedules(self) -> list[dict[str, Any]]:
        return [self.get_schedule(r["id"]) for r in self.db.query("SELECT id FROM schedules ORDER BY created_at")]

    def update_schedule(self, schedule_id: str, **fields: Any) -> dict[str, Any]:
        self.get_schedule(schedule_id)
        if "enabled" in fields:
            fields["enabled"] = int(bool(fields["enabled"]))
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE schedules SET {cols} WHERE id = ?", (*fields.values(), schedule_id))
        return self.get_schedule(schedule_id)

    def delete_schedule(self, schedule_id: str) -> None:
        if self.db.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,)).rowcount == 0:
            raise NotFound(f"schedule {schedule_id} does not exist")

    def due_schedules(self) -> list[dict[str, Any]]:
        return self.db.query("SELECT * FROM schedules WHERE enabled = 1 AND next_run_at <= ?", (now_iso(),))

    # Runs and steps --------------------------------------------------------
    def create_run(self, *, kind: str, mode: Mode, task_id: str | None = None,
                   session_id: str | None = None) -> str:
        rid = new_id("r")
        self.db.execute(
            "INSERT INTO runs(id, kind, task_id, session_id, mode, status, started_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (rid, kind, task_id, session_id, Mode(mode), RunStatus.RUNNING, now_iso()),
        )
        return rid

    def checkpoint_run(self, run_id: str, messages: list[dict[str, Any]], steps: int) -> None:
        self.db.execute("UPDATE runs SET messages_json = ?, steps = ? WHERE id = ?", (dumps(messages), steps, run_id))

    def finish_run(self, run_id: str, *, status: RunStatus, final: str | None = None, error: str | None = None) -> None:
        self.db.execute(
            "UPDATE runs SET status = ?, final = ?, error = ?, finished_at = ? WHERE id = ?",
            (status, final, error, now_iso(), run_id),
        )

    def get_run(self, run_id: str, *, with_steps: bool = True) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM runs WHERE id = ?", (run_id,))
        if row is None:
            raise NotFound(f"run {run_id} does not exist")
        row["messages"] = json.loads(row.pop("messages_json"))
        if with_steps:
            row["step_records"] = self.list_steps(run_id)
        return row

    def runs_for_task(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT id FROM runs WHERE task_id = ? ORDER BY started_at", (task_id,))
        return [self.get_run(r["id"]) for r in rows]

    def resumable_run(self, task_id: str) -> dict[str, Any] | None:
        row = self.db.one(
            "SELECT id FROM runs WHERE task_id = ? AND status = ? ORDER BY started_at DESC LIMIT 1",
            (task_id, RunStatus.RUNNING),
        )
        return self.get_run(row["id"], with_steps=False) if row else None

    def start_step(self, run_id: str, idx: int, type_: str, name: str | None, input_: Any) -> int:
        cur = self.db.execute(
            "INSERT INTO run_steps(run_id, idx, type, name, input_json, status, started_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, idx, type_, name, dumps(input_), "running", now_iso()),
        )
        return int(cur.lastrowid)

    def finish_step(self, step_id: int, *, status: str, output: Any = None, error: str | None = None) -> None:
        self.db.execute(
            "UPDATE run_steps SET status = ?, output_json = ?, error = ?, finished_at = ? WHERE id = ?",
            (status, dumps(output), error, now_iso(), step_id),
        )

    def list_steps(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM run_steps WHERE run_id = ? ORDER BY id", (run_id,))
        for r in rows:
            r["input"] = json.loads(r.pop("input_json") or "null")
            r["output"] = json.loads(r.pop("output_json") or "null")
        return rows

    # Chat sessions ---------------------------------------------------------
    def get_session(self, session_id: str) -> list[dict[str, Any]]:
        row = self.db.one("SELECT messages_json FROM sessions WHERE id = ?", (session_id,))
        if row is None:
            raise NotFound(f"session {session_id} does not exist")
        return json.loads(row["messages_json"])

    def save_session(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        ts = now_iso()
        self.db.execute(
            "INSERT INTO sessions(id, messages_json, created_at, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET messages_json = excluded.messages_json, updated_at = excluded.updated_at",
            (session_id, dumps(messages), ts, ts),
        )

    # Private notes ---------------------------------------------------------
    def add_note(self, *, title: str, body: str, task_id: str | None) -> dict[str, Any]:
        cur = self.db.execute(
            "INSERT INTO notes(task_id, title, body, created_at) VALUES (?, ?, ?, ?)",
            (task_id, title, body, now_iso()),
        )
        return self.db.one("SELECT * FROM notes WHERE id = ?", (cur.lastrowid,)) or {}

    def list_notes(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.db.query("SELECT * FROM notes ORDER BY id DESC LIMIT ?", (limit,))
