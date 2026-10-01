"""SQLite database: one local file holding all of Jig's state.

All access goes through a single connection guarded by a lock. Statements are
short, so they run inline on the event loop; this is a deliberate simplicity
trade-off for the foundations.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS goals (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL,
    plan_json TEXT,
    result TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    goal_id TEXT REFERENCES goals(id),
    schedule_id TEXT REFERENCES schedules(id),
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    depends_on_json TEXT NOT NULL DEFAULT '[]',
    run_after TEXT NOT NULL,
    result TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status, run_after);

CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    prompt TEXT NOT NULL,
    mode TEXT NOT NULL,
    interval_s REAL NOT NULL,
    next_run_at TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_task_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    messages_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    task_id TEXT REFERENCES tasks(id),
    session_id TEXT,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    messages_json TEXT NOT NULL DEFAULT '[]',
    final TEXT,
    error TEXT,
    steps INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS run_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id),
    idx INTEGER NOT NULL,
    type TEXT NOT NULL,
    name TEXT,
    input_json TEXT,
    output_json TEXT,
    status TEXT NOT NULL,
    error TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS run_steps_run ON run_steps(run_id, idx);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    task_id TEXT,
    run_id TEXT,
    tool_call_id TEXT,
    tool TEXT NOT NULL,
    args_json TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    sentinel_json TEXT,
    status TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS approvals_status ON approvals(status);
CREATE UNIQUE INDEX IF NOT EXISTS approvals_call ON approvals(run_id, tool_call_id);

CREATE TABLE IF NOT EXISTS rules (
    id TEXT PRIMARY KEY,
    tool TEXT NOT NULL,
    arg TEXT,
    pattern TEXT,
    decision TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'fact',
    tags TEXT NOT NULL DEFAULT '',
    source TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content, tags, content='memories', content_rowid='id', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, tags) VALUES (new.id, new.content, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, tags)
    VALUES ('delete', old.id, old.content, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, tags)
    VALUES ('delete', old.id, old.content, old.tags);
    INSERT INTO memories_fts(rowid, content, tags) VALUES (new.id, new.content, new.tags);
END;

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    actor TEXT NOT NULL,
    task_id TEXT,
    run_id TEXT,
    summary TEXT NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS audit_kind ON audit(kind, id);
CREATE INDEX IF NOT EXISTS audit_task ON audit(task_id, id);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN
    SELECT RAISE(ABORT, 'audit log is append-only');
END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN
    SELECT RAISE(ABORT, 'audit log is append-only');
END;

CREATE TABLE IF NOT EXISTS secrets (
    name TEXT PRIMARY KEY,
    backend TEXT NOT NULL,
    ciphertext BLOB,
    allowed_tools_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def now() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="milliseconds")


def now_iso() -> str:
    return iso(now())


def later_iso(seconds: float) -> str:
    return iso(now() + timedelta(seconds=seconds))


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA)
        self._conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")
