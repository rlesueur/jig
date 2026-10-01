"""Run an experiment config: plan trials, start what they need, run them resumably, write JSONL.

Results: research/results/<experiment>/<run name>/
  run.json      provenance (models, quantisation, server builds, hardware, commit, seeds, config)
  trials.jsonl  one record per finished trial (resumable: finished trial ids are skipped)
  raw/          per-trial Jig data directories when keep_raw is set (gitignored)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from jig.errors import ModelServerUnavailable

from . import provenance
from .compute_policy import NoPolicy, Paused, Policy
from .experiments import EXPERIMENTS
from .experiments.common import conditions
from .paths import RESULTS
from .servers import LlamaServer, ServerError, ServerSpec
from .services import BenchServices, ServiceError

log = logging.getLogger("jigbench")
FATAL = (ModelServerUnavailable, ServerError, ServiceError)


def load_config(path: Path) -> dict[str, Any]:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in ("name", "experiment", "endpoints", "conditions"):
        if key not in cfg:
            raise ValueError(f"{path}: missing required key {key!r}")
    if cfg["experiment"] not in EXPERIMENTS:
        raise ValueError(f"{path}: unknown experiment {cfg['experiment']!r}")
    cfg["_config_path"] = str(path)
    return cfg


def run_dir(cfg: dict[str, Any]) -> Path:
    return RESULTS / cfg["experiment"] / cfg["name"]


def finished_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            if rec.get("status_harness") == "ok":
                out.add(rec["trial_id"])
    return out


def endpoints_for(cfg: dict[str, Any], cond_id: str) -> set[str]:
    cond = next(c for c in conditions(cfg) if c.id == cond_id)
    names = {cond.agent["endpoint"]}
    if cond.reviewer in ("model", "guardian"):
        names.add(cond.sentinel["endpoint"])
    if "judge" in cfg:
        names.add(cfg["judge"]["endpoint"])
    return names


@dataclass
class RunContext:
    cfg: dict[str, Any]
    out_dir: Path
    raw_dir: Path
    services: BenchServices
    endpoints: dict[str, str] = field(default_factory=dict)
    servers: dict[str, LlamaServer] = field(default_factory=dict)
    endpoint_info: dict[str, Any] = field(default_factory=dict)

    def ensure(self, names: set[str]) -> None:
        for name in [n for n in self.servers if n not in names]:
            log.info("stopping server for endpoint %s", name)
            self.servers.pop(name).stop()
            self.endpoints.pop(name, None)
        for name in sorted(names):
            if name in self.endpoints:
                continue
            ep = self.cfg["endpoints"][name]
            if "server" in ep:
                srv = LlamaServer(ServerSpec(**ep["server"]))
                log.info("starting %s on port %s", srv.spec.model, srv.spec.port)
                srv.start()
                self.servers[name] = srv
                self.endpoints[name] = srv.base_url
                self.endpoint_info[name] = {**provenance.endpoint_info(srv.base_url), "server": srv.describe()}
            else:
                self.endpoints[name] = ep["base_url"]
                self.endpoint_info[name] = provenance.endpoint_info(ep["base_url"])
            self.write_run_json()

    def stop_all(self) -> None:
        for srv in self.servers.values():
            srv.stop()
        self.servers.clear()
        self.endpoints.clear()

    def write_run_json(self) -> None:
        path = self.out_dir / "run.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        data.setdefault("sessions", [])
        if not data["sessions"] or data["sessions"][-1].get("closed"):
            cfg = {k: v for k, v in self.cfg.items() if not k.startswith("_")}
            data["sessions"].append({**provenance.capture(cfg, {}), "config_path": self.cfg["_config_path"],
                                     "services": self.services.describe(), "endpoints": {}})
        data["sessions"][-1]["endpoints"].update(self.endpoint_info)
        data["name"], data["experiment"] = self.cfg["name"], self.cfg["experiment"]
        path.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")

    def close_session(self, reason: str) -> None:
        path = self.out_dir / "run.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("sessions"):
                data["sessions"][-1].update(closed=True, closed_reason=reason,
                                            closed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
                path.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")


async def _guarded(coro: Any, policy: Any, poll_s: float = 5.0) -> Any:
    task = asyncio.ensure_future(coro)
    while True:
        done, _ = await asyncio.wait({task}, timeout=poll_s)
        if done:
            return task.result()
        if reason := policy.must_stop():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise Paused(reason)


async def run(config_path: Path, *, policy: Policy | NoPolicy | None = None, limit: int | None = None,
              stop_services: bool = False) -> dict[str, Any]:
    policy = policy or NoPolicy()
    cfg = load_config(config_path)
    exp = EXPERIMENTS[cfg["experiment"]]
    out_dir = run_dir(cfg)
    out_dir.mkdir(parents=True, exist_ok=True)
    trials_path = out_dir / "trials.jsonl"
    done = finished_ids(trials_path)
    todo = [t for t in exp.plan(cfg) if t["trial_id"] not in done]
    todo.sort(key=lambda t: t["condition"])  # keep each condition's servers up for all its trials
    if limit is not None:
        todo = todo[:limit]
    summary = {"planned": len(todo) + len(done), "already_done": len(done), "ran": 0, "errors": 0, "paused": None}
    log.info("%s: %d trials to run (%d already done)", cfg["name"], len(todo), len(done))
    if not todo:
        return summary
    ok, why = policy.may_start()
    if not ok:
        summary["paused"] = why
        return summary
    services = BenchServices()
    services.start()
    ctx = RunContext(cfg=cfg, out_dir=out_dir, raw_dir=out_dir / "raw", services=services)
    ctx.write_run_json()
    reason = "finished"
    try:
        for trial in todo:
            if why := policy.between_trials():
                raise Paused(why)
            ctx.ensure(endpoints_for(cfg, trial["condition"]))
            started = time.time()
            rec: dict[str, Any]
            try:
                result = await _guarded(exp.run_trial(ctx, trial), policy)
                rec = {**result, "status_harness": "ok"}
            except (Paused, *FATAL):
                raise
            except Exception as exc:
                summary["errors"] += 1
                log.error("trial %s failed: %s", trial["trial_id"], exc)
                rec = {**trial, "status_harness": "error", "harness_error": f"{type(exc).__name__}: {exc}",
                       "traceback": traceback.format_exc()[-3000:]}
                if cfg.get("stop_on_error", False):
                    raise
            rec.update(started_at=datetime.fromtimestamp(started, timezone.utc).isoformat(timespec="seconds"),
                       duration_s=round(time.time() - started, 2),
                       endpoint_models={n: (ctx.endpoint_info.get(n) or {}).get("model_id")
                                        for n in endpoints_for(cfg, trial["condition"])})
            with trials_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            summary["ran"] += 1
            log.info("[%d/%d] %s -> %s", summary["ran"], len(todo), trial["trial_id"], rec["status_harness"])
    except Paused as p:
        reason = f"paused: {p}"
        summary["paused"] = str(p)
        log.warning("run paused: %s", p)
    finally:
        ctx.stop_all()
        ctx.close_session(reason)
        if stop_services:
            services.stop()
    return summary
