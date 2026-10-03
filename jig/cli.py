"""Command-line entry point: ``jig serve``, ``jig chat``, ``jig health``, ``jig token``, ``jig ui``,
``jig autostart``, ``jig stop [--model]``, ``jig model``, ``jig remote`` and ``jig connect`` /
``jig connections`` / ``jig disconnect``."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

import httpx

from .autostart import cli as autostart_cli
from .config import load_config
from .connectors import cli as connectors_cli
from .searxng import add_parsers as add_search_parsers
from .searxng import run as run_search
from .errors import JigError
from .model import ModelClient
from .vision import probe_vision


def _port_free(host: str, port: int) -> bool:
    import socket

    with socket.socket() as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def _local_url(host: str, port: int) -> str:
    return f"http://{'127.0.0.1' if host in ('0.0.0.0', '::', '') else host}:{port}"


def _serve(args: argparse.Namespace) -> int:
    import dataclasses

    from .api import create_app
    from .instance import EXIT_INSTANCE_LOCKED, running_instance
    from .lifecycle import configure_file_logging, run_server

    config = load_config(args.config, **({"data_dir": args.data_dir} if args.data_dir else {}))
    host = args.host or config.server.host
    port = args.port or config.server.port
    if port == 8080:
        print("Jig can't use port 8080: that's where llama.cpp's model server usually runs. Choose another port, "
              "for example: jig serve --port 8766", file=sys.stderr)
        return 2
    config = dataclasses.replace(config, server=dataclasses.replace(config.server, host=host, port=port))
    # A person at a terminal sees a short summary; the full log goes to the log file. Services, and
    # anything reading the output, keep the full log on the console as before.
    interactive = sys.stdout.isatty() and not args.verbose and not args.log_file
    if args.log_file:
        configure_file_logging(config.data_dir / "logs" / "jig.log")
    elif interactive:
        configure_file_logging(config.data_dir / "logs" / "jig.log", capture_std=False)
        console = logging.StreamHandler()
        console.setLevel(logging.WARNING)
        console.setFormatter(logging.Formatter("%(message)s"))
        # A failed start is explained in one plain line (jig.startup); the traceback stays in the log file.
        console.addFilter(lambda r: not (r.name.startswith("uvicorn") and r.levelno >= logging.ERROR))
        logging.getLogger().addHandler(console)
    else:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    url = _local_url(host, port)
    if holder := running_instance(config.data_dir):
        where = _local_url(holder.get("host") or host, holder.get("port") or port)
        print(f"Jig is already running for this data folder, at {where}. Open it with: jig ui\n"
              f"(To restart it, run jig stop first.)", file=sys.stderr)
        return EXIT_INSTANCE_LOCKED
    if not _port_free(host, port):
        print(f"Jig can't start on port {port}: another program is already using it. Close that program, or "
              f"start Jig on another port: jig serve --port {port + 1}", file=sys.stderr)
        return 1
    opening = args.start_reason == "manual" and (args.browser or args.window
                                                 or (sys.stdout.isatty() and not args.no_browser))
    # On Windows Jig opens in its own window (jig.desktop); --browser, or another system, uses the browser.
    where = "browser" if args.browser or sys.platform != "win32" else "window"
    open_ui = (where if opening else None, args.config, port)
    app = create_app(config, start_reason=args.start_reason)
    ui_cmd = f'jig --config "{args.config}" ui' if args.config else "jig ui"
    app.state.on_started = lambda controller: _started(app, controller, url, config, open_ui, interactive, ui_cmd)
    return run_server(app, host=host, port=port, data_dir=config.data_dir, log_to_file=args.log_file,
                      access_log=not interactive)


def _started(app, controller, url: str, config, open_ui: tuple, interactive: bool, ui_cmd: str = "jig ui") -> None:
    """Called once the agent (or set-up mode) has started, before the port opens. ``open_ui`` is (where,
    config path, port): where is "window", "browser" or None."""
    import threading

    where = open_ui[0]
    place = "in its window" if where == "window" else "in your browser"
    if controller.setup is not None:
        problem = controller.setup.problem
        print(f"Jig is running at {url}, in set-up mode: {problem.title} {problem.text}", flush=True)
        print("The agent stays off until a model passes its checks. Finish setting up "
              + (f"{place}." if where else f"in Jig: {ui_cmd}"), flush=True)
    else:
        jig = controller.jig
        location = jig.config.model.location
        print(f"Jig is running at {url}, using {jig.model.model_name} "
              f"({'in the cloud at ' + location.host if location.is_cloud else 'on ' + jig.config.model.base_url}).",
              flush=True)
        print(f"Opening it {place} now." if where else f"Open it with: {ui_cmd}", flush=True)
    if interactive:
        print(f"Stop Jig with Ctrl+C or jig stop. Log: {config.data_dir / 'logs' / 'jig.log'}", flush=True)
    if where:
        threading.Thread(target=_open_signed_in, args=(app, url, config, open_ui), daemon=True,
                         name="jig-open-ui").start()


def _open_signed_in(app, url: str, config, open_ui: tuple) -> None:
    """Wait until the server answers, then open Jig's window (which signs itself in), or the web UI in the
    browser with a one-time sign-in code (as 'jig ui' does)."""
    import time
    import webbrowser

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{url}/health", timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.3)
    else:
        return
    where, config_path, port = open_ui
    if where == "window":
        from .desktop import open_window

        open_window(config_path, config.data_dir, port)
        return
    code, _ttl = app.state.auth.new_login_code()
    link = f"{url}/#code={code}"
    if not webbrowser.open(link):
        print(f"Couldn't open a browser. Open this link within two minutes (it works once): {link}", flush=True)


def _open_vault(config):
    from .audit import AuditLog
    from .db import Database
    from .vault import Vault

    db = Database(config.db_path)
    try:
        return db, AuditLog(db), Vault(db, config.vault)
    except BaseException:
        db.close()
        raise


async def _health(args: argparse.Namespace) -> int:
    """Check the configured endpoints and run the real capability probes."""
    from .cloud import require_consent, resolve_api_key
    from .discovery import check_context
    from .friendly import context_note, explain

    config = load_config(args.config)
    if not config.model.base_url:
        print("Jig isn't set up yet: no model has been chosen. Start Jig with jig serve and finish setting up in "
              "your browser.", file=sys.stderr)
        return 1
    db, audit, vault = _open_vault(config)
    try:
        require_consent(config, audit)  # the probes send test prompts, so a cloud endpoint needs consent too
        keys = {label: resolve_api_key(ep, vault, role=label)
                for label, ep in (("agent", config.model), ("sentinel", config.sentinel))}
    finally:
        db.close()
    report: dict = {}
    failed = None
    for label, endpoint in (("agent", config.model), ("sentinel", config.sentinel)):
        client = ModelClient(endpoint, label=f"{label} model", api_key=keys[label])
        try:
            report[label] = {**await client.health(), "location": endpoint.location.as_dict()}
            if not args.quick:
                report[label] |= await client.probe_structured_output()
                if label == "agent":
                    report[label] |= await client.probe_tool_calling()
                    report[label] |= await check_context(config, client)
                    if config.vision.enabled:
                        report[label] |= await probe_vision(client)
        except JigError as exc:
            report[label] = {"status": "failed", "error": str(exc), "location": endpoint.location.as_dict()}
            failed = failed or exc
        finally:
            await client.aclose()
    if args.json:
        print(json.dumps(report, indent=2))
        return 1 if failed else 0
    for label, who in (("agent", "Agent"), ("sentinel", "Safety checker")):
        r = report[label]
        if r.get("status") == "failed":
            print(f"{who}: not working.")
            continue
        where = "cloud" if r["location"]["kind"] == "cloud" else "this computer"
        checks = [] if args.quick else [name for key, name in (("tool_calling", "tools"),
                                                                ("structured_output", "Jig's answer format"),
                                                                ("vision", "pictures")) if r.get(key)]
        print(f"{who}: {r['model']} on {where} ({r['base_url']}) is working"
              + (f": it passed the {', '.join(checks)} checks." if checks else "."))
        if r.get("context_tokens"):
            print(f"  Context: {r['context_tokens']:,} tokens"
                  + ("" if r["context_tokens"] >= config.runtime.min_context_tokens else
                     f" (Jig works best with {config.runtime.min_context_tokens:,} or more)"))
        if (r.get("context_tokens") or 0) >= config.runtime.min_context_tokens and (
                note := context_note(r["context_tokens"], r.get("context_app"), config.runtime.min_context_tokens,
                                     r.get("reload_context"))):
            print(f"  {note}")
    if failed:
        print(f"\n{explain(failed, config)}")
        return 1
    return 0


def _sandbox(args: argparse.Namespace) -> int:
    from .sandbox_container.docker import IMAGE_DIR, build_image

    config = load_config(args.config)
    if args.action == "status":
        from .code_execution import BROWSER_TOOLS, CODE_TOOLS, code_execution_status

        # The container and compose backends register these tools when Jig starts (and refuse to start without
        # Docker); the directory backend registers none.
        names = set(CODE_TOOLS + BROWSER_TOOLS) if config.sandbox.backend in ("container", "compose") else set()
        status = code_execution_status(config, names)
        if args.json:
            print(json.dumps(status, indent=2))
            return 0
        print(f"Sandbox backend: {status['backend']} ({config.source})")
        print(f"Running code: {status['summary']}")
        if docker := status.get("docker"):
            print(f"Docker: {'running, ' + docker['version'] if docker['daemon'] else docker['problem']}")
            print(f"Sandbox image {docker['image']}: {'built' if docker['image_built'] else 'not built'}")
        for i, step in enumerate(status["steps"], 1):
            print(f"  {i}. {step}")
        return 0
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


def _model_key(args: argparse.Namespace, config) -> int:
    """``jig model key set|delete <name>`` and ``jig model key status``: model API keys in the vault."""
    import getpass

    from .cloud import key_secret_name

    action, name = args.sub, args.target
    if action not in ("set", "delete", "status"):
        print("Usage: jig model key set <name> | jig model key delete <name> | jig model key status "
              "(<name> is the provider, for example openai, openrouter, anthropic or gemini)", file=sys.stderr)
        return 2
    if action != "status" and not name:
        print(f"Usage: jig model key {action} <name>, for example: jig model key {action} openai", file=sys.stderr)
        return 2
    db, audit, vault = _open_vault(config)
    try:
        if action == "status":
            return _model_key_status(config, vault)
        secret = key_secret_name(name)
        if action == "delete":
            if not _confirmed(args, f"Delete the model API key {secret!r} from the vault?"):
                return 1
            vault.delete(secret)
            audit.record("vault.deleted", f"model API key {secret!r} deleted", actor="user", secret=secret, via="cli")
            print(f"Deleted {secret!r}. Restart Jig if it was using it.")
            return 0
        if args.stdin:
            value = sys.stdin.readline().strip()
        elif sys.stdin and sys.stdin.isatty():
            value = getpass.getpass(f"Paste the API key for {name} (it won't be shown): ").strip()
        else:
            print("No key given: there is no terminal to type it into. Pipe it in with --stdin.", file=sys.stderr)
            return 1
        if not value or any(c.isspace() for c in value):
            print("Nothing stored: the key is empty or contains spaces.", file=sys.stderr)
            return 1
        stored = vault.set(secret, value, allowed_tools=[])
        audit.record("vault.set", f"model API key {secret!r} stored", actor="user", secret=secret, via="cli",
                     allowed_tools=[])
    finally:
        db.close()
    print(f"Stored the API key as {secret!r} in the vault ({stored['backend']}). It is never shown again, and no tool "
          "can use it.")
    used_by = [s for s, ep in (("[model]", config.model), ("[sentinel]", config.sentinel)) if ep.api_key_secret == secret]
    if used_by:
        print(f"{' and '.join(used_by)} in {config.source} use it. Restart Jig to pick it up.")
    else:
        print(f'To use it, set api_key_secret = "{secret}" under [model] in {config.source} (the cloud profiles in '
              "profiles/ already do).")
    return 0


def _model_key_status(config, vault) -> int:
    import os

    from .config import MODEL_KEY_PREFIX
    from .errors import SecretNotFound

    stored = {s["name"]: s for s in vault.list() if s["name"].startswith(MODEL_KEY_PREFIX)}
    for label, ep in (("Agent", config.model), ("Safety checker", config.sentinel)):
        if ep.api_key_secret:
            try:
                vault.reveal(ep.api_key_secret)
                state = f"in the vault as {ep.api_key_secret!r} (set {stored[ep.api_key_secret]['updated_at']})"
            except SecretNotFound:
                short = ep.api_key_secret.removeprefix(MODEL_KEY_PREFIX)
                state = (f"MISSING: {ep.api_key_secret!r} is not in the vault; store it with "
                         f"'jig model key set {short}'")
        elif ep.api_key_env:
            state = (f"from the environment variable {ep.api_key_env} "
                     f"({'set' if os.environ.get(ep.api_key_env) else 'NOT set'})")
        else:
            state = "none (no key is sent)"
        print(f"{label} ({ep.location.host}): API key {state}")
    others = sorted(set(stored) - {config.model.api_key_secret, config.sentinel.api_key_secret})
    if others:
        print("Also in the vault, not used by this config: " + ", ".join(others))
    return 0


def _model_cloud(args: argparse.Namespace, config) -> int:
    """``jig model cloud status|confirm|revoke``: consent to sending data to a cloud model."""
    from .cloud import (GIVEN, ROLES, active_consents, cloud_uses, consent_groups, consent_state, disclosure,
                        record_consent, record_revocation)

    action = args.sub or "status"
    if action not in ("status", "confirm", "revoke"):
        print("Usage: jig model cloud status | confirm | revoke", file=sys.stderr)
        return 2
    uses = cloud_uses(config)
    db, audit, _vault = _open_vault(config)
    try:
        if action == "status":
            for role, ep in (("agent", config.model), ("sentinel", config.sentinel)):
                loc = ep.location
                if not loc.is_cloud:
                    print(f"{ROLES[role][0].upper() + ROLES[role][1:]}: local ({loc.host}, {loc.reason})")
                    continue
                state = consent_state(audit, role, next(u.origin for u in uses if u.role == role))
                confirmed = state and state["kind"] == GIVEN
                print(f"{ROLES[role][0].upper() + ROLES[role][1:]}: CLOUD ({loc.host}); allow_cloud "
                      f"{'set' if ep.allow_cloud else 'NOT set'}; "
                      f"{'confirmed ' + state['ts'] if confirmed else 'not confirmed'}")
            return 0
        if action == "revoke":
            active = active_consents(audit)
            if not active:
                print("There is no cloud model consent to withdraw.")
                return 0
            for endpoint_origin, roles in active.items():
                record_revocation(audit, roles, endpoint_origin, via="cli")
                print(f"Withdrawn: {' and '.join(ROLES[r] for r in roles)} at {endpoint_origin}")
            print("Jig will refuse to start with a cloud model until you confirm again.")
            return 0
        if not uses:
            print("Nothing to confirm: the agent and the safety checker both use local models.")
            return 0
        missing = [u for u in uses if not u.endpoint.allow_cloud]
        if missing:
            for u in missing:
                section = "model" if u.role == "agent" else "sentinel"
                print(f"Not confirmed: {ROLES[u.role]} would use {u.location.host}, but [{section}] allow_cloud is not "
                      f"set in {config.source}. Add allow_cloud = true under [{section}] first"
                      + (", or point [sentinel] base_url at a local model server to keep safety checks on this "
                         "machine." if u.role == "sentinel" else "."),
                      file=sys.stderr)
            return 1
        print(disclosure(uses))
        print()
        if not _confirmed(args, "Send this to the cloud model(s) above?"):
            return 1
        for group in consent_groups(uses):
            record_consent(audit, group, via="cli")
        print("Confirmed and recorded in the audit log. Withdraw it any time with 'jig model cloud revoke'.")
        return 0
    finally:
        db.close()


def _model(args: argparse.Namespace) -> int:
    """``jig model status|start|stop``: the model server that the running Jig launched and supervises.
    ``jig model key ...`` and ``jig model cloud ...`` work without a running Jig."""
    config = load_config(args.config, **({"data_dir": args.data_dir} if args.data_dir else {}))
    if args.action == "key":
        return _model_key(args, config)
    if args.action == "cloud":
        return _model_cloud(args, config)
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
    """Open Jig's own window on Windows (for this config's Jig), or sign the browser in with a one-time code
    in the URL fragment, which is never sent to the server or written to its logs; the page swaps it for a
    session cookie and removes it from the address bar."""
    import webbrowser

    base = _target_url(args)
    if sys.platform == "win32" and not (args.browser or args.print_url or args.url):
        try:
            answering = httpx.get(f"{base}/health", timeout=5).status_code == 200
        except httpx.HTTPError:
            answering = False
        if not answering:
            print(_not_running(base), file=sys.stderr)
            return 1
        from urllib.parse import urlsplit

        from .desktop import open_window

        open_window(args.config, load_config(args.config).data_dir, urlsplit(base).port)
        return 0
    async with httpx.AsyncClient(timeout=10.0, headers=_auth_headers(args)) as client:
        try:
            r = await client.post(f"{base}/auth/login-code")
        except httpx.HTTPError:
            print(_not_running(base), file=sys.stderr)
            return 1
        if r.status_code == 401:
            print(_other_jig(base), file=sys.stderr)
            return 1
        if r.status_code != 200:
            print(f"Jig at {base} couldn't make a sign-in link (error {r.status_code}): {_error_text(r)}",
                  file=sys.stderr)
            return 1
    url = f"{base}/#code={r.json()['code']}"
    if args.print_url:
        print(url)
    elif not webbrowser.open(url):
        print(f"Could not open a browser. Open this link within two minutes (it works once): {url}",
              file=sys.stderr)
        return 1
    return 0


def _target_url(args: argparse.Namespace) -> str:
    """--url, else the Jig running for this config's data folder, else the configured address."""
    if args.url:
        return args.url.rstrip("/")
    from .instance import running_instance

    config = load_config(args.config)
    info = running_instance(config.data_dir) or {}
    return _local_url(info.get("host") or config.server.host, info.get("port") or config.server.port)


def _not_running(base: str) -> str:
    return f"Jig isn't running at {base}. Start it with: jig serve"


def _other_jig(base: str) -> str:
    return (f"Something at {base} didn't accept this Jig's sign-in token. It may be another Jig with a different "
            "data folder. Check the address, or use --config for that Jig.")


def _error_text(r: httpx.Response) -> str:
    try:
        return str(r.json().get("error", r.text))
    except ValueError:
        return r.text[:300]


async def _chat(args: argparse.Namespace) -> int:
    base = _target_url(args)
    session_id = None
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None), headers=_auth_headers(args)) as client:
        try:
            check = await client.get(f"{base}/auth/session")
        except httpx.HTTPError:
            print(_not_running(base), file=sys.stderr)
            return 1
        if check.status_code != 200 or not check.json()["authenticated"]:
            print(_other_jig(base), file=sys.stderr)
            return 1
        status = await client.get(f"{base}/status")
        if status.status_code == 200 and status.json().get("status") == "setup":
            problem = status.json()["setup"]["problem"]
            print(f"Jig is in set-up mode: {problem['title']} {problem['text']}\nFinish setting up in the browser: "
                  "jig ui", file=sys.stderr)
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
                    await r.aread()
                    print(f"Jig couldn't answer (error {r.status_code}): {_error_text(r)}")
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


MENU = """Jig, your always-on personal AI agent.

  jig serve       Start Jig. It opens (in its own window on Windows), and walks you through set-up the first time.
  jig ui          Open Jig, already signed in. Add --browser to use your browser instead.
  jig stop        Turn Jig off.
  jig health      Check your model works with Jig.
  jig chat        Chat with Jig here in the terminal.
  jig search      Install search (SearXNG) or turn it on. Same as Settings, Search.

More: jig --help"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jig", description="Jig: an always-on personal AI agent, built for local "
                                                             "models.")
    parser.add_argument("--config", help="path to jig.toml")
    sub = parser.add_subparsers(dest="command")
    s = sub.add_parser("serve", help="start Jig (opens it when you run it yourself: its own window on Windows, "
                                     "else your browser)")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.add_argument("--data-dir", help="data folder (default: from the config)")
    s.add_argument("--start-reason", choices=["manual", "autostart"], default="manual",
                   help="recorded in the audit log and /status (autostart entries pass 'autostart')")
    s.add_argument("--log-file", action="store_true",
                   help="log to <data_dir>/logs/jig.log (rotating) instead of the console")
    s.add_argument("--browser", action="store_true",
                   help="open Jig in your browser (instead of its own window on Windows) once it has started")
    s.add_argument("--window", action="store_true",
                   help="open Jig's own window once it has started, even when run from a script (Windows)")
    s.add_argument("--no-browser", action="store_true", help="don't open Jig")
    s.add_argument("--verbose", action="store_true", help="show the full log on the console")
    autostart_cli.add_parsers(sub)
    c = sub.add_parser("chat", help="chat with Jig in the terminal")
    c.add_argument("--url", help="Jig's address (default: the running Jig for this config, else [server] port)")
    c.add_argument("--mode", choices=["action", "research"], default="action")
    c.add_argument("--show-thinking", action="store_true")
    h = sub.add_parser("health", help="check the configured models work with Jig")
    h.add_argument("--quick", action="store_true", help="only check that the models are served")
    h.add_argument("--json", action="store_true", help="machine-readable output")
    t = sub.add_parser("token", help="show or rotate the API access token")
    t.add_argument("action", choices=["show", "rotate"])
    u = sub.add_parser("ui", help="open Jig, already signed in (its own window on Windows, else your browser)")
    u.add_argument("--url", help="Jig's address (default: the running Jig for this config, else [server] port); "
                                 "opens in the browser")
    u.add_argument("--browser", action="store_true", help="open Jig in your browser instead of its own window")
    u.add_argument("--print-url", action="store_true", help="print the one-time sign-in link instead of opening it")
    sb = sub.add_parser("sandbox", help="manage the container sandbox")
    sb.add_argument("action", choices=["build", "status"],
                    help="build: build the sandbox Docker image; status: whether Jig can run code, and what it needs")
    sb.add_argument("--json", action="store_true", help="'status': machine-readable output")
    m = sub.add_parser("model", help="the model server Jig launched ([model.launch]), model API keys, and cloud "
                                     "model consent")
    m.add_argument("action", choices=["status", "start", "stop", "key", "cloud"],
                   help="status: what Jig manages and its GPU memory; start: launch it again; stop: stop it "
                        "(only one Jig launched) while Jig keeps running; key set|delete <name> / key status: "
                        "a cloud model's API key in the vault; cloud status|confirm|revoke: consent to a cloud model")
    m.add_argument("sub", nargs="?", help="for 'key': set, delete or status; for 'cloud': status, confirm or revoke")
    m.add_argument("target", nargs="?", help="for 'key set' and 'key delete': the key's name, usually the provider "
                                             "(openai, openrouter, anthropic, gemini)")
    m.add_argument("--stdin", action="store_true", help="'key set': read the key from standard input")
    m.add_argument("--yes", action="store_true", help="confirm without the y/N prompt")
    m.add_argument("--json", action="store_true", help="machine-readable output")
    m.add_argument("--data-dir", help="data directory (default: from the config)")
    r = sub.add_parser("remote", help="use Jig from your other devices through Tailscale (tailscale serve)")
    r.add_argument("action", choices=["enable", "disable", "status"])
    r.add_argument("--yes", action="store_true", help="confirm without the y/N prompt (for scripts)")
    r.add_argument("--json", action="store_true", help="machine-readable output")
    r.add_argument("--port", type=int, help="Jig's port (default: [server] port)")
    r.add_argument("--data-dir", help="data directory (default: from the config)")
    connectors_cli.add_parsers(sub)
    add_search_parsers(sub)
    args = parser.parse_args(argv)
    if args.command is None:
        print(MENU)
        return 0
    try:
        if args.command == "serve":
            return _serve(args)
        if args.command in ("connect", "connections", "disconnect"):
            return connectors_cli.run(args)
        if args.command == "search":
            return run_search(args)
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
        from .friendly import explain

        logging.getLogger("jig.cli").debug("command failed", exc_info=exc)
        print(f"{explain(exc)}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
