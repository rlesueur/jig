"""``jig connect <provider>``, ``jig connections`` and ``jig disconnect <provider>``.

Secrets are only ever typed at a prompt that doesn't echo them, or read from the file the provider
downloads; they go straight into the vault. These commands work whether or not Jig is running.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
import webbrowser
from pathlib import Path

from ..config import load_config
from ..errors import ConnectorError
from ..tools.builtin import http_client
from . import connect, google, microsoft
from .base import PROVIDERS, ConnectionStore, Connectors, provider

CLIENT_ALIASES = {"google-client": google.FAMILY, "microsoft-client": microsoft.FAMILY}


def add_parsers(sub: argparse._SubParsersAction) -> None:
    c = sub.add_parser("connect", help="connect one of your accounts (Gmail, ...); see docs/connectors-setup.md")
    c.add_argument("provider", choices=sorted(PROVIDERS))
    c.add_argument("--access", help="access level, for example read, send or manage for Gmail (default: the "
                                    "connector's default)")
    c.add_argument("--client-json", help="Google: the Desktop app client JSON downloaded from the Google Cloud console")
    c.add_argument("--client-id", help="Microsoft: use your organisation's own app registration (its Application "
                                       "(client) ID) instead of Jig's built-in app")
    c.add_argument("--tenant", help="Microsoft, with --client-id: your tenant (Directory ID or domain) for a "
                                    "single-organisation app; default common")
    c.add_argument("--token", action="store_true",
                   help="GitHub: connect with a fine-grained personal access token instead of signing in with the "
                        "Jig GitHub App (advanced)")
    c.add_argument("--option", action="append", default=[], metavar="NAME=VALUE",
                   help="a non-secret setting the connector asks for (for example homeserver=https://matrix.org)")
    c.add_argument("--stdin", action="store_true",
                   help="read the secret(s) the connector asks for from standard input, one per line, instead of "
                        "prompting")
    c.add_argument("--no-browser", action="store_true", help="print the sign-in link instead of opening a browser")
    c.add_argument("--data-dir", help="data directory (default: from the config)")
    ls = sub.add_parser("connections", help="list your connected accounts and what each can do")
    ls.add_argument("--json", action="store_true", help="machine-readable output")
    ls.add_argument("--data-dir", help="data directory (default: from the config)")
    d = sub.add_parser("disconnect", help="revoke and delete a connected account's tokens (or a stored app client)")
    d.add_argument("provider", choices=sorted([*PROVIDERS, *CLIENT_ALIASES]))
    d.add_argument("--yes", action="store_true", help="confirm without the y/N prompt")
    d.add_argument("--data-dir", help="data directory (default: from the config)")


def _open(config):
    from ..audit import AuditLog
    from ..db import Database
    from ..vault import Vault

    db = Database(config.db_path)
    try:
        audit = AuditLog(db)
        return db, ConnectionStore(db, Vault(db, config.vault), audit, config.connectors)
    except BaseException:
        db.close()
        raise


def _confirm(args: argparse.Namespace, question: str) -> bool:
    if getattr(args, "yes", False):
        return True
    if not sys.stdin or not sys.stdin.isatty():
        print("Nothing changed: there is no terminal to confirm on. Re-run with --yes to confirm.", file=sys.stderr)
        return False
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _store_microsoft_client(args: argparse.Namespace, store: ConnectionStore) -> None:
    if args.tenant and not args.client_id:
        raise ConnectorError("--tenant only applies with --client-id (your own app registration)")
    if args.client_id:
        store.set_client(microsoft.FAMILY, microsoft.client_from_id(args.client_id, args.tenant or ""), via="cli")
        print("Stored your app registration's client ID in the vault (connector.microsoft.client). "
              "'jig disconnect microsoft-client' goes back to Jig's built-in app.")


def _token_values(args: argparse.Namespace, spec) -> dict[str, str]:
    """What a token connector asks for: settings from --option, secrets from a hidden prompt or --stdin."""
    options: dict[str, str] = {}
    for item in args.option:
        name, sep, value = item.partition("=")
        if not sep:
            raise ConnectorError(f"--option must be NAME=VALUE, not {item!r}")
        options[name.strip()] = value.strip()
    known = {i.name for i in spec.inputs if not i.secret}
    unknown = set(options) - known
    if unknown:
        raise ConnectorError(f"{spec.label} has no setting {sorted(unknown)}; it asks for {sorted(known) or 'none'}")
    values: dict[str, str] = {}
    lines = iter(sys.stdin.read().splitlines()) if args.stdin else None
    for item in spec.inputs:
        if not item.secret:
            if item.name in options:
                values[item.name] = options[item.name]
            elif sys.stdin and sys.stdin.isatty() and not args.stdin:
                values[item.name] = input(f"{item.prompt}: ").strip()
            else:
                raise ConnectorError(f"{spec.label} needs --option {item.name}=... ({item.prompt})")
        elif lines is not None:
            values[item.name] = next(lines, "").strip()
        elif sys.stdin and sys.stdin.isatty():
            values[item.name] = getpass.getpass(f"{item.prompt} (it won't be shown): ").strip()
        else:
            raise ConnectorError(f"{spec.label} needs {item.prompt}; there is no terminal to ask on, so pass it "
                                 "on standard input with --stdin")
    return values


def _ensure_client(args: argparse.Namespace, store: ConnectionStore, family: str) -> None:
    if family != google.FAMILY:
        return
    if args.client_json:
        path = Path(args.client_json).expanduser()
        store.set_client(family, google.client_from_json(path), via="cli")
        print(f"Stored the Google app client in the vault (connector.google.client). You can delete {path} now.")
        return
    if store.has_client(family):
        return
    if not sys.stdin or not sys.stdin.isatty():
        raise ConnectorError("no Google app client is stored, and there is no terminal to ask for it; pass "
                             "--client-json <downloaded file> (docs/connectors-setup.md)")
    print("No Google app client is stored yet (see docs/connectors-setup.md, 'Create the OAuth client').")
    client_id = input("Client ID (ends in .apps.googleusercontent.com): ").strip()
    client_secret = getpass.getpass("Client secret (it won't be shown): ").strip()
    client = {"client_id": client_id, "client_secret": client_secret}
    google.check_client(client)
    store.set_client(family, client, via="cli")
    print("Stored the Google app client in the vault (connector.google.client).")


async def _connect(args: argparse.Namespace, store: ConnectionStore) -> int:
    spec = provider(args.provider)
    if (args.client_id or args.tenant) and spec.family != microsoft.FAMILY:
        print("jig: --client-id and --tenant are for Microsoft only", file=sys.stderr)
        return 2
    if args.token and "token" not in spec.methods:
        print(f"jig: {spec.label} has no personal access token option", file=sys.stderr)
        return 2
    if spec.family == microsoft.FAMILY:
        _store_microsoft_client(args, store)
    if spec.needs_client:
        _ensure_client(args, store, spec.family)
    client = store.client_state(spec)
    if not client["configured"] and not args.token:
        raise ConnectorError(client["problem"])
    level = args.access or spec.default_access
    if level not in spec.access_levels:
        print(f"jig: {spec.label} access must be one of {list(spec.access_levels)}", file=sys.stderr)
        return 2
    print(f"Connecting {spec.label} with '{level}' access: {spec.access_levels[level].description}.")
    if spec.kind == "token" or args.token:
        values = _token_values(args, spec)
        async with http_client() as http:
            result = await connect(args.provider, access=level, store=store, http=http, values=values,
                                   method="token", via="cli")
        return _connected(args, spec, result)
    if spec.kind == "device":
        def show_code(info: dict) -> None:
            print(f"\nOpen {info['verification_uri']} in a browser (on any device), sign in to GitHub and type "
                  f"this code:\n\n    {info['user_code']}\n\nWaiting for you to finish (the code works for "
                  f"{int(info['expires_in']) // 60} minutes)...")
            if not args.no_browser:
                webbrowser.open(info["verification_uri"])

        async with http_client() as http:
            result = await connect(args.provider, access=level, store=store, http=http, show_code=show_code,
                                   method="device", via="cli")
        return _connected(args, spec, result)
    print("Scopes requested: " + ", ".join(spec.access_levels[level].scopes))

    def show(url: str) -> None:
        if args.no_browser:
            print(f"\nOpen this link in a browser on THIS computer (it answers on 127.0.0.1) and sign in:\n{url}\n")
        else:
            print("Opening your browser to sign in. If it doesn't open, run again with --no-browser.")

    def open_browser(url: str) -> None:
        if not args.no_browser and not webbrowser.open(url):
            print(f"Could not open a browser. Open this link on this computer:\n{url}")

    async with http_client() as http:
        result = await connect(args.provider, access=level, store=store, http=http, open_browser=open_browser,
                               ready=show, via="cli")
    return _connected(args, spec, result)


def _connected(args: argparse.Namespace, spec, result: dict) -> int:
    row = result["connection"]
    print(f"\n{spec.label} is connected as {row['account']} ('{row['access']}' access).")
    print("Granted: " + ", ".join(row["scopes"]))
    if result["not_granted"]:
        print("NOT granted (the tools that need these will say so): " + ", ".join(result["not_granted"]))
    print("The tokens are in the vault as connector.%s.grant; the model never sees them." % args.provider)
    print(f"Remove it any time with 'jig disconnect {args.provider}'.")
    return 0


def _list(args: argparse.Namespace, store: ConnectionStore) -> int:
    rows = store.status()
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    for r in rows:
        if r["connected"]:
            state = f"connected as {r['account']} ('{r['access']}' access) since {r['connected_at']}"
        elif r["status"] == "needs_reconnect":
            state = f"NEEDS RECONNECTING: {r['last_error']}"
        else:
            state = "not connected" + ("" if r["client_configured"] else f" ({r['client_problem']})")
        print(f"{r['label']} ({r['provider']}): {state}")
        if r["scopes"]:
            print("  scopes: " + ", ".join(r["scopes"]))
    return 0


async def _disconnect(args: argparse.Namespace, store: ConnectionStore) -> int:
    if args.provider in CLIENT_ALIASES:
        family = CLIENT_ALIASES[args.provider]
        users = [s.id for s in PROVIDERS.values() if s.family == family and store.get(s.id)]
        if users:
            print(f"jig: disconnect {', '.join(users)} first; they use this app client.", file=sys.stderr)
            return 1
        if not _confirm(args, f"Delete the stored {family} app client from the vault?"):
            return 1
        print("Deleted." if store.delete_client(family, via="cli") else "There was no stored client.")
        return 0
    spec = provider(args.provider)
    if not _confirm(args, f"Disconnect {spec.label}? Jig revokes its access where it can and deletes the tokens."):
        return 1
    async with http_client() as http:
        result = await Connectors(store, http, {}).disconnect(args.provider, via="cli")
    print(f"{spec.label}: tokens deleted from the vault; {result['at_provider']}.")
    if spec.manage_url:
        print(f"Check or remove the app's access yourself at {spec.manage_url}")
    return 0


def run(args: argparse.Namespace) -> int:
    config = load_config(args.config, **({"data_dir": args.data_dir} if args.data_dir else {}))
    db, store = _open(config)
    try:
        if args.command == "connect":
            return asyncio.run(_connect(args, store))
        if args.command == "connections":
            return _list(args, store)
        return asyncio.run(_disconnect(args, store))
    finally:
        db.close()
