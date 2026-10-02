"""Append-only audit log. Updates and deletes are rejected by SQLite triggers.

The log never holds what was said: no message, tool argument, tool result, error text, Sentinel reason or
approval note, because those can quote the user and the log can't be edited. It keeps ids, sizes and decisions,
so deleting a conversation or job (or forgetting a memory or note) removes its words from Jig's database, and
the log still shows what happened and when. Entries written before this rule (older Jig versions) are left as
they are: see ``older_entries_with_content``.
"""

from __future__ import annotations

from typing import Any

from .db import Database, dumps, now_iso

# Kinds whose entries, before the rule above, could carry text from a conversation or job.
_KINDS_THAT_HELD_CONTENT = (
    "tool.call", "tool.result", "tool.error", "policy.url_normalised", "policy.resolved", "policy.connector_limit",
    "policy.decision", "sentinel.verdict", "sentinel.error", "approval.requested", "approval.resolved", "run.end",
    "model.call", "task.created", "task.status", "goal.created", "goal.planned", "goal.plan_failed", "goal.status",
    "scheduler.error",
)
_CONTENT_FREE_FROM = "audit_content_free_from"


def _is_id(key: str, value: Any) -> bool:
    return (key == "id" or key.endswith(("_id", "_ids"))) and (
        isinstance(value, str) and len(value) <= 100
        or isinstance(value, list) and all(isinstance(v, (str, int)) for v in value))


def sizes(args: dict[str, Any]) -> dict[str, Any]:
    """Tool arguments as the audit log keeps them: numbers, flags and ids as they are, anything else only as its
    size, so ``{"query": "my sister's flat"}`` becomes ``{"query": "[17 characters]"}``."""
    return {k: v if v is None or isinstance(v, (bool, int, float)) or _is_id(k, v) else size(v) for k, v in args.items()}


def size(value: Any) -> str:
    if isinstance(value, str):
        return f"[{len(value)} characters]"
    if isinstance(value, (list, dict)):
        return f"[{len(value)} items]"
    return "[1 value]"


def result_shape(result: Any) -> dict[str, Any]:
    """A tool result as the audit log keeps it: its size and the ids of what it touched, never its text."""
    out: dict[str, Any] = {"chars": len(dumps(result))}
    if isinstance(result, dict):
        if isinstance(result.get("id"), (str, int)):
            out["id"] = result["id"]
        for value in result.values():
            if isinstance(value, list):
                out["items"] = len(value)
                ids = [x["id"] for x in value if isinstance(x, dict) and isinstance(x.get("id"), (str, int))]
                if ids:
                    out["ids"] = ids[:50]
                break
    elif isinstance(result, list):
        out["items"] = len(result)
    return out


def text_size(text: str | None) -> int | None:
    return None if text is None else len(text)


class AuditLog:
    def __init__(self, db: Database):
        self.db = db
        # The first entry written under the no-content rule; on a new database, the very first entry.
        db.execute("INSERT OR IGNORE INTO meta(key, value) VALUES (?, (SELECT COALESCE(MAX(id), 0) + 1 FROM audit))",
                   (_CONTENT_FREE_FROM,))

    def older_entries_with_content(self) -> dict[str, Any]:
        """Entries from before the no-content rule whose kind could carry conversation or job text: how many, and
        from when to when. They are not rewritten; the log is append-only."""
        start = int(self.db.one("SELECT value FROM meta WHERE key = ?", (_CONTENT_FREE_FROM,))["value"])  # type: ignore[index]
        marks = ",".join("?" * len(_KINDS_THAT_HELD_CONTENT))
        row = self.db.one(f"SELECT COUNT(*) AS n, MIN(ts) AS first, MAX(ts) AS last FROM audit "
                          f"WHERE id < ? AND kind IN ({marks})", (start, *_KINDS_THAT_HELD_CONTENT))
        return {"count": row["n"], "first": row["first"], "last": row["last"], "before_id": start}  # type: ignore[index]

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
