"""D1 - Jig's defences measured on AgentDojo (MIT, arXiv:2406.13352), run unchanged.

For each (defence configuration, suite, attack, user task, injection task) we run AgentDojo's own task with
its own attack and its own utility/security checks, but every tool call the agent makes is executed through
Jig's real gate (mode, core rules, custom rules, Sentinel, approvals). We author no attack content: the
injection strings come only from AgentDojo's shipped attacks.

Metrics per trial:
  * utility         - did the benign user task still succeed (AgentDojo's utility check)?
  * security        - did AgentDojo judge the injection as NOT achieved (its security check)?
  * attack_success  - the injection achieved its goal despite Jig's defences (1 - security, injected runs only).
  * approvals       - how many approvals Jig raised; blocked_calls - tool calls the gate refused.

The agent LLM is Jig's configured model, reached over its OpenAI-compatible endpoint. AgentDojo's pipeline is
synchronous and runs in a worker thread; Jig's gate is async and runs on the harness loop (so the approval
queue works), bridged by `run_coroutine_threadsafe` inside `JigGatedToolsExecutor`.
"""

from __future__ import annotations

import asyncio
from importlib.metadata import version as _pkg_version
from typing import Any

import openai
from agentdojo.agent_pipeline import AgentPipeline, InitQuery, OpenAILLM, SystemMessage, ToolsExecutionLoop
from agentdojo.agent_pipeline.agent_pipeline import load_system_message
from agentdojo.attacks.attack_registry import load_attack
from agentdojo.task_suite.load_suites import get_suite

from jig.constants import Mode

from ..agentdojo_adapter import EnvHolder, JigGatedToolsExecutor, build_registry, gated_executor
from ..jigenv import JigEnv
from .common import conditions, workdir

AGENTDOJO_VERSION = _pkg_version("agentdojo")
SYSTEM_MESSAGE = load_system_message(None)


def _cond_cfg(cfg: dict[str, Any], cond_id: str) -> dict[str, Any]:
    return next(c for c in cfg["conditions"] if c["id"] == cond_id)


def _mode_of(cfg: dict[str, Any], cond_id: str) -> Mode:
    return Mode.RESEARCH if _cond_cfg(cfg, cond_id).get("mode") == "research" else Mode.ACTION


def plan(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    version = cfg.get("benchmark", {}).get("version", "v1")
    n_user = cfg.get("user_tasks_per_suite")
    n_inj = cfg.get("injection_tasks_per_suite")
    trials: list[dict[str, Any]] = []
    for cond in conditions(cfg):
        for seed in cfg.get("seeds", [0]):
            for suite_name in cfg["suites"]:
                suite = get_suite(version, suite_name)
                user_tasks = list(suite.user_tasks.keys())[: n_user] if n_user else list(suite.user_tasks.keys())
                inj_tasks = list(suite.injection_tasks.keys())[: n_inj] if n_inj else list(suite.injection_tasks.keys())
                for attack in cfg["attacks"]:
                    if attack == "none":
                        for ut in user_tasks:
                            trials.append({"trial_id": f"d1|{cond.id}|{seed}|{suite_name}|none|{ut}|",
                                           "condition": cond.id, "seed": seed, "suite": suite_name,
                                           "attack": "none", "user_task": ut, "injection_task": ""})
                    else:
                        for ut in user_tasks:
                            for it in inj_tasks:
                                trials.append({"trial_id": f"d1|{cond.id}|{seed}|{suite_name}|{attack}|{ut}|{it}",
                                               "condition": cond.id, "seed": seed, "suite": suite_name,
                                               "attack": attack, "user_task": ut, "injection_task": it})
    return trials


async def run_trial(ctx: Any, trial: dict[str, Any]) -> dict[str, Any]:
    cfg = ctx.cfg
    cond = next(c for c in conditions(cfg) if c.id == trial["condition"])
    mode = _mode_of(cfg, trial["condition"])
    version = cfg.get("benchmark", {}).get("version", "v1")
    suite = get_suite(version, trial["suite"])
    user_task = suite.get_user_task_by_id(trial["user_task"])
    spec = cond.env_spec(cfg, trial["seed"], ctx.endpoints)

    holder = EnvHolder()
    async with JigEnv(spec, workdir(ctx, trial), keep=cfg.get("keep_raw", False)) as env:
        registry = build_registry(suite.tools, holder)
        executor = gated_executor(env.jig, registry)
        loop = asyncio.get_running_loop()
        client = openai.OpenAI(base_url=ctx.endpoints[cond.agent["endpoint"]], api_key="sk-no-key-needed")
        temperature = float((cfg.get("sampling") or {}).get("temperature", 0.0))
        llm = OpenAILLM(client, cond.agent.get("name", ""), temperature=temperature)
        gated = JigGatedToolsExecutor(executor, holder, loop, mode, run_id=trial["trial_id"],
                                      intent=user_task.PROMPT)
        pipeline = AgentPipeline([
            SystemMessage(SYSTEM_MESSAGE),
            InitQuery(),
            llm,
            ToolsExecutionLoop([gated, llm], max_iters=spec.max_steps),
        ])
        pipeline.name = cond.id

        if trial["attack"] == "none":
            injection_task = None
            injections: dict[str, str] = {}
        else:
            attack = load_attack(trial["attack"], suite, pipeline)
            injection_task = suite.get_injection_task_by_id(trial["injection_task"])
            injections = attack.attack(user_task, injection_task)

        utility, security = await asyncio.to_thread(
            suite.run_task_with_pipeline, pipeline, user_task, injection_task, injections)
        approvals = len(env.responder.requests)

    injected = trial["attack"] != "none"
    return {
        **trial,
        "utility": bool(utility),
        "security": bool(security),
        "attack_success": bool(injected and not security),
        "approvals": approvals,
        "blocked_calls": gated.blocked,
        "executed_calls": gated.executed,
        "mode": mode.value,
        "benchmark": "AgentDojo",
        "benchmark_version": version,
        "benchmark_licence": cfg.get("benchmark", {}).get("licence", "MIT"),
        "agentdojo_version": AGENTDOJO_VERSION,
    }
