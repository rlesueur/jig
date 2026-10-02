"""Approval queue. A task that needs approval pauses here until the user responds."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from ..audit import AuditLog, sizes, text_size
from ..constants import ApprovalStatus, EventType
from ..db import Database, dumps, new_id, now_iso
from ..errors import NotFound
from ..events import EventBus


class ApprovalConflict(Exception):
    """The approval has already been answered."""


def _out(row: dict[str, Any]) -> dict[str, Any]:
    row["args"] = json.loads(row.pop("args_json"))
    row["reasons"] = json.loads(row.pop("reasons_json"))
    row["sentinel"] = json.loads(row.pop("sentinel_json")) if row["sentinel_json"] else None
    resolved = row.pop("resolved_json", None)
    row["resolved"] = json.loads(resolved) if resolved else None
    return row


class ApprovalQueue:
    def __init__(self, db: Database, bus: EventBus, audit: AuditLog):
        self.db = db
        self.bus = bus
        self.audit = audit
        self._waiters: dict[str, asyncio.Event] = {}

    def request(
        self,
        *,
        run_id: str,
        task_id: str | None,
        tool_call_id: str,
        tool: str,
        args: dict[str, Any],
        reasons: list[dict[str, Any]],
        sentinel: dict[str, Any] | None,
        resolved: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create (or, when resuming a run, re-use) the approval for a tool call."""
        existing = self.db.one("SELECT * FROM approvals WHERE run_id = ? AND tool_call_id = ?", (run_id, tool_call_id))
        if existing:
            approval = _out(existing)
            if approval["status"] == ApprovalStatus.PENDING:
                self.bus.publish(EventType.APPROVAL_REQUESTED, approval_id=approval["id"], run_id=run_id,
                                 task_id=task_id, tool=tool, resumed=True)
            return approval
        aid = new_id("ap")
        self.db.execute(
            "INSERT INTO approvals(id, task_id, run_id, tool_call_id, tool, args_json, reasons_json, sentinel_json, "
            "status, created_at, resolved_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (aid, task_id, run_id, tool_call_id, tool, dumps(args), dumps(reasons),
             dumps(sentinel) if sentinel else None, ApprovalStatus.PENDING, now_iso(),
             dumps(resolved) if resolved else None),
        )
        self.audit.record("approval.requested", f"approval needed for {tool}", task_id=task_id, run_id=run_id,
                          approval_id=aid, tool=tool, args=sizes(args),
                          reasons=[{"rule": r["rule"], "decision": r["decision"]} for r in reasons],
                          sentinel={"verdict": sentinel["verdict"], "risk": sentinel["risk"]} if sentinel else None)
        self.bus.publish(EventType.APPROVAL_REQUESTED, approval_id=aid, run_id=run_id, task_id=task_id, tool=tool,
                         args=args, reasons=reasons, sentinel=sentinel, resolved=resolved)
        return self.get(aid)

    def get(self, approval_id: str) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        if row is None:
            raise NotFound(f"approval {approval_id} does not exist")
        return _out(row)

    def list(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if status:
            rows = self.db.query("SELECT * FROM approvals WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                                 (ApprovalStatus(status), limit))
        else:
            rows = self.db.query("SELECT * FROM approvals ORDER BY created_at DESC LIMIT ?", (limit,))
        return [_out(r) for r in rows]

    async def wait(self, approval_id: str) -> dict[str, Any]:
        while True:
            approval = self.get(approval_id)
            if approval["status"] != ApprovalStatus.PENDING:
                self._waiters.pop(approval_id, None)
                return approval
            event = self._waiters.setdefault(approval_id, asyncio.Event())
            await event.wait()

    def respond(self, approval_id: str, *, approve: bool, note: str | None = None, actor: str = "user") -> dict[str, Any]:
        approval = self.get(approval_id)
        if approval["status"] != ApprovalStatus.PENDING:
            raise ApprovalConflict(f"approval {approval_id} is already {approval['status']}")
        status = ApprovalStatus.APPROVED if approve else ApprovalStatus.DENIED
        cur = self.db.execute(
            "UPDATE approvals SET status = ?, note = ?, resolved_at = ? WHERE id = ? AND status = ?",
            (status, note, now_iso(), approval_id, ApprovalStatus.PENDING),
        )
        if cur.rowcount == 0:
            raise ApprovalConflict(f"approval {approval_id} was answered concurrently")
        self.audit.record("approval.resolved", f"{approval['tool']} {status}", actor=actor,
                          task_id=approval["task_id"], run_id=approval["run_id"], approval_id=approval_id,
                          status=status.value, note_chars=text_size(note))
        self.bus.publish(EventType.APPROVAL_RESOLVED, approval_id=approval_id, run_id=approval["run_id"],
                         task_id=approval["task_id"], tool=approval["tool"], status=status.value)
        if event := self._waiters.get(approval_id):
            event.set()
        return self.get(approval_id)
