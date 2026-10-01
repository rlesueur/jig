"""Command-line entry point: ``jig serve``, ``jig chat`` and ``jig health``."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

import httpx

from .config import load_config
from .errors import JigError
from .model import ModelClient
from .vision import probe_vision


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .api import create_app

    config = load_config(args.config)
    host = args.host or config.server.host
    port = args.port or config.server.port
    if port == 8080:
        print("Port 8080 is reserved for the model server; choose another port.", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    print(f"Jig serving on http://{host}:{port}  (model {config.model.name or '(auto-discover)'} "
          f"at {config.model.base_url}; config {config.source})")
    print(f"Data: {config.data_dir}   Sandbox: {config.sandbox_dir}")
    uvicorn.run(create_app(config), host=host, port=port, log_level="info")
    return 0


async def _health(args: argparse.Namespace) -> int:
    """Check the configured endpoints and run the real capability probes."""
    config = load_config(args.config)
    report: dict = {}
    for label, endpoint in (("agent", config.model), ("sentinel", config.sentinel)):
        client = ModelClient(endpoint, label=f"{label} model")
        try:
            report[label] = await client.health()
            if not args.quick:
                report[label] |= await client.probe_structured_output()
                if label == "agent":
                    report[label] |= await client.probe_tool_calling()
                    if config.vision.enabled:
                        report[label] |= await probe_vision(client)
        finally:
            await client.aclose()
    print(json.dumps(report, indent=2))
    return 0


def _print_event(event: dict) -> None:
    t, d = event["type"], event["data"]
    if t == "tool.start":
        print(f"\n  [tool] {d['tool']} ({d['variant']})", flush=True)
    elif t == "sentinel.verdict":
        print(f"\n  [sentinel] {d['tool']}: {d['verdict']} ({d['risk']}) - {d['reason']}", flush=True)
    elif t == "approval.requested" and not d.get("resumed"):
        print(f"\n  [approval needed] {d['tool']} {json.dumps(d.get('args'))}", flush=True)
        for r in d.get("reasons") or []:
            print(f"     - {r['rule']}: {r['reason']}", flush=True)


async def _chat(args: argparse.Namespace) -> int:
    base = args.url.rstrip("/")
    session_id = None
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None)) as client:
        (await client.get(f"{base}/health")).raise_for_status()
        print("Jig chat. Type /quit to leave. Approvals are asked for inline.")
        while True:
            try:
                message = input("\nyou> ").strip()
            except EOFError:
                return 0
            if message in ("/quit", "/exit"):
                return 0
            if not message:
                continue
            body = {"message": message, "session_id": session_id, "mode": args.mode}
            pending: list[dict] = []
            async with client.stream("POST", f"{base}/chat", json=body) as r:
                if r.status_code != 200:
                    print(f"error: HTTP {r.status_code} {(await r.aread()).decode()}")
                    continue
                print("jig> ", end="", flush=True)
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    item = json.loads(line)
                    kind = item["type"]
                    if kind == "content":
                        print(item["text"], end="", flush=True)
                    elif kind == "reasoning" and args.show_thinking:
                        print(f"\x1b[2m{item['text']}\x1b[0m", end="", flush=True)
                    elif kind == "event":
                        _print_event(item["event"])
                        if item["event"]["type"] == "approval.requested" and not item["event"]["data"].get("resumed"):
                            pending.append(item["event"]["data"])
                            asyncio.create_task(_ask_approval(client, base, item["event"]["data"]))
                    elif kind == "done":
                        session_id = item["session_id"]
                        print()
                    elif kind == "error":
                        session_id = item["session_id"]
                        print(f"\nerror: {item['error']}")


async def _ask_approval(client: httpx.AsyncClient, base: str, data: dict) -> None:
    answer = await asyncio.to_thread(input, f"  approve {data['tool']}? [y/N] ")
    r = await client.post(f"{base}/approvals/{data['approval_id']}", json={"approve": answer.strip().lower() == "y"})
    r.raise_for_status()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jig", description="Jig: a local, always-on personal AI agent.")
    parser.add_argument("--config", help="path to jig.toml")
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("serve", help="run the always-on agent and HTTP API")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    c = sub.add_parser("chat", help="chat with a running Jig server")
    c.add_argument("--url", default="http://127.0.0.1:8766")
    c.add_argument("--mode", choices=["action", "research"], default="action")
    c.add_argument("--show-thinking", action="store_true")
    h = sub.add_parser("health", help="check the configured model servers and their capabilities")
    h.add_argument("--quick", action="store_true", help="only check that the models are served")
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            return _serve(args)
        if args.command == "health":
            return asyncio.run(_health(args))
        return asyncio.run(_chat(args))
    except JigError as exc:
        print(f"jig: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
