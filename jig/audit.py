"""Append-only audit log. Updates and deletes are rejected by SQLite triggers."""

from __future__ import annotations

from typing import Any

from .db import Database, dumps, now_iso


class AuditLog:
    def __init__(self, db: Database):
        self.db = db

    def record(
        self,
        kind: str,
        summary: str,
        *,
        actor: str = "agent",
        task_id: str | None = None,
        run_id: str | None = None,
        **data: Any,
    ) -> int:
        cur = self.db.execute(
            "INSERT INTO audit(ts, kind, actor, task_id, run_id, summary, data_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now_iso(), kind, actor, task_id, run_id, summary, dumps(data)),
        )
        return int(cur.lastrowid)

    def query(
        self,
        *,
        kind: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
        after_id: int = 0,
        before_id: int | None = None,
        newest_first: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM audit WHERE id > ?"
        params: list[Any] = [after_id]
        if before_id is not None:
            sql += " AND id < ?"
            params.append(before_id)
        if kind:
            # 'tool' matches 'tool.call', 'tool.result' and so on.
            sql += " AND (kind = ? OR kind LIKE ?)"
            params += [kind, f"{kind}.%"]
        if task_id:
            sql += " AND task_id = ?"
            params.append(task_id)
        if run_id:
            sql += " AND run_id = ?"
            params.append(run_id)
        sql += f" ORDER BY id {'DESC' if newest_first else 'ASC'} LIMIT ?"
        params.append(limit)
        return self.db.query(sql, tuple(params))
