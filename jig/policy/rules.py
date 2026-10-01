"""Custom rules: user-editable allow / ask / block per tool, optionally per argument.

``tool`` is a glob over tool names (``web_*``). ``arg`` and ``pattern``
optionally narrow the rule to calls whose argument matches a glob, such as
``url`` matching ``https://*.gov.uk/*``. The highest priority match wins;
ties go to the most restrictive decision.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Any

from ..constants import Decision
from ..db import Database, new_id, now_iso
from ..errors import NotFound

_RESTRICTIVENESS = {Decision.ALLOW: 0, Decision.ASK: 1, Decision.BLOCK: 2}


class RuleStore:
    def __init__(self, db: Database):
        self.db = db

    def create(self, *, tool: str, decision: str, arg: str | None = None, pattern: str | None = None,
               priority: int = 0, note: str | None = None, enabled: bool = True) -> dict[str, Any]:
        self._validate(tool, decision, arg, pattern)
        ts = now_iso()
        rid = new_id("rule")
        self.db.execute(
            "INSERT INTO rules(id, tool, arg, pattern, decision, priority, note, enabled, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, tool, arg, pattern, Decision(decision), priority, note, int(enabled), ts, ts),
        )
        return self.get(rid)

    def get(self, rule_id: str) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM rules WHERE id = ?", (rule_id,))
        if row is None:
            raise NotFound(f"rule {rule_id} does not exist")
        row["enabled"] = bool(row["enabled"])
        return row

    def list(self) -> list[dict[str, Any]]:
        return [self.get(r["id"]) for r in self.db.query("SELECT id FROM rules ORDER BY priority DESC, created_at")]

    def update(self, rule_id: str, **fields: Any) -> dict[str, Any]:
        current = self.get(rule_id)
        merged = {**current, **fields}
        self._validate(merged["tool"], merged["decision"], merged["arg"], merged["pattern"])
        if "enabled" in fields:
            fields["enabled"] = int(bool(fields["enabled"]))
        if "decision" in fields:
            fields["decision"] = Decision(fields["decision"]).value
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE rules SET {cols} WHERE id = ?", (*fields.values(), rule_id))
        return self.get(rule_id)

    def delete(self, rule_id: str) -> None:
        if self.db.execute("DELETE FROM rules WHERE id = ?", (rule_id,)).rowcount == 0:
            raise NotFound(f"rule {rule_id} does not exist")

    def match(self, tool: str, args: dict[str, Any]) -> dict[str, Any] | None:
        best: dict[str, Any] | None = None
        for rule in self.db.query("SELECT * FROM rules WHERE enabled = 1"):
            if not fnmatchcase(tool, rule["tool"]):
                continue
            if rule["arg"]:
                if rule["arg"] not in args or not fnmatchcase(str(args[rule["arg"]]), rule["pattern"] or "*"):
                    continue
            key = (rule["priority"], _RESTRICTIVENESS[Decision(rule["decision"])])
            if best is None or key > (best["priority"], _RESTRICTIVENESS[Decision(best["decision"])]):
                best = rule
        return best

    @staticmethod
    def _validate(tool: str, decision: str, arg: str | None, pattern: str | None) -> None:
        if not tool or not isinstance(tool, str):
            raise ValueError("rule 'tool' must be a non-empty glob")
        Decision(decision)
        if pattern and not arg:
            raise ValueError("rule 'pattern' needs 'arg'")
