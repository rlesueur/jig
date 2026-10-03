"""Compare how Jig chooses the memories it puts in front of the model, on Jig's own C1 memory benchmark.

Uses C1's ``retrieval`` setup (research/harness/jigbench/experiments/c1_memory.py, LongMemEval-S): every turn
of a question's past conversations is saved as a Jig memory, then a Jig research task answers the question,
graded with LongMemEval's own judge prompts. Jig is imported from ``--src``, so two versions can be compared,
and ``--memory-prompt`` sets ``[runtime] memory_prompt`` for versions that have it.

Two ways of asking: ``c1`` is C1's own question ("Use memory_search (try several queries) ..."); ``plain``
asks the question alone, as a user would in chat, so the memories Jig puts in the prompt matter more.

    python scripts/measure_memory_prompt.py --src <jig source tree> --base-url http://127.0.0.1:8080/v1 \
        --label after-relevant --memory-prompt relevant --out results.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import shutil
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASKS = ("c1", "plain")


def question_text(q: dict, ask: str) -> str:
    if ask == "c1":
        return (f"The current date is {q['question_date']}. Use memory_search (try several queries) to recall what "
                f"the user told you in past conversations, then answer concisely: {q['question']}")
    return f"The current date is {q['question_date']}. {q['question']}"


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--src", required=True, help="a Jig source tree (the folder that holds jig/)")
    p.add_argument("--base-url", required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--memory-prompt", default="", help="relevant or recent; leave out for versions without it")
    p.add_argument("--asks", default=",".join(ASKS))
    p.add_argument("--per-type", type=int, default=5)
    p.add_argument("--out", required=True, help="JSON lines, appended to; questions already in it are skipped")
    a = p.parse_args()
    sys.path.insert(0, str(Path(a.src).resolve()))
    sys.path.insert(1, str(ROOT / "research" / "harness"))
    # Only C1 is needed: skip jigbench.experiments' __init__, which imports every experiment and their packages.
    experiments = types.ModuleType("jigbench.experiments")
    experiments.__path__ = [str(ROOT / "research" / "harness" / "jigbench" / "experiments")]
    sys.modules["jigbench.experiments"] = experiments
    os.environ["JIG_MODEL_BASE_URL"] = a.base_url
    import jig as jig_package
    from jig.config import EndpointConfig, load_config
    from jig.constants import Mode
    from jig.model import ModelClient
    from jig.runtime import Jig
    from jigbench.experiments.c1_memory import judge_prompt, select_questions, sessions_for, split_text

    assert Path(jig_package.__file__).resolve().is_relative_to(Path(a.src).resolve()), jig_package.__file__
    qtypes = ["knowledge-update", "temporal-reasoning", "multi-session", "single-session-user",
              "single-session-assistant", "single-session-preference"]
    questions = select_questions({"questions_per_type": {t: a.per_type for t in qtypes}, "question_seed": 0})
    out = Path(a.out)
    done = {(r["question_id"], r["ask"]) for r in map(json.loads, out.read_text(encoding="utf-8").splitlines())
            } if out.exists() else set()
    judge = ModelClient(EndpointConfig(base_url=a.base_url, max_tokens=4096,
                                       sampling={"temperature": 0.0, "seed": 0}), label="judge model")
    await judge.health()
    for ask in a.asks.split(","):
        for q in questions:
            if (q["question_id"], ask) in done:
                continue
            base = Path(tempfile.mkdtemp(prefix=f"jig-mem-{a.label}-"))
            config = load_config(Path(a.src) / "jig.toml", data_dir=base / "data", sandbox_dir=base / "sandbox")
            if a.memory_prompt:
                config = dataclasses.replace(config, runtime=dataclasses.replace(
                    config.runtime, memory_prompt=a.memory_prompt))
            runtime = Jig(config)
            await runtime.start(run_scheduler=False, check_capabilities=False)
            calls: list[dict] = []
            original = runtime.model.chat

            async def chat(messages, **kw):
                result = await original(messages, **kw)
                calls.append({"prompt_tokens": int((result.usage or {}).get("prompt_tokens") or 0),
                              "tools": [c.name for c in result.tool_calls]})
                return result

            runtime.model.chat = chat
            started = time.perf_counter()
            declined = 0
            try:
                for s in sessions_for(q, 10, 0):
                    for t in s["turns"]:
                        for piece in split_text(t["content"], 1500):
                            runtime.memory.add(f"[{s['date']}] {t['role']}: {piece}", kind="episode",
                                               tags=["origin:conversation"], source=f"session:{s['id']}")
                memories = len(runtime.memory.list(limit=100_000))
                task = runtime.create_task(title="Answer a question about past conversations",
                                           description=question_text(q, ask), mode=Mode.RESEARCH)
                # Nobody is there to answer an approval card: decline it, the same way in every version.
                running = asyncio.create_task(runtime.run_task(task["id"]))
                while not running.done():
                    for approval in runtime.approvals.list(status="pending"):
                        runtime.approvals.respond(approval["id"], approve=False,
                                                  note="Nobody can approve this here; answer without it.")
                        declined += 1
                    await asyncio.sleep(0.5)
                await running
                task = runtime.store.get_task(task["id"])
            finally:
                await runtime.stop()
                shutil.rmtree(base, ignore_errors=True)
            response = task.get("result") or f"[task {task['status']}: {task.get('error')}]"
            answer = q["answer"] if isinstance(q["answer"], str) else json.dumps(q["answer"])
            verdict = await judge.chat([{"role": "user", "content": judge_prompt(
                q["question_type"], q["question"], answer, response, q["question_id"].endswith("_abs"))}])
            tail = verdict.content.strip().lower().split("</think>")[-1].strip().rstrip(".")
            if not tail.startswith(("yes", "no")):
                raise RuntimeError(f"judge reply is neither yes nor no: {verdict.content[:200]!r}")
            row = {"label": a.label, "question_id": q["question_id"], "question_type": q["question_type"],
                   "ask": ask, "correct": tail.startswith("yes"), "status": task["status"],
                   "outcome": task.get("outcome"),
                   "model_calls": len(calls), "prompt_tokens": sum(c["prompt_tokens"] for c in calls),
                   "searches": sum(c["tools"].count("memory_search") for c in calls), "memories": memories,
                   "approvals_declined": declined,
                   "wall_s": round(time.perf_counter() - started, 1), "question": q["question"],
                   "answer": answer, "response": response[:1500]}
            with out.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
            print(f"{a.label} {ask} {q['question_id']} {q['question_type']}: correct={row['correct']} "
                  f"calls={row['model_calls']} searches={row['searches']}", flush=True)
    await judge.aclose()


if __name__ == "__main__":
    asyncio.run(main())
