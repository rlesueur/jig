"""Turn Jig's append-only audit log into per-tool-call records for scoring."""

from __future__ import annotations

import json
from typing import Any


def _key(tool: str, args: Any) -> str:
    return tool + "|" + json.dumps(args, sort_keys=True, default=str)


def tool_calls(audit: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    pending_by_key: dict[str, list[str]] = {}
    for row in audit:
        kind, d = row["kind"], row["data"]
        if kind == "tool.call" and d.get("call_id"):
            cid = d["call_id"]
            if cid in calls:  # a resumed run re-requests the same call id
                continue
            calls[cid] = {"call_id": cid, "task_id": row["task_id"], "run_id": row["run_id"], "tool": d["tool"],
                          "args": d.get("args"), "mode": d.get("mode"), "sentinel": None, "approval": None,
                          "blocked_by": None, "ok": None, "error_type": None, "error": None}
            order.append(cid)
            pending_by_key.setdefault(_key(d["tool"], d.get("args")), []).append(cid)
        elif kind in ("sentinel.verdict", "approval.requested"):
            ids = pending_by_key.get(_key(d.get("tool", ""), d.get("args")), [])
            target = next((c for c in ids if (calls[c]["sentinel"] if kind == "sentinel.verdict"
                                              else calls[c]["approval"]) is None), None)
            if target is None:
                continue
            if kind == "sentinel.verdict":
                calls[target]["sentinel"] = {k: d.get(k) for k in ("verdict", "risk", "reason", "elapsed_s")}
            else:
                calls[target]["approval"] = {"approval_id": d.get("approval_id"), "reasons": d.get("reasons"),
                                             "status": "pending"}
        elif kind == "approval.resolved":
            for c in calls.values():
                if c["approval"] and c["approval"]["approval_id"] == d.get("approval_id"):
                    c["approval"]["status"] = d.get("status")
        elif kind in ("tool.result", "tool.error") and d.get("call_id") in calls:
            c = calls[d["call_id"]]
            c["ok"] = kind == "tool.result"
            if kind == "tool.error":
                c["error_type"], c["error"] = d.get("error_type"), (d.get("error") or "")[:500]
                c["blocked_by"] = classify_block(c)
    return [calls[c] for c in order]


def classify_block(call: dict[str, Any]) -> str | None:
    et, err = call["error_type"], call["error"] or ""
    if et == "ModeViolation":
        return "mode"
    if et == "PolicyBlocked":
        if "Sentinel denied" in err:
            return "sentinel"
        if "core rule" in err:
            return "core"
        if "custom rule" in err:
            return "rule"
        return "policy"
    if et == "ApprovalDenied":
        return "human"
    return None


def model_usage(audit: list[dict[str, Any]]) -> dict[str, Any]:
    prompt = completion = calls = 0
    elapsed = 0.0
    for row in audit:
        if row["kind"] == "model.call" and "usage" in row["data"]:
            u = row["data"]["usage"] or {}
            prompt += int(u.get("prompt_tokens") or 0)
            completion += int(u.get("completion_tokens") or 0)
            elapsed += float(row["data"].get("elapsed_s") or 0)
            calls += 1
    return {"model_calls": calls, "prompt_tokens": prompt, "completion_tokens": completion,
            "model_elapsed_s": round(elapsed, 2)}
