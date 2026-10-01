"""Shared fixtures. Every test uses the REAL local model server and a real temporary SQLite database."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest

from jig.config import load_config
from jig.events import Event
from jig.runtime import Jig


@pytest.fixture(scope="session")
def capabilities(tmp_path_factory) -> dict:
    """Run the real capability probes once against whatever model jig.toml (or JIG_CONFIG) points at."""
    base = tmp_path_factory.mktemp("caps")
    config = load_config(data_dir=base / "data", sandbox_dir=base / "sandbox")

    async def probe() -> dict:
        runtime = Jig(config)
        try:
            await runtime.start(run_scheduler=False)
            return runtime.capabilities
        finally:
            await runtime.stop()

    return asyncio.run(probe())


@pytest.fixture
def config(tmp_path: Path):
    return load_config(data_dir=tmp_path / "data", sandbox_dir=tmp_path / "sandbox")


@pytest.fixture
async def jig(config, capabilities) -> AsyncIterator[Jig]:
    # start() runs the real health check and fails loudly if the server is down; the
    # capability probes already ran once for the session.
    runtime = Jig(config)
    await runtime.start(run_scheduler=False, check_capabilities=False)
    runtime.capabilities = capabilities
    try:
        yield runtime
    finally:
        await runtime.stop()


@pytest.fixture
def events(jig: Jig) -> list[Event]:
    seen: list[Event] = []
    jig.bus.add_listener(seen.append)
    return seen


async def wait_for(predicate: Callable[[], object], *, timeout: float = 240.0, interval: float = 0.25,
                   what: str = "condition") -> object:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(interval)
    raise AssertionError(f"timed out after {timeout:.0f}s waiting for {what}")


def audit_kinds(jig: Jig, **filters) -> list[str]:
    return [r["kind"] for r in jig.audit.query(limit=5000, **filters)]
