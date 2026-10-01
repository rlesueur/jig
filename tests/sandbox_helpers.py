"""Helpers for the container sandbox tests: a real Jig runtime with the container backend and real Docker."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from jig.constants import Mode
from jig.db import new_id
from jig.model import ToolCall
from jig.policy.gate import CallContext, ToolOutcome
from jig.runtime import Jig

from .conftest import wait_for


async def gated_call(jig: Jig, name: str, args: dict[str, Any], *, intent: str, mode: Mode = Mode.ACTION,
                     run_id: str | None = None,
                     on_approval: Callable[[dict[str, Any]], Any] | None = None) -> tuple[ToolOutcome, list[dict]]:
    """Run one tool call through the full gate (core rules, custom rules, the real Sentinel, approvals).

    ``on_approval`` is called for every approval the call asks for and must return True (approve) or
    False (deny); by default approvals are granted, because these tests check behaviour, not the
    Sentinel's judgement. Returns the outcome and the approvals that were requested.
    """
    run_id = run_id or jig.store.create_run(kind="chat", mode=mode)
    call = ToolCall(id=new_id("call"), name=name, arguments_raw=json.dumps(args))
    ctx = CallContext(run_id=run_id, task_id=None, mode=mode, intent=intent)
    task = asyncio.create_task(jig.executor.execute(call, ctx))
    seen: list[dict[str, Any]] = []
    while not task.done():
        for approval in jig.approvals.list(status="pending"):
            if approval["run_id"] != run_id or any(a["id"] == approval["id"] for a in seen):
                continue
            seen.append(approval)
            decision = on_approval(approval) if on_approval else True
            if asyncio.iscoroutine(decision):
                decision = await decision
            jig.approvals.respond(approval["id"], approve=bool(decision), note="test")
        await asyncio.sleep(0.2)
    return task.result(), seen


async def pending_approval(jig: Jig, tool: str) -> dict[str, Any]:
    found = await wait_for(lambda: [a for a in jig.approvals.list(status="pending") if a["tool"] == tool],
                           what=f"a pending approval for {tool}")
    return found[0]
