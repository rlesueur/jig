"""Command-line entry point: ``jig serve``, ``jig chat``, ``jig health``, ``jig token``, ``jig ui``,
``jig autostart``, ``jig stop [--model]``, ``jig model`` and ``jig remote``."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

import httpx

from .autostart import cli as autostart_cli
from .config import load_config
from .errors import JigError
from .model import ModelClient
from .vision import probe_vision


def _serve(args: argparse.Namespace) -> int:
    import dataclasses

    from .api import create_app
    from .instance import EXIT_INSTANCE_LOCKED, running_instance
    from .lifecycle import configure_file_logging, run_server

    config = load_config(args.config)
    host = args.host or config.server.host
    port = args.port or config.server.port
    if port == 8080:
        print("Port 8080 is reserved for the model server; choose another port.", file=sys.stderr)
        return 2
    config = dataclasses.replace(config, server=dataclasses.replace(config.server, host=host, port=port))
    if args.log_file:
        configure_file_logging(config.data_dir / "logs" / "jig.log")
    else:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if holder := running_instance(config.data_dir):
        print(f"Not starting: another Jig (pid {holder.get('pid')}, port {holder.get('port')}, started "
              f"{holder.get('started_at')} by {holder.get('start_reason')}) already uses {config.data_dir} and "
              "holds its lock; stop it first with 'jig stop'.", file=sys.stderr)
        return EXIT_INSTANCE_LOCKED
    print(f"Jig serving on http://{host}:{port}  (model {config.model.name or '(auto-discover)'} "
          f"at {config.model.base_url}; config {config.source})")
    print(f"Data: {config.data_dir}   Sandbox: {config.sandbox_dir}")
    app = create_app(config, start_reason=args.start_reason)
    print(f"Web UI: run 'jig ui' to open it signed in. API token: {config.data_dir / 'api-token'} "
          "('jig token show').")
    return run_server(app, host=host, port=port, data_dir=config.data_dir, log_to_file=args.log_file)


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


def _sandbox(args: argparse.Namespace) -> int:
    from .sandbox_container.docker import IMAGE_DIR, build_image

    config = load_config(args.config)
    print(f"Building {config.sandbox.image} from {IMAGE_DIR}")
    build_image(config.sandbox.image)
    print(f"Built {config.sandbox.image}. Select it with [sandbox] backend = \"container\".")
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


def _auth_headers(args: argparse.Namespace) -> dict[str, str]:
    """The API token from the configured data directory, read automatically."""
    from .auth import TokenStore

    return {"Authorization": f"Bearer {TokenStore(load_config(args.config).data_dir).get()}"}


def _token(args: argparse.Namespace) -> int:
    from .auth import TokenStore
    from .autostart.base import record_audit
    from .devices import revoke_all_offline

    config = load_config(args.config)
    store = TokenStore(config.data_dir)
    if args.action == "rotate":
        store.rotate()
        revoked = revoke_all_offline(config.data_dir, "the master API token was rotated")
        record_audit(config.data_dir, "auth.token_rotated", f"master token rotated; {revoked} device(s) revoked",
                     via="cli", devices_revoked=revoked)
        print(f"New API token written to {store.path}. Programs and browsers using the old one are signed out, "
              f"and {revoked} paired device(s) were revoked.", file=sys.stderr)
        return 0
    print(store.ensure())
    return 0


def running_api(config) -> tuple[str, dict[str, str]]:
    """The base URL and token headers of the Jig running for this config's data directory."""
    from .auth import TokenStore
    from .instance import running_instance

    info = running_instance(config.data_dir)
    if info is None:
        raise JigError(f"Jig isn't running for {config.data_dir}. Start it with 'jig serve'.")
    base = f"http://{info.get('host') or '127.0.0.1'}:{info.get('port') or config.server.port}"
    return base, {"Authorization": f"Bearer {TokenStore(config.data_dir).get()}"}


def _confirmed(args: argparse.Namespace, question: str) -> bool:
    if args.yes:
        return True
    if not sys.stdin or not sys.stdin.isatty():
        print("Nothing changed: there is no terminal to confirm on. Re-run with --yes to confirm.", file=sys.stderr)
        return False
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        print("\nNothing changed: no answer was given. Re-run with --yes to confirm.", file=sys.stderr)
        return False


def _api_result(r: httpx.Response, ok: tuple[int, ...] = (200,)) -> dict | None:
    if r.status_code in ok:
        return r.json()
    try:
        message = r.json().get("error", r.text)
    except ValueError:
        message = r.text
    print(f"jig: {message}", file=sys.stderr)
    return None


def _model(args: argparse.Namespace) -> int:
    """``jig model status|start|stop``: the model server that the running Jig launched and supervises."""
    config = load_config(args.config, **({"data_dir": args.data_dir} if args.data_dir else {}))
    base, headers = running_api(config)
    if args.action == "status":
        body = _api_result(httpx.get(f"{base}/model", headers=headers, timeout=30))
        if body is None:
            return 1
        if args.json:
            print(json.dumps(body, indent=2))
            return 0
        state = (f"running, launched and supervised by Jig (pid {body['pid']})" if body["managed"] else
                 "stopped by you ('jig model start' starts it again)" if body["stopped_by_user"] else
                 "not managed by Jig")
        print(f"Model server at {body['base_url']}: {state}")
        if body.get("gpu"):
            g = body["gpu"]
            print(f"  GPU memory: {g['vram_mib']} MiB" if g.get("vram_mib") is not None else
                  f"  GPU: {g.get('note') or g.get('reason')}")
        if body.get("refusal") and not body["managed"]:
            print(f"  {body['refusal']}")
        return 0
    if args.action == "stop":
        if not _confirmed(args, "Stop the model server Jig started? Jig keeps running but can't answer until it is "
                                "started again."):
            return 1
        body = _api_result(httpx.post(f"{base}/model/stop", headers=headers, json={"confirm": True}, timeout=60))
        if body is None:
            return 1
        print(f"Stopped the model server (pid {body['pid']}). {body['message']}")
        return 0
    timeout = config.model_launch.readiness_timeout_s + 60
    print(f"Starting the model server and waiting up to {config.model_launch.readiness_timeout_s:.0f}s for it...")
    body = _api_result(httpx.post(f"{base}/model/start", headers=headers, timeout=timeout))
    if body is None:
        return 1
    print(f"Model server ready (pid {body['pid']})." if body["started"] else
          "A model server was already running at the endpoint, so Jig did not launch one.")
    return 0


REMOTE_DISCLOSURE = """Use Jig from your other devices (through Tailscale):

  - Runs: tailscale serve --bg --https=443 http://127.0.0.1:{port}
  - Exposes: the Jig web UI and API at https://<this machine>.<your tailnet>.ts.net, to devices on YOUR
    tailnet only. It is never put on the public internet (Jig refuses Tailscale Funnel).
  - Who gets in: only the Tailscale login that turns it on (or [remote] allowed_logins), and only from a
    device you have paired with a one-time code from Settings on this computer.
  - Jig itself keeps listening on 127.0.0.1 only. Turn it off with 'jig remote disable'."""


def _remote(args: argparse.Namespace) -> int:
    from .autostart.base import record_audit
    from .remote import RemoteAccess

    config = load_config(args.config, **({"data_dir": args.data_dir} if args.data_dir else {}))
    port = args.port or config.server.port
    remote = RemoteAccess(config)
    if args.action == "status":
        st = remote.status(port)
        if args.json:
            print(json.dumps(st, indent=2, default=str))
            return 0
        print(f"Remote access: {'ON at ' + st['url'] if st['enabled'] else 'off'}")
        if not st["applicable"]:
            print(f"  {st['reason']}")
            return 0
        ts = st["tailscale"]
        print(f"  Tailscale: {'installed (' + str(ts['version']) + ')' if ts['installed'] else 'NOT installed'}"
              f"{', ' + str(ts['backend_state']) if ts['backend_state'] else ''}"
              f"{', signed in as ' + ts['login'] if ts['login'] else ''}")
        if st["enabled"]:
            print(f"  Allowed Tailscale logins: {', '.join(st['allowed_logins'])}")
        for p in st["problems"]:
            print(f"  PROBLEM: {p}")
        if st["steps"]:
            print("  To use Jig from your other devices:")
            for i, s in enumerate(st["steps"], 1):
                print(f"    {i}. {s}")
        return 0
    if args.action == "enable":
        print(REMOTE_DISCLOSURE.format(port=port))
        print()
        if not _confirmed(args, "Turn on remote access through Tailscale?"):
            return 1
        st = remote.enable(port)
        record_audit(config.data_dir, "remote.enabled", f"remote access on at {st['url']}", via="cli", url=st["url"],
                     allowed_logins=st["allowed_logins"])
        print(f"Remote access is on: {st['url']}\nPair a device from Settings > Use Jig from your other devices > "
              "Add a device (on this computer).")
        return 0
    if not _confirmed(args, "Turn off remote access? Your other devices will no longer reach Jig."):
        return 1
    st = remote.disable(port)
    record_audit(config.data_dir, "remote.disabled", "remote access off", via="cli",
                 removed_serve_entry=st["removed_serve_entry"])
    print("Remote access is off." + (" Removed Jig's tailscale serve entry." if st["removed_serve_entry"] else ""))
    for note in st["notes"]:
        print(f"  {note}")
    return 0


async def _ui(args: argparse.Namespace) -> int:
    """Sign the browser in with a one-time code in the URL fragment, which is never sent to the server
    or written to its logs; the page swaps it for a session cookie and removes it from the address bar."""
    import webbrowser

    base = args.url.rstrip("/")
    async with httpx.AsyncClient(timeout=10.0, headers=_auth_headers(args)) as client:
        r = await client.post(f"{base}/auth/login-code")
        if r.status_code != 200:
            print(f"error: HTTP {r.status_code} {r.text}", file=sys.stderr)
            return 1
    url = f"{base}/#code={r.json()['code']}"
    if args.print_url:
        print(url)
    elif not webbrowser.open(url):
        print(f"Could not open a browser. Open this link within two minutes (it works once): {url}",
              file=sys.stderr)
        return 1
    return 0


async def _chat(args: argparse.Namespace) -> int:
    base = args.url.rstrip("/")
    session_id = None
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None), headers=_auth_headers(args)) as client:
        check = await client.get(f"{base}/auth/session")
        check.raise_for_status()
        if not check.json()["authenticated"]:
            print(f"error: {base} rejected the API token from the configured data directory", file=sys.stderr)
            return 1
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
    s.add_argument("--start-reason", choices=["manual", "autostart"], default="manual",
                   help="recorded in the audit log and /status (autostart entries pass 'autostart')")
    s.add_argument("--log-file", action="store_true",
                   help="log to <data_dir>/logs/jig.log (rotating) instead of the console")
    autostart_cli.add_parsers(sub)
    c = sub.add_parser("chat", help="chat with a running Jig server")
    c.add_argument("--url", default="http://127.0.0.1:8766")
    c.add_argument("--mode", choices=["action", "research"], default="action")
    c.add_argument("--show-thinking", action="store_true")
    h = sub.add_parser("health", help="check the configured model servers and their capabilities")
    h.add_argument("--quick", action="store_true", help="only check that the models are served")
    t = sub.add_parser("token", help="show or rotate the API access token")
    t.add_argument("action", choices=["show", "rotate"])
    u = sub.add_parser("ui", help="open the web UI in your browser, already signed in")
    u.add_argument("--url", default="http://127.0.0.1:8766")
    u.add_argument("--print-url", action="store_true", help="print the one-time sign-in link instead of opening it")
    sb = sub.add_parser("sandbox", help="manage the container sandbox")
    sb.add_argument("action", choices=["build"], help="build: build the sandbox Docker image")
    m = sub.add_parser("model", help="the model server that the running Jig launched ([model.launch])")
    m.add_argument("action", choices=["status", "start", "stop"],
                   help="status: what Jig manages and its GPU memory; start: launch it again; stop: stop it "
                        "(only one Jig launched) while Jig keeps running")
    m.add_argument("--yes", action="store_true", help="confirm 'stop' without the y/N prompt")
    m.add_argument("--json", action="store_true", help="machine-readable output")
    m.add_argument("--data-dir", help="data directory (default: from the config)")
    r = sub.add_parser("remote", help="use Jig from your other devices through Tailscale (tailscale serve)")
    r.add_argument("action", choices=["enable", "disable", "status"])
    r.add_argument("--yes", action="store_true", help="confirm without the y/N prompt (for scripts)")
    r.add_argument("--json", action="store_true", help="machine-readable output")
    r.add_argument("--port", type=int, help="Jig's port (default: [server] port)")
    r.add_argument("--data-dir", help="data directory (default: from the config)")
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            return _serve(args)
        if args.command == "model":
            return _model(args)
        if args.command == "remote":
            return _remote(args)
        if args.command == "sandbox":
            return _sandbox(args)
        if args.command == "autostart":
            return autostart_cli.run(args)
        if args.command == "stop":
            return autostart_cli.run_stop(args)
        if args.command == "health":
            return asyncio.run(_health(args))
        if args.command == "token":
            return _token(args)
        if args.command == "ui":
            return asyncio.run(_ui(args))
        return asyncio.run(_chat(args))
    except JigError as exc:
        print(f"jig: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
