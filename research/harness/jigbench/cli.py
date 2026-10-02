"""jigbench command line: run, report, submit-results, overnight, policy-status."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from .paths import LOGS


def _setup_logging(name: str) -> Path:
    LOGS.mkdir(parents=True, exist_ok=True)
    path = LOGS / f"{name}-{datetime.now():%Y%m%d}.log"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.FileHandler(path, encoding="utf-8"), logging.StreamHandler(sys.stdout)])
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jigbench")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run (or resume) an experiment config")
    r.add_argument("config", type=Path)
    r.add_argument("--policy", choices=["pilot", "overnight"], default="overnight",
                   help="'overnight' enforces the compute policy; 'pilot' is for short development runs")
    r.add_argument("--limit", type=int)
    rep = sub.add_parser("report", help="summarise results into tables and figures")
    rep.add_argument("runs", nargs="*", type=Path, help="run directories (default: all under results/)")
    s = sub.add_parser("submit-results", help="validate a run and package it for contribution")
    s.add_argument("run_dir", type=Path)
    s.add_argument("--contributor", required=True)
    s.add_argument("--notes", default="")
    o = sub.add_parser("overnight", help="work through the queue under the compute policy")
    o.add_argument("--queue", type=Path, required=True)
    sub.add_parser("policy-status", help="show what the compute policy would decide now")
    sub.add_parser("restore-watchdog", help="restore the main model server from the latest unrestored "
                                            "handover file if no harness is alive (run from a scheduled task)")
    sub.add_parser("handover-status", help="show pending (unrestored) GPU handovers and 8080 idleness")
    args = ap.parse_args(argv)

    if args.cmd == "run":
        from .compute_policy import NoPolicy, Policy
        from .runner import run
        _setup_logging("run")
        summary = asyncio.run(run(args.config, policy=Policy() if args.policy == "overnight" else NoPolicy(),
                                  limit=args.limit))
        print(json.dumps(summary, indent=1))
        return 0 if not summary["errors"] else 2
    if args.cmd == "report":
        from .report import report
        print(json.dumps(report(args.runs or None), indent=1))
        return 0
    if args.cmd == "submit-results":
        from .submit import submit
        print(submit(args.run_dir, contributor=args.contributor, notes=args.notes))
        return 0
    if args.cmd == "overnight":
        from .overnight import overnight
        _setup_logging("overnight")
        return overnight(args.queue)
    if args.cmd == "policy-status":
        from .compute_policy import Policy, idle_seconds
        ok, why = Policy().may_start()
        print(json.dumps({"may_start": ok, "reason": why, "idle_s": round(idle_seconds()),
                          "must_stop": Policy().must_stop()}, indent=1))
        return 0
    if args.cmd == "restore-watchdog":
        from .handover import restore_watchdog
        _setup_logging("restore-watchdog")
        return restore_watchdog()
    if args.cmd == "handover-status":
        from .handover import IdleWatch, JigClient, unrestored_handovers
        jig = JigClient()
        busy, why = jig.busy()
        print(json.dumps({"pending_handovers": [str(p) for p in unrestored_handovers()],
                          "main_8080_idle": IdleWatch().sample(), "jig_running": jig.running(),
                          "jig_busy": busy, "jig_detail": why}, indent=1, default=str))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
