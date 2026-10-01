"""End-to-end demo against a running ``jig serve``: goal -> plan -> tasks -> approval -> done.

Watches /events over WebSocket the whole time and records every avatar state.
Usage: python scripts/demo.py [--url http://127.0.0.1:8766]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx
import websockets

from jig.auth import TokenStore
from jig.config import load_config

GOAL = ("Read https://example.com and then save a two-sentence summary of what that page says to "
        "example-summary.md in the workspace.")


async def main(url: str, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "events.jsonl"
    avatar: list[tuple[str, str, str | None]] = []
    stop = asyncio.Event()
    auth = {"Authorization": f"Bearer {TokenStore(load_config().data_dir).get()}"}

    async def watch() -> None:
        ws_url = url.replace("http", "ws", 1) + "/events"
        async with websockets.connect(ws_url, additional_headers=auth) as ws, asyncio.timeout(900):
            with log_path.open("w", encoding="utf-8") as fh:
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                    except TimeoutError:
                        continue
                    event = json.loads(raw)
                    fh.write(raw + "\n")
                    d = event.get("data", {})
                    if event["type"] == "avatar.state":
                        avatar.append((event.get("ts", "snapshot"), d["state"], d.get("variant")))
                        print(f"  [avatar] {d['state']}{':' + d['variant'] if d.get('variant') else ''}")
                    elif event["type"] in ("task.status", "goal.status", "sentinel.verdict", "approval.requested",
                                           "approval.resolved", "tool.start"):
                        brief = {k: d[k] for k in ("title", "status", "tool", "verdict", "reason", "variant") if k in d}
                        print(f"  [{event['type']}] {json.dumps(brief, ensure_ascii=False)}")

    watcher = asyncio.create_task(watch())
    await asyncio.sleep(0.5)
    async with httpx.AsyncClient(base_url=url, timeout=30, headers=auth) as api:
        status = (await api.get("/status")).raise_for_status().json()
        print(f"Jig {status['version']} with model {status['model']['model']} at {status['model']['base_url']}")
        rule = (await api.post("/rules", json={"tool": "write_file", "decision": "ask",
                                               "note": "demo: always ask before writing files"})).json()
        print(f"Custom rule {rule['id']}: write_file -> ask")
        goal = (await api.post("/goals", json={"description": GOAL, "title": "Summarise example.com"})).json()
        print(f"Goal {goal['id']} created; planning...")

        started = time.monotonic()
        approved: set[str] = set()
        while time.monotonic() - started < 900:
            for ap in (await api.get("/approvals", params={"status": "pending"})).json():
                if ap["id"] in approved:
                    continue
                print(f"\n>>> APPROVAL {ap['id']} for {ap['tool']} {json.dumps(ap['args'])[:160]}")
                for r in ap["reasons"]:
                    print(f"    reason [{r['rule']}]: {r['reason']}")
                await asyncio.sleep(2)  # let the avatar sit in the approval state for a moment
                await api.post(f"/approvals/{ap['id']}", json={"approve": True, "note": "approved in demo"})
                approved.add(ap["id"])
                print(">>> approved\n")
            g = (await api.get(f"/goals/{goal['id']}")).json()
            if g["status"] in ("done", "failed", "cancelled"):
                break
            await asyncio.sleep(0.5)
        await asyncio.sleep(4)  # let the transient success state decay back to idle
        stop.set()
        await watcher

        print(f"\nGoal status: {g['status']}  ({time.monotonic() - started:.1f}s)")
        print(f"Plan: {g['plan']['summary'] if g['plan'] else None}")
        for t in g["tasks"]:
            print(f"  - [{t['mode']}] {t['title']}: {t['status']}")
        print(f"Result: {g['result'] or g['error']}")
        sentinel = (await api.get("/audit", params={"kind": "sentinel"})).json()
        for s in sentinel:
            print(f"Sentinel: {s['summary']} - {s['data'].get('reason')}")
        audit_count = len((await api.get("/audit", params={"limit": 5000})).json())
        print(f"Audit entries: {audit_count}")

    sequence = [f"{s}{':' + v if v else ''}" for _, s, v in avatar]
    print("Avatar state sequence:", " -> ".join(sequence))
    (out_dir / "summary.json").write_text(json.dumps({"goal": g, "avatar": sequence}, indent=2), encoding="utf-8")
    return 0 if g["status"] == "done" else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8766")
    p.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "demo-output"))
    a = p.parse_args()
    raise SystemExit(asyncio.run(main(a.url.rstrip("/"), Path(a.out))))
