"""The Windows autostart launcher: ``pythonw.exe -m jig.autostart.launch --config ... --data-dir ... --port ...``.

It runs ``jig serve`` as a child (no console window) and restarts it, a bounded number of times, if it
exits with an error: for example when the model server is not ready within ``[model.launch]
readiness_timeout_s``. A clean exit (``jig stop``, logoff) ends the launcher with exit code 0. If another
Jig already uses the data directory, it stops at once with that error instead of retrying. Its own log
is ``<data_dir>/logs/autostart.log``; Jig's is ``jig.log`` next to it.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from ..instance import EXIT_INSTANCE_LOCKED, read_info
from ..lifecycle import LOG_BACKUPS, LOG_FORMAT, LOG_MAX_BYTES, StopSignals
from .base import LaunchSpec

log = logging.getLogger("jig.autostart.launch")

# A child that ran at least this long before failing counts as a fresh failure, not a repeated one.
HEALTHY_RUN_S = 600.0


def _setup_logging(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="jig.autostart.launch")
    p.add_argument("--config", required=True)
    p.add_argument("--data-dir", required=True)
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--retry-delay", type=float, default=60.0)
    args = p.parse_args(argv)
    spec = LaunchSpec(config_path=Path(args.config), data_dir=Path(args.data_dir), host="127.0.0.1",
                      port=args.port, python=Path(sys.executable))
    _setup_logging(spec.data_dir / "logs" / "autostart.log")
    cmd = [str(spec.python), *spec.serve_args()]
    env = {**os.environ, "JIG_DATA_DIR": str(spec.data_dir)}

    child_done = threading.Event()
    # At logoff, hold the session open until the child (which handles the session end itself) has exited.
    signals = StopSignals(lambda why: log.info("session ending (%s); waiting for Jig to stop", why), None)
    signals.stopped = child_done
    signals.start()

    failures = 0
    while True:
        log.info("starting Jig: %s", subprocess.list2cmdline(cmd))
        started = time.monotonic()
        child_done.clear()
        proc = subprocess.Popen(cmd, env=env, cwd=str(spec.config_path.parent), stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        code = proc.wait()
        child_done.set()
        ran = time.monotonic() - started
        if code == 0:
            log.info("Jig stopped cleanly after %.0fs", ran)
            return 0
        if code == EXIT_INSTANCE_LOCKED:
            holder = read_info(spec.data_dir) or {}
            log.error("not started: another Jig (pid %s, port %s, started %s by %s) already holds the lock on %s; "
                      "not retrying. Stop it with 'jig stop' and run the task again (see jig.log)",
                      holder.get("pid", "?"), holder.get("port", "?"), holder.get("started_at", "?"),
                      holder.get("start_reason", "?"), spec.data_dir)
            return code
        failures = 1 if ran >= HEALTHY_RUN_S else failures + 1
        if failures > args.retries:
            log.error("Jig failed %d times in a row (last exit code %d); giving up. See %s", failures, code,
                      spec.log_path)
            return code
        log.warning("Jig exited with code %d after %.0fs; restarting in %.0fs (retry %d of %d). See %s", code, ran,
                    args.retry_delay, failures, args.retries, spec.log_path)
        time.sleep(args.retry_delay)


if __name__ == "__main__":
    raise SystemExit(main())
