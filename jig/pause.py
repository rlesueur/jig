"""Pausing a run at a safe point.

A pause interrupts a run only where nothing has happened outside Jig yet: between
steps, during a model call, or while waiting for an approval. A tool that is
already executing is allowed to finish, so a side effect is never cut off half
way and repeated on resume. The run's checkpoint is kept and it resumes from it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

T = TypeVar("T")


class RunPaused(Exception):
    """The run stopped at a safe point because the user paused it.

    Deliberately not a ``JigError``: it must never be turned into a tool error or a failed task.
    """


async def until_paused(work: Awaitable[T], pause: asyncio.Event | None, what: str) -> T:
    """Await ``work``, or cancel it and raise ``RunPaused`` if ``pause`` is set first."""
    if pause is None:
        return await work
    if pause.is_set():
        if asyncio.iscoroutine(work):
            work.close()
        raise RunPaused(f"paused before {what}")
    job = asyncio.ensure_future(work)
    waiter = asyncio.ensure_future(pause.wait())
    try:
        done, _ = await asyncio.wait({job, waiter}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        waiter.cancel()
        if not job.done():
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
    if job in done:
        return job.result()
    raise RunPaused(f"paused during {what}")
