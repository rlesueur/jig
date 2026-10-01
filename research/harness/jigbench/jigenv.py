"""A real, isolated Jig instance per trial.

Each trial gets its own data directory (SQLite database, audit log, memory) and sandbox, a generated Jig
TOML config pointing at the agent and reviewer endpoints, and the benchmark host routing. Tasks run through
Jig's real scheduler, agent loop, gate, Sentinel and approval queue.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from jig.config import load_config
from jig.constants import EventType, Mode, TaskStatus
from jig.runtime import Jig

from . import memdefence, netroute
from .reviewers import GuardianReviewer, PassThroughReviewer, ProvenanceSentinel, ProvenanceTracker

TERMINAL = {TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.BLOCKED}


@dataclass
class Endpoint:
    base_url: str
    name: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Endpoint":
        return cls(base_url=d["base_url"], name=d.get("name", ""))


@dataclass
class EnvSpec:
    agent: Endpoint
    reviewer: str = "model"                     # model | none | rules-only
    sentinel: Endpoint | None = None            # required when reviewer == "model"
    sampling: dict[str, Any] = field(default_factory=lambda: {"temperature": 1.0, "top_p": 0.95, "top_k": 20,
                                                               "min_p": 0.05})
    seed: int = 0
    max_steps: int = 10
    max_tokens: int = 8192
    rules: list[dict[str, Any]] = field(default_factory=list)
    approval_policy: str = "approve_all"        # approve_all | deny_all | oracle
    memory_defence: str = "none"
    read_timeout_s: float = 900.0

    def __post_init__(self) -> None:
        if self.reviewer not in ("model", "guardian", "none", "rules-only"):
            raise ValueError(f"unknown reviewer {self.reviewer!r}")
        if self.reviewer in ("model", "guardian") and self.sentinel is None:
            raise ValueError(f"reviewer {self.reviewer!r} needs a sentinel endpoint")
        if self.approval_policy not in ("approve_all", "deny_all", "oracle"):
            raise ValueError(f"unknown approval policy {self.approval_policy!r}")
        memdefence.parse(self.memory_defence)


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return json.dumps(str(v))


def write_config(spec: EnvSpec, root: Path) -> Path:
    sentinel = spec.sentinel or spec.agent
    sampling = {**spec.sampling, "seed": spec.seed}
    # The Granite Guardian card prescribes greedy scoring.
    s_sampling = {"temperature": 0.0, "seed": spec.seed} if spec.reviewer == "guardian" else sampling
    lines = [
        "[model]", f"base_url = {_toml_value(spec.agent.base_url)}", f"name = {_toml_value(spec.agent.name)}",
        f"read_timeout_s = {spec.read_timeout_s}", f"max_tokens = {spec.max_tokens}", "",
        "[model.sampling]", *[f"{k} = {_toml_value(v)}" for k, v in sampling.items()], "",
        "[sentinel]", f"base_url = {_toml_value(sentinel.base_url)}", f"name = {_toml_value(sentinel.name)}",
        f"read_timeout_s = {spec.read_timeout_s}", f"max_tokens = {spec.max_tokens}", "",
        "[sentinel.sampling]", *[f"{k} = {_toml_value(v)}" for k, v in s_sampling.items()], "",
        "[vision]", "enabled = false", "",
        "[sandbox]", 'backend = "directory"', "",
        "[paths]", 'data_dir = "data"', 'sandbox_dir = "sandbox"', "",
        "[runtime]", 'agent_id = "bench"', 'timezone = "Europe/London"', f"max_steps = {spec.max_steps}",
        "max_concurrent_tasks = 1", "heartbeat_s = 0.5", "min_context_tokens = 16384", "",
        "[server]", 'host = "127.0.0.1"', "port = 8766", "",
    ]
    path = root / "jig.toml"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


class ApprovalResponder:
    """Stands in for the user at the approval queue (benchmark condition), and records every request."""

    def __init__(self, jig: Jig, policy: str, harmful: Callable[[str, dict[str, Any]], bool] | None):
        self.jig, self.policy, self.harmful = jig, policy, harmful
        self.requests: list[dict[str, Any]] = []

    def __call__(self, event: Any) -> None:
        if event.type != EventType.APPROVAL_REQUESTED or event.data.get("resumed"):
            return
        d = event.data
        if self.policy == "approve_all":
            approve = True
        elif self.policy == "deny_all":
            approve = False
        else:
            if self.harmful is None:
                raise RuntimeError("approval policy 'oracle' needs a harmfulness predicate")
            approve = not self.harmful(d["tool"], d.get("args") or {})
        self.requests.append({"approval_id": d["approval_id"], "task_id": d.get("task_id"), "tool": d["tool"],
                              "args": d.get("args"), "reasons": d.get("reasons"), "approved": approve})
        asyncio.get_running_loop().call_soon(
            lambda: self.jig.approvals.respond(d["approval_id"], approve=approve,
                                               note=f"jigbench simulated user ({self.policy})", actor="jigbench"))


class JigEnv:
    def __init__(self, spec: EnvSpec, workdir: Path, *, harmful: Callable[[str, dict[str, Any]], bool] | None = None,
                 keep: bool = True):
        self.spec, self.workdir, self.keep = spec, workdir, keep
        self.harmful = harmful
        self.intents: dict[str, str] = {}
        self.tracker = ProvenanceTracker()
        self.stats: dict[str, int] = {}
        self.sentinel_usage: list[dict[str, Any]] = []
        self.jig: Jig | None = None
        self._exempt = None

    async def __aenter__(self) -> "JigEnv":
        if self.workdir.exists():
            shutil.rmtree(self.workdir)
        self.workdir.mkdir(parents=True)
        config = load_config(write_config(self.spec, self.workdir))
        self._exempt = netroute.benchmark_hosts_exempt(_routes())
        self._exempt.__enter__()
        jig = Jig(config)
        self.jig = jig
        old_http = netroute.install(jig, _routes())
        await old_http.aclose()
        self._instrument_sentinel_model()
        if self.spec.reviewer in ("none", "rules-only"):
            jig.sentinel = jig.executor.sentinel = PassThroughReviewer()
        elif self.spec.reviewer == "guardian":
            jig.sentinel = jig.executor.sentinel = GuardianReviewer(jig.sentinel_model)
        elif "provenance" in memdefence.parse(self.spec.memory_defence):
            jig.sentinel = jig.executor.sentinel = ProvenanceSentinel(jig.sentinel_model, self.tracker)
        memdefence.install(jig, self.spec.memory_defence, self.intents, self.tracker, self.stats)
        for rule in self.spec.rules:
            jig.rules.create(**rule)
        self.responder = ApprovalResponder(jig, self.spec.approval_policy, self.harmful)
        jig.bus.add_listener(self.responder)
        await jig.start(run_scheduler=True, check_capabilities=False)
        return self

    def _instrument_sentinel_model(self) -> None:
        """Count the Sentinel's tokens and latency (Jig records only the verdict and elapsed time)."""
        client = self.jig.sentinel_model
        chat = client.chat

        async def counted(*args: Any, **kwargs: Any):
            result = await chat(*args, **kwargs)
            self.sentinel_usage.append({"usage": result.usage, "elapsed_s": result.elapsed_s})
            return result

        client.chat = counted  # type: ignore[method-assign]

    async def __aexit__(self, *exc: Any) -> None:
        try:
            if self.jig is not None:
                await self.jig.stop()
        finally:
            if self._exempt is not None:
                self._exempt.__exit__(None, None, None)
            if not self.keep:
                shutil.rmtree(self.workdir, ignore_errors=True)

    # Convenience -----------------------------------------------------------------------------------
    def seed_memories(self, contents: list[str], *, origin: str = "user") -> list[dict[str, Any]]:
        return [self.jig.memory.add(c, tags=[f"origin:{origin}"], source="jigbench:seed") for c in contents]

    async def run_task(self, title: str, description: str, mode: Mode, *, timeout_s: float = 900) -> dict[str, Any]:
        task = self.jig.create_task(title=title, description=description, mode=mode)
        self.intents[task["id"]] = f"{title}: {description}"
        started = time.monotonic()
        while True:
            task = self.jig.store.get_task(task["id"])
            if task["status"] in TERMINAL:
                break
            if time.monotonic() - started > timeout_s:
                self.jig.cancel_task(task["id"])
                task = {**self.jig.store.get_task(task["id"]), "timed_out": True}
                break
            await asyncio.sleep(0.25)
        task["wall_s"] = round(time.monotonic() - started, 2)
        return task

    def audit(self, *, task_id: str | None = None, kind: str | None = None) -> list[dict[str, Any]]:
        rows = self.jig.audit.query(task_id=task_id, kind=kind, limit=100_000)
        for r in rows:
            r["data"] = json.loads(r.pop("data_json") or "{}")
        return rows


def _routes() -> dict[str, int]:
    from .services import routes
    return routes()
