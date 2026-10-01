"""Streams one chat turn from a running ``jig serve`` while watching avatar states on /events."""

from __future__ import annotations

import argparse
import asyncio
import json

import httpx
import websockets

from jig.auth import TokenStore
from jig.config import load_config


async def main(url: str, message: str) -> None:
    states: list[str] = []
    stop = asyncio.Event()
    auth = {"Authorization": f"Bearer {TokenStore(load_config().data_dir).get()}"}

    async def watch() -> None:
        async with websockets.connect(url.replace("http", "ws", 1) + "/events", additional_headers=auth) as ws:
            while not stop.is_set():
                try:
                    event = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.5))
                except TimeoutError:
                    continue
                if event["type"] == "avatar.state":
                    d = event["data"]
                    states.append(d["state"] + (f":{d['variant']}" if d.get("variant") else "")
                                  + (" (background)" if d.get("background") else ""))

    watcher = asyncio.create_task(watch())
    await asyncio.sleep(0.5)
    reasoning = 0
    async with httpx.AsyncClient(timeout=httpx.Timeout(10, read=None), headers=auth) as client:
        async with client.stream("POST", f"{url}/chat", json={"message": message}) as r:
            print("jig> ", end="", flush=True)
            async for line in r.aiter_lines():
                if not line:
                    continue
                item = json.loads(line)
                if item["type"] == "content":
                    print(item["text"], end="", flush=True)
                elif item["type"] == "reasoning":
                    reasoning += len(item["text"])
                elif item["type"] == "event":
                    e = item["event"]
                    print(f"\n  [{e['type']}] {e['data'].get('tool')}", flush=True)
                elif item["type"] in ("done", "error"):
                    print(f"\n[{item['type']}] run {item['run_id']} session {item['session_id']}")
    await asyncio.sleep(4)
    stop.set()
    await watcher
    print(f"reasoning characters streamed: {reasoning}")
    print("Avatar state sequence:", " -> ".join(states))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8766")
    p.add_argument("message", nargs="?", default="What time is it in London right now? Answer in one sentence.")
    a = p.parse_args()
    asyncio.run(main(a.url.rstrip("/"), a.message))
