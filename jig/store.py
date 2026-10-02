"""Goals, tasks, schedules, runs, run steps, chat sessions and private notes."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .constants import GoalStatus, Mode, RunStatus, TaskStatus
from .db import Database, dumps, iso, later_iso, new_id, now, now_iso
from .errors import NotFound
from .recurrence import Recurrence


def _task_out(row: dict[str, Any]) -> dict[str, Any]:
    row["depends_on"] = json.loads(row.pop("depends_on_json"))
    return row


class Store:
    def __init__(self, db: Database, on_schedule_change: Callable[[str, str], None] | None = None,
                 on_note_change: Callable[[int | None, str], None] | None = None):
        self.db = db
        # Called with (schedule_id, "created" | "updated" | "deleted") after every change, whoever made it.
        self.on_schedule_change = on_schedule_change
        # Called with (note_id, "added" | "edited" | "deleted"), or (None, "wiped"), whoever made the change.
        self.on_note_change = on_note_change

    # Runtime settings ------------------------------------------------------
    def get_meta(self, key: str) -> str | None:
        row = self.db.one("SELECT value FROM meta WHERE key = ?", (key,))
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (key, value))

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

    def list_tasks(self, *, status: str | None = None, goal_id: str | None = None, limit: int = 200,
                   newest_first: bool = False) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM tasks WHERE 1=1", []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if goal_id:
            sql += " AND goal_id = ?"
            params.append(goal_id)
        sql += f" ORDER BY created_at {'DESC' if newest_first else 'ASC'}, rowid {'DESC' if newest_first else 'ASC'} LIMIT ?"
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
    @staticmethod
    def recurrence(schedule: dict[str, Any]) -> Recurrence:
        """The repeat of a schedule, from ``get_schedule`` or a raw row."""
        if "repeat" in schedule:
            repeat = schedule["repeat"]
        elif schedule.get("repeat_json"):
            repeat = json.loads(schedule["repeat_json"])
        else:
            repeat = {"kind": "interval", "interval_s": schedule["interval_s"]}
        return Recurrence(repeat, schedule.get("timezone"))

    def create_schedule(self, *, name: str, prompt: str, mode: Mode, interval_s: float | None = None,
                        repeat: dict[str, Any] | None = None, timezone: str | None = None,
                        start_in_s: float = 0.0, created_by: str = "user") -> dict[str, Any]:
        """``interval_s`` alone is the original interval schedule; ``repeat`` (see jig.recurrence) adds calendar
        times. An interval schedule first runs after ``start_in_s``; a calendar one at its next matching time."""
        if (interval_s is None) == (repeat is None):
            raise ValueError("give either interval_s or repeat, not both or neither")
        rec = Recurrence(repeat or {"kind": "interval", "interval_s": interval_s}, timezone)
        if rec.interval_s is None and start_in_s:
            raise ValueError("start_in_s only applies to interval schedules; a calendar schedule runs at its times")
        first = later_iso(start_in_s) if rec.interval_s is not None else iso(rec.next_after(now()))
        ts = now_iso()
        sid = new_id("s")
        self.db.execute(
            "INSERT INTO schedules(id, name, prompt, mode, interval_s, next_run_at, enabled, created_at, updated_at, "
            "repeat_json, timezone, created_by) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)",
            (sid, name, prompt, Mode(mode), rec.interval_s or 0.0, first, ts, ts, dumps(rec.repeat), rec.timezone,
             created_by),
        )
        self._schedule_changed(sid, "created")
        return self.get_schedule(sid)

    def get_schedule(self, schedule_id: str) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM schedules WHERE id = ?", (schedule_id,))
        if row is None:
            raise NotFound(f"schedule {schedule_id} does not exist")
        rec = self.recurrence(row)
        row.pop("repeat_json")
        row["enabled"] = bool(row["enabled"])
        row["repeat"] = rec.repeat
        row["timezone"] = rec.timezone
        row["repeat_text"] = rec.describe()
        row["last_task"] = self.db.one(
            "SELECT id, status, result, error, created_at, started_at, finished_at FROM tasks WHERE id = ?",
            (row["last_task_id"],)) if row["last_task_id"] else None
        return row

    def list_schedules(self) -> list[dict[str, Any]]:
        return [self.get_schedule(r["id"]) for r in self.db.query("SELECT id FROM schedules ORDER BY created_at")]

    def update_schedule(self, schedule_id: str, **fields: Any) -> dict[str, Any]:
        """Set columns as they are (the scheduler's bookkeeping). People's changes go through ``edit_schedule``."""
        self.get_schedule(schedule_id)
        if "enabled" in fields:
            fields["enabled"] = int(bool(fields["enabled"]))
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE schedules SET {cols} WHERE id = ?", (*fields.values(), schedule_id))
        self._schedule_changed(schedule_id, "updated")
        return self.get_schedule(schedule_id)

    def edit_schedule(self, schedule_id: str, *, name: str | None = None, prompt: str | None = None,
                      mode: Mode | None = None, enabled: bool | None = None, interval_s: float | None = None,
                      repeat: dict[str, Any] | None = None, timezone: str | None = None) -> dict[str, Any]:
        """Change a schedule. A new repeat or timezone takes effect from now; resuming a calendar schedule that
        missed its time while paused waits for its next time, while an overdue interval schedule runs at once."""
        current = self.get_schedule(schedule_id)
        if interval_s is not None and repeat is not None:
            raise ValueError("give either interval_s or repeat, not both")
        fields: dict[str, Any] = {k: v for k, v in (("name", name), ("prompt", prompt)) if v is not None}
        if mode is not None:
            fields["mode"] = Mode(mode)
        if interval_s is not None:
            repeat = {"kind": "interval", "interval_s": interval_s}
        if repeat is not None or timezone is not None:
            rec = Recurrence(repeat or current["repeat"], timezone or current["timezone"])
            fields |= {"repeat_json": dumps(rec.repeat), "timezone": rec.timezone, "interval_s": rec.interval_s or 0.0,
                       "next_run_at": iso(rec.next_after(now()))}
        if enabled is not None:
            fields["enabled"] = int(enabled)
            if enabled and not current["enabled"] and "next_run_at" not in fields:
                rec = self.recurrence(current)
                if rec.interval_s is None and current["next_run_at"] <= now_iso():
                    fields["next_run_at"] = iso(rec.next_after(now()))
        if not fields:
            return current
        return self.update_schedule(schedule_id, **fields)

    def delete_schedule(self, schedule_id: str) -> None:
        with self.db.transaction() as conn:
            # Jobs it already ran are kept; they just no longer point at a schedule that is gone.
            conn.execute("UPDATE tasks SET schedule_id = NULL WHERE schedule_id = ?", (schedule_id,))
            if conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,)).rowcount == 0:
                raise NotFound(f"schedule {schedule_id} does not exist")
        self._schedule_changed(schedule_id, "deleted")

    def _schedule_changed(self, schedule_id: str, action: str) -> None:
        if self.on_schedule_change:
            self.on_schedule_change(schedule_id, action)

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

    def list_runs(self, *, task_id: str | None = None, kind: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Run summaries, newest first, without messages or steps."""
        sql = ("SELECT id, kind, task_id, session_id, mode, status, steps, final, error, started_at, finished_at "
               "FROM runs WHERE 1=1")
        params: list[Any] = []
        if task_id:
            sql += " AND task_id = ?"
            params.append(task_id)
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        sql += " ORDER BY started_at DESC, rowid DESC LIMIT ?"
        params.append(limit)
        return self.db.query(sql, tuple(params))

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
    # Notes have no search index; deleting one removes the row, and secure_delete zeroes the pages it used.
    def add_note(self, *, title: str, body: str, task_id: str | None) -> dict[str, Any]:
        cur = self.db.execute(
            "INSERT INTO notes(task_id, title, body, created_at) VALUES (?, ?, ?, ?)",
            (task_id, title, body, now_iso()),
        )
        self._note_changed(cur.lastrowid, "added")
        return self.get_note(cur.lastrowid)

    def get_note(self, note_id: int) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM notes WHERE id = ?", (note_id,))
        if row is None:
            raise NotFound(f"note {note_id} does not exist")
        return row

    def list_notes(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.db.query("SELECT * FROM notes ORDER BY id DESC LIMIT ?", (limit,))

    def count_notes(self) -> int:
        return int(self.db.one("SELECT COUNT(*) AS n FROM notes")["n"])  # type: ignore[index]

    def edit_note(self, note_id: int, *, title: str | None = None, body: str | None = None) -> dict[str, Any]:
        fields = {k: v.strip() for k, v in (("title", title), ("body", body)) if v is not None}
        if empty := [k for k, v in fields.items() if not v]:
            raise ValueError(f"a note's {' and '.join(empty)} must not be empty")
        if not fields:
            return self.get_note(note_id)
        sets = ", ".join(f"{k} = ?" for k in fields)
        cur = self.db.execute(f"UPDATE notes SET {sets}, updated_at = ? WHERE id = ?",
                              (*fields.values(), now_iso(), note_id))
        if cur.rowcount == 0:
            raise NotFound(f"note {note_id} does not exist")
        self.db.checkpoint()
        self._note_changed(note_id, "edited")
        return self.get_note(note_id)

    def delete_note(self, note_id: int) -> None:
        if self.db.execute("DELETE FROM notes WHERE id = ?", (note_id,)).rowcount == 0:
            raise NotFound(f"note {note_id} does not exist")
        self.db.checkpoint()
        self._note_changed(note_id, "deleted")

    def wipe_notes(self) -> dict[str, Any]:
        """Delete every note. ``wal_cleared`` is as for ``Database.checkpoint``."""
        with self.db.transaction() as conn:
            n = int(conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0])
            conn.execute("DELETE FROM notes")
        cleared = self.db.checkpoint()
        self._note_changed(None, "wiped")
        return {"deleted": n, "wal_cleared": cleared}

    def _note_changed(self, note_id: int | None, action: str) -> None:
        if self.on_note_change:
            self.on_note_change(note_id, action)
