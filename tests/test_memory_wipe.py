"""Forgetting everything: every memory and its search index are removed, from the database files themselves."""

from __future__ import annotations

from jig.constants import EventType

SECRET_WORDS = ("quokkaxylophone", "Robyn's locker code is 4417 zanzibarquill")


def _files(jig) -> bytes:
    path = jig.config.db_path
    wal = path.with_name(path.name + "-wal")
    return path.read_bytes() + (wal.read_bytes() if wal.exists() else b"")


async def test_wipe_removes_every_memory_and_its_index(jig, events):
    for i in range(40):
        jig.memory.add(f"Fact {i}: Robyn's favourite {SECRET_WORDS[0]} number is {i}", tags=["test"])
    jig.memory.add(SECRET_WORDS[1], kind="private")
    jig.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    before = _files(jig)
    assert all(w.encode() in before for w in SECRET_WORDS), "the test must see the text before the wipe"
    assert jig.memory.search("zanzibarquill")

    out = jig.memory.wipe()
    assert out == {"forgotten": 41, "wal_cleared": True}
    assert jig.memory.count() == 0 and jig.memory.list() == []
    assert jig.memory.search("quokkaxylophone") == [] and jig.memory.search("zanzibarquill") == []
    assert jig.db.query("SELECT rowid FROM memories_fts WHERE memories_fts MATCH 'zanzibarquill'") == []
    assert jig.db.one("SELECT COUNT(*) AS n FROM memories_fts_docsize")["n"] == 0
    after = _files(jig)
    for w in (*SECRET_WORDS, "zanzibarquill"):
        assert w.encode() not in after, f"{w!r} is still in the database files"
    assert (EventType.MEMORY_CHANGED, {"memory_id": None, "action": "wiped"}) in [(e.type, e.data) for e in events]

    # Memory keeps working afterwards, and new ids never reuse the old ones the audit log refers to.
    fresh = jig.memory.add("Robyn likes rowing")
    assert fresh["id"] == 42 and jig.memory.search("rowing")[0]["id"] == 42
    assert jig.memory.wipe()["forgotten"] == 1
    assert jig.memory.wipe() == {"forgotten": 0, "wal_cleared": True}
