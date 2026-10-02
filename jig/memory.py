"""Local, inspectable, editable long-term memory.

Storage lives in SQLite. Search goes through a pluggable ``SearchBackend``;
the default uses FTS5, and an embedding backend can be added later.
Forgetting is a hard delete: the content is removed from the table and the
index, and the audit log records only the memory's id, never its content.
``wipe`` forgets everything at once. There are no embeddings; FTS5 is the only index.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, Protocol

from .db import Database, now_iso
from .errors import NotFound


class SearchBackend(Protocol):
    def search(self, query: str, limit: int) -> list[tuple[int, float]]:
        """Return (memory_id, score) pairs, best first."""


class FTS5Backend:
    """Full-text search over the ``memories_fts`` index (kept in sync by triggers)."""

    _TOKEN = re.compile(r"\w+", re.UNICODE)

    def __init__(self, db: Database):
        self.db = db

    def search(self, query: str, limit: int) -> list[tuple[int, float]]:
        tokens = self._TOKEN.findall(query)
        if not tokens:
            return []
        # Quote every token so user text can never inject FTS5 syntax.
        match = " OR ".join(f'"{t}"' for t in tokens)
        rows = self.db.query(
            "SELECT rowid, bm25(memories_fts) AS score FROM memories_fts "
            "WHERE memories_fts MATCH ? ORDER BY score LIMIT ?",
            (match, limit),
        )
        return [(r["rowid"], -r["score"]) for r in rows]


class MemoryStore:
    def __init__(self, db: Database, backend: SearchBackend | None = None,
                 on_change: Callable[[int | None, str], None] | None = None):
        self.db = db
        self.backend = backend or FTS5Backend(db)
        # Called with (memory_id, "added" | "edited" | "forgotten") after every change, whoever made it
        # (the user through the API or the agent through its memory tools), so open UIs stay current.
        # A wipe of everything is (None, "wiped").
        self.on_change = on_change

    def _changed(self, memory_id: int | None, action: str) -> None:
        if self.on_change:
            self.on_change(memory_id, action)

    def add(self, content: str, *, kind: str = "fact", tags: list[str] | None = None,
            source: str | None = None) -> dict[str, Any]:
        content = content.strip()
        if not content:
            raise ValueError("memory content must not be empty")
        ts = now_iso()
        cur = self.db.execute(
            "INSERT INTO memories(content, kind, tags, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (content, kind, " ".join(tags or []), source, ts, ts),
        )
        memory = self.get(int(cur.lastrowid))
        self._changed(memory["id"], "added")
        return memory

    def get(self, memory_id: int) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM memories WHERE id = ?", (memory_id,))
        if row is None:
            raise NotFound(f"memory {memory_id} does not exist")
        return self._out(row)

    def list(self, *, limit: int = 100, offset: int = 0, kind: str | None = None) -> list[dict[str, Any]]:
        if kind:
            rows = self.db.query(
                "SELECT * FROM memories WHERE kind = ? ORDER BY id DESC LIMIT ? OFFSET ?",
                (kind, limit, offset),
            )
        else:
            rows = self.db.query(
                "SELECT * FROM memories ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
            )
        return [self._out(r) for r in rows]

    def search(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        hits = self.backend.search(query, limit)
        out = []
        for memory_id, score in hits:
            row = self.db.one("SELECT * FROM memories WHERE id = ?", (memory_id,))
            if row:
                out.append({**self._out(row), "score": round(score, 4)})
        return out

    def edit(self, memory_id: int, *, content: str | None = None, kind: str | None = None,
             tags: list[str] | None = None) -> dict[str, Any]:
        current = self.get(memory_id)
        new_content = content.strip() if content is not None else current["content"]
        if not new_content:
            raise ValueError("memory content must not be empty")
        self.db.execute(
            "UPDATE memories SET content = ?, kind = ?, tags = ?, updated_at = ? WHERE id = ?",
            (
                new_content,
                kind or current["kind"],
                " ".join(tags) if tags is not None else " ".join(current["tags"]),
                now_iso(),
                memory_id,
            ),
        )
        self._scrub()
        memory = self.get(memory_id)
        self._changed(memory_id, "edited")
        return memory

    def forget(self, memory_id: int) -> None:
        cur = self.db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        if cur.rowcount == 0:
            raise NotFound(f"memory {memory_id} does not exist")
        self._scrub()
        self._changed(memory_id, "forgotten")

    def _scrub(self) -> None:
        """After an edit or a forget: FTS5 only marks old entries as deleted, so rewrite the index without them,
        then clear the write-ahead log, so the old words are no longer in the database files."""
        self.db.execute("INSERT INTO memories_fts(memories_fts) VALUES ('optimize')")
        self.db.checkpoint()

    def count(self) -> int:
        return int(self.db.one("SELECT COUNT(*) AS n FROM memories")["n"])  # type: ignore[index]

    def wipe(self) -> dict[str, Any]:
        """Forget every memory: the rows, the whole search index, and the database pages that held them.

        Deleted pages are zeroed (``secure_delete``) and the write-ahead log is checkpointed and truncated,
        so the text is no longer in the database files. ``wal_cleared`` is False only if another connection
        (for example a CLI command) was reading at that moment; the next checkpoint then clears it.
        """
        with self.db.transaction() as conn:
            n = int(conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
            conn.execute("DELETE FROM memories")
            # The external-content index is kept in step by triggers; 'delete-all' also drops anything
            # it might still hold, and 'optimize' rewrites it into fresh, empty segments.
            conn.execute("INSERT INTO memories_fts(memories_fts) VALUES ('delete-all')")
            conn.execute("INSERT INTO memories_fts(memories_fts) VALUES ('optimize')")
        cleared = self.db.checkpoint()
        self._changed(None, "wiped")
        return {"forgotten": n, "wal_cleared": cleared}

    @staticmethod
    def _out(row: dict[str, Any]) -> dict[str, Any]:
        return {**row, "tags": [t for t in row["tags"].split(" ") if t]}
