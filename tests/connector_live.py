"""Shared by the opt-in live connector tests: one call through the real gate, answering its approval
like the user would, and a runtime on the user's own test config.

The runtime opens the config's own data directory, where the connections and their tokens are. A Jig
serving that config holds the directory, and two runtimes must never share one (a second copy of the
tokens would go stale when either refreshes them), so the live tests are skipped, saying which Jig to
stop, while one is running."""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from jig.config import Config, load_config
from jig.constants import Mode
from jig.instance import running_instance
from jig.model import ToolCall
from jig.policy.gate import CallContext
from jig.runtime import Jig


async def call(jig: Jig, name: str, args: dict, *, intent: str, approve: bool = True):
    run_id = f"r_live_{uuid.uuid4().hex[:8]}"
    task = asyncio.create_task(jig.executor.execute(
        ToolCall(id=uuid.uuid4().hex[:8], name=name, arguments_raw=json.dumps(args)),
        CallContext(run_id=run_id, task_id=None, mode=Mode.ACTION, intent=intent)))
    while not task.done():
        for a in jig.approvals.list(status="pending"):
            if a["run_id"] == run_id:
                jig.approvals.respond(a["id"], approve=approve, note="live test")
        await asyncio.sleep(0.2)
    return await task


def skip_if_in_use(config_path: str, config: Config) -> None:
    holder = running_instance(config.data_dir)
    if holder is not None:
        where = f" on {holder['host']}:{holder['port']}" if holder.get("port") else ""
        pytest.skip(f"a Jig (pid {holder.get('pid')}{where}) is serving {config_path} and holds its data directory "
                    f"{config.data_dir}; stop it with 'jig --config {config_path} stop' to run this live test, "
                    "then start it again")


@asynccontextmanager
async def live_runtime(config_path: str, provider: str):
    config = load_config(Path(config_path))
    skip_if_in_use(config_path, config)
    runtime = Jig(config)
    await runtime.start(run_scheduler=False, check_capabilities=False)
    try:
        row = runtime.connections.get(provider)
        assert row and row["status"] == "connected", f"{provider} is not connected in {config_path}'s data dir"
        yield runtime
    finally:
        await runtime.stop()
