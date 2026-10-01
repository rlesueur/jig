"""Overnight queue: run configs in order under the compute policy; resume where the last window stopped."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import yaml

from .compute_policy import Policy
from .paths import HARNESS, LOGS
from .report import report
from .runner import run

log = logging.getLogger("jigbench.overnight")
LOCK = LOGS / "overnight.lock"


def _acquire() -> bool:
    LOGS.mkdir(parents=True, exist_ok=True)
    if LOCK.exists():
        import psutil

        pid = int(LOCK.read_text().strip() or 0)
        if pid and psutil.pid_exists(pid):
            log.info("another overnight run (pid %d) is active; exiting", pid)
            return False
        log.warning("removing stale lock from pid %d", pid)
    LOCK.write_text(str(os.getpid()))
    return True


def overnight(queue: Path) -> int:
    if not _acquire():
        return 0
    try:
        policy = Policy()
        ok, why = policy.may_start()
        log.info("compute policy: %s", why)
        if not ok:
            return 0
        entries = yaml.safe_load(queue.read_text(encoding="utf-8"))["queue"]
        for entry in entries:
            cfg = (HARNESS / entry).resolve() if not Path(entry).is_absolute() else Path(entry)
            log.info("queue: %s", cfg)
            summary = asyncio.run(run(cfg, policy=policy, stop_services=True))
            log.info("queue: %s -> %s", cfg.name, summary)
            if summary["paused"]:
                log.info("stopping for now: %s", summary["paused"])
                return 0
        log.info("queue complete; writing report")
        report()
        return 0
    finally:
        LOCK.unlink(missing_ok=True)
