"""Shared helpers: the reviewer/agent condition and its real Jig environment spec, plus run work dirs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..jigenv import Endpoint, EnvSpec


@dataclass
class Condition:
    """One reviewer/agent configuration; `server` entries are started by the runner when needed."""

    id: str
    agent: dict[str, Any]
    reviewer: str
    sentinel: dict[str, Any] | None
    rules: list[dict[str, Any]]
    memory_defence: str = "none"

    def env_spec(self, cfg: dict[str, Any], seed: int, endpoints: dict[str, str], **overrides: Any) -> EnvSpec:
        agent = Endpoint(base_url=endpoints[self.agent["endpoint"]], name=self.agent.get("name", ""))
        sentinel = None
        if self.reviewer in ("model", "guardian"):
            sentinel = Endpoint(base_url=endpoints[self.sentinel["endpoint"]], name=self.sentinel.get("name", ""))
        kw = dict(agent=agent, reviewer=self.reviewer, sentinel=sentinel, seed=seed, rules=self.rules,
                  memory_defence=self.memory_defence, sampling=cfg.get("sampling") or EnvSpec.__dataclass_fields__[
                      "sampling"].default_factory(), max_steps=cfg.get("max_steps", 10),
                  approval_policy=cfg.get("approval_policy", "approve_all"))
        kw.update(overrides)
        return EnvSpec(**kw)


def conditions(cfg: dict[str, Any]) -> list[Condition]:
    out = []
    for c in cfg["conditions"]:
        out.append(Condition(id=c["id"], agent=c.get("agent") or cfg["agent"], reviewer=c.get("reviewer", "model"),
                             sentinel=c.get("sentinel"), rules=c.get("rules", []),
                             memory_defence=c.get("memory_defence", "none")))
    return out


def workdir(ctx: Any, trial: dict[str, Any]) -> Path:
    return ctx.raw_dir / trial["trial_id"].replace("|", "__").replace(":", "-")
