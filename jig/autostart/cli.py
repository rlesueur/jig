"""``jig autostart enable | disable | status | show`` and ``jig stop``."""

from __future__ import annotations

import argparse
import json
import sys

from ..config import load_config
from ..instance import request_stop
from . import AutostartError, LaunchSpec, backend_for, disable, enable


def add_parsers(sub: argparse._SubParsersAction) -> None:
    a = sub.add_parser("autostart", help="start Jig automatically when you log in (opt-in)")
    a.add_argument("action", choices=["enable", "disable", "status", "show"],
                   help="enable: register (asks first); disable: remove; status: report; show: print what "
                        "enable would register, changing nothing")
    a.add_argument("--yes", action="store_true", help="confirm 'enable' without the y/N prompt (for scripts)")
    a.add_argument("--now", action="store_true", help="with enable: also start it now")
    a.add_argument("--port", type=int, help="port the autostarted Jig listens on (default: [server] port)")
    a.add_argument("--data-dir", help="data directory (default: from the config)")
    a.add_argument("--entry", help="task name, launchd label or systemd unit (default: \\Jig\\Jig Agent, "
                                   "io.github.rlesueur.jig or jig.service)")
    a.add_argument("--json", action="store_true", help="machine-readable output")
    s = sub.add_parser("stop", help="ask the running Jig (for this data directory) to shut down gracefully")
    s.add_argument("--data-dir", help="data directory (default: from the config)")


def _backend(args: argparse.Namespace):
    config = load_config(args.config, **({"data_dir": args.data_dir} if args.data_dir else {}))
    spec = LaunchSpec.from_config(config, port=args.port)
    return backend_for(spec, entry=args.entry)


def run_stop(args: argparse.Namespace) -> int:
    config = load_config(args.config, **({"data_dir": args.data_dir} if args.data_dir else {}))
    info = request_stop(config.data_dir)
    print(f"Asked Jig (pid {info.get('pid')}) to stop; it finishes its shutdown in the background.")
    return 0


def run(args: argparse.Namespace) -> int:
    backend = _backend(args)
    if args.action == "show":
        plan = backend.plan()
        print(json.dumps(plan.as_dict(), indent=2) if args.json else f"{plan.disclosure()}\n\n{plan.definition}")
        return 0
    if args.action == "status":
        st = backend.status()
        if args.json:
            print(json.dumps(st.as_dict(), indent=2, default=str))
            return 0
        print(f"Autostart ({st.backend}): {'REGISTERED' if st.registered else 'not registered'} as {st.entry}")
        if st.registered:
            print(f"  State:       {st.state}")
            print(f"  Last run:    {st.last_run or 'never'}")
            print(f"  Last result: {st.last_result}")
        running = "no"
        if st.running:
            inst = st.instance or {}
            running = (f"yes (pid {inst.get('pid')}, port {inst.get('port')}, started {inst.get('started_at')} "
                       f"by {inst.get('start_reason')}; {'answering' if st.answering else 'NOT answering'} on HTTP)")
        print(f"  Jig running: {running}")
        print(f"  Log:         {st.log_path}")
        return 0
    if args.action == "disable":
        removed = disable(backend, via="cli")
        print("Removed:\n" + "\n".join(f"  - {r}" for r in removed) if removed else
              f"Autostart was not registered ({backend.entry}); nothing to remove.")
        st = backend.status()
        if st.running:
            print(f"Jig is still running (pid {(st.instance or {}).get('pid')}); stop it with 'jig stop'.")
        return 0
    # enable
    plan = backend.plan()
    print(plan.disclosure())
    print()
    if not args.yes:
        if not sys.stdin or not sys.stdin.isatty():
            print("Not enabled: there is no terminal to confirm on. Re-run with --yes to confirm.", file=sys.stderr)
            return 1
        try:
            answer = input("Register this so Jig starts automatically? [y/N] ")
        except EOFError:  # on Windows, NUL claims to be a terminal
            print("\nNot enabled: no answer was given. Re-run with --yes to confirm.", file=sys.stderr)
            return 1
        if answer.strip().lower() not in ("y", "yes"):
            print("Not enabled.")
            return 1
    try:
        plan = enable(backend, confirmed=True, via="cli", start_now=args.now)
    except AutostartError as exc:
        print(f"Not enabled: {exc}", file=sys.stderr)
        return 1
    print(f"Enabled: {plan.entry}. Turn it off with 'jig autostart disable'.")
    return 0
