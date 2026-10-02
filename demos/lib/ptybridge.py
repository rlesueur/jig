"""Run a program in a real Windows pseudo-console (ConPTY) and log its exact output with timestamps.

Usage: python ptybridge.py --cols 110 --rows 30 --log out.jsonl [--cwd DIR] -- program [args...]

Control comes in on stdin as JSON lines from the Node orchestrator:
  {"op": "write", "data": "..."}   send keystrokes to the console
  {"op": "kill"}                   terminate the program
Every chunk the console emits is written, unaltered, to stdout and to --log as
  {"t": <unix seconds>, "data": "<raw VT output>"}
and the exit as {"t": ..., "exit": <code>}. The replay renders these bytes with xterm.js.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time

from winpty import PtyProcess


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cols", type=int, default=110)
    p.add_argument("--rows", type=int, default=30)
    p.add_argument("--log", required=True)
    p.add_argument("--cwd", default=os.getcwd())
    p.add_argument("argv", nargs=argparse.REMAINDER)
    a = p.parse_args()
    argv = a.argv[1:] if a.argv and a.argv[0] == "--" else a.argv
    if not argv:
        print("ptybridge: no program given", file=sys.stderr)
        return 2

    log = open(a.log, "w", encoding="utf-8")
    out_lock = threading.Lock()

    def emit(obj: dict) -> None:
        line = json.dumps(obj, ensure_ascii=False)
        with out_lock:
            log.write(line + "\n")
            log.flush()
            sys.stdout.write(line + "\n")
            sys.stdout.flush()

    proc = PtyProcess.spawn(argv, cwd=a.cwd, env=dict(os.environ), dimensions=(a.rows, a.cols))
    emit({"t": time.time(), "start": argv, "cols": a.cols, "rows": a.rows})

    def control() -> None:
        for raw in sys.stdin:
            raw = raw.strip()
            if not raw:
                continue
            msg = json.loads(raw)
            if msg["op"] == "write":
                emit({"t": time.time(), "input": msg["data"]})
                proc.write(msg["data"])
            elif msg["op"] == "kill":
                proc.terminate(force=True)
                return
            else:
                raise ValueError(f"unknown op {msg['op']!r}")

    threading.Thread(target=control, daemon=True).start()
    while True:
        try:
            data = proc.read(65536)
        except EOFError:
            break
        if data:
            emit({"t": time.time(), "data": data})
        elif not proc.isalive():
            break
    proc.wait() if hasattr(proc, "wait") else None
    emit({"t": time.time(), "exit": proc.exitstatus})
    log.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
