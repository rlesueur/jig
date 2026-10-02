"""Overnight queue: run configs in order under the compute policy; resume where the last window stopped."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import yaml

from .compute_policy import NoPolicy, Policy
from .paths import HARNESS, LOGS
from .report import report
from .runner import FATAL, run

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


def entries(queue: Path) -> list[tuple[Path, int | None]]:
    """Queue entries as (config, trials per turn). A plain path runs to completion when its turn comes; an
    entry `{config: ..., per_turn: N}` runs at most N trials per turn, so such entries take turns."""
    out = []
    for entry in yaml.safe_load(queue.read_text(encoding="utf-8"))["queue"]:
        path, per_turn = (entry, None) if isinstance(entry, str) else (entry["config"], int(entry["per_turn"]))
        out.append(((HARNESS / path).resolve() if not Path(path).is_absolute() else Path(path), per_turn))
    return out


def overnight(queue: Path, policy: Policy | NoPolicy | None = None) -> int:
    """Returns 0 when the queue finished or paused politely, 1 when an entry could not start (for example a
    model server refused for lack of VRAM). Such an entry is logged as an error and the queue moves on to the
    next one; the refused entry is retried in the next window. Its finished trials are kept.

    The queue is gone through in rounds until a round finishes no new trial, so entries with `per_turn`
    alternate (an entry that keeps erroring stops a round from counting as progress)."""
    if not _acquire():
        return 0
    try:
        policy = policy or Policy()
        ok, why = policy.may_start()
        log.info("compute policy: %s", why)
        if not ok:
            return 0
        todo = entries(queue)
        blocked: list[str] = []
        progressed = True
        while progressed:
            progressed = False
            for cfg, per_turn in todo:
                if cfg.name in blocked:
                    continue
                log.info("queue: %s%s", cfg, f" (up to {per_turn} trials this turn)" if per_turn else "")
                try:
                    summary = asyncio.run(run(cfg, policy=policy, limit=per_turn, stop_services=True))
                except FATAL as exc:
                    log.error("queue: %s could not run now (%s: %s); moving on to the next entry",
                              cfg.name, type(exc).__name__, exc)
                    blocked.append(cfg.name)
                    continue
                log.info("queue: %s -> %s", cfg.name, summary)
                if summary["paused"]:
                    log.info("stopping for now: %s", summary["paused"])
                    return 1 if blocked else 0
                progressed |= summary["ran"] > summary["errors"]
        if blocked:
            log.error("queue incomplete: %s could not run; writing report", ", ".join(blocked))
            report()
            return 1
        log.info("queue complete; writing report")
        report()
        return 0
    finally:
        LOCK.unlink(missing_ok=True)
