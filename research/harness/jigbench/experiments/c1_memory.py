"""C1 - Memory strategies over long multi-session histories (adapted from LongMemEval, MIT licence).

Data: LongMemEval-S (cleaned), https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned, which ships
timestamped multi-session user-assistant histories and later questions of five types, including
knowledge updates (staleness) and temporal reasoning. Each question's history is replayed session by
session in date order (simulated clock), then the question is asked "on" its question date.

Strategies (all on top of Jig's real model client, memory store and agent loop):
* full_context    - the most recent sessions that fit `context_budget_tokens`, placed in the prompt.
* rolling_summary - after each session the model rewrites a bounded summary; the answer sees only it.
* retrieval       - every turn is stored as a Jig memory; the Jig agent answers with `memory_search`.
* hybrid_keep     - per session, a Jig research task extracts dated facts with `memory_add`; every
                    `consolidate_every` sessions an action task reviews the memory list and adds
                    "UPDATE" notes for superseded facts (no forgetting); the agent answers via `memory_search`.
* hybrid_forget   - as hybrid_keep, but consolidation removes superseded facts with `memory_forget`
                    (a side-effecting tool, so Jig's Sentinel reviews every forget).

Grading uses LongMemEval's own judge prompts (ported verbatim from src/evaluation/evaluate_qa.py) on a
judge model that is configured separately from the agent and recorded with the results.
"""

from __future__ import annotations

import json
import random
import time
from functools import lru_cache
from typing import Any

from jig.constants import Mode
from jig.model import ModelClient

from ..jigenv import JigEnv
from ..paths import DATA
from ..trace import model_usage
from .common import conditions, workdir

DATASET = DATA / "longmemeval" / "longmemeval_s_cleaned.json"
STRATEGIES = ("full_context", "rolling_summary", "retrieval", "hybrid_keep", "hybrid_forget")

# --- LongMemEval judge prompts (MIT licence, (c) the LongMemEval authors), ported verbatim ---------------
_BASE = ("I will give you a question, a correct answer, and a response from a model. Please answer yes if the "
         "response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct "
         "answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If "
         "the response only contains a subset of the information required by the answer, answer no. ")


def judge_prompt(task: str, question: str, answer: str, response: str, abstention: bool) -> str:
    if abstention:
        t = ("I will give you an unanswerable question, an explanation, and a response from a model. Please answer "
             "yes if the model correctly identifies the question as unanswerable. The model could say that the "
             "information is incomplete, or some other information is given but the asked information is not.\n\n"
             "Question: {}\n\nExplanation: {}\n\nModel Response: {}\n\nDoes the model correctly identify the "
             "question as unanswerable? Answer yes or no only.")
    elif task in ("single-session-user", "single-session-assistant", "multi-session"):
        t = _BASE + "\n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
    elif task == "temporal-reasoning":
        t = (_BASE + "In addition, do not penalize off-by-one errors for the number of days. If the question asks "
             "for the number of days/weeks/months, etc., and the model makes off-by-one errors (e.g., predicting 19 "
             "days when the answer is 18), the model's response is still correct. \n\nQuestion: {}\n\nCorrect "
             "Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only.")
    elif task == "knowledge-update":
        t = ("I will give you a question, a correct answer, and a response from a model. Please answer yes if the "
             "response contains the correct answer. Otherwise, answer no. If the response contains some previous "
             "information along with an updated answer, the response should be considered as correct as long as "
             "the updated answer is the required answer.\n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: "
             "{}\n\nIs the model response correct? Answer yes or no only.")
    elif task == "single-session-preference":
        t = ("I will give you a question, a rubric for desired personalized response, and a response from a model. "
             "Please answer yes if the response satisfies the desired response. Otherwise, answer no. The model does "
             "not need to reflect all the points in the rubric. The response is correct as long as it recalls and "
             "utilizes the user's personal information correctly.\n\nQuestion: {}\n\nRubric: {}\n\nModel Response: "
             "{}\n\nIs the model response correct? Answer yes or no only.")
    else:
        raise ValueError(f"unknown LongMemEval task type {task!r}")
    return t.format(question, answer, response)
# -----------------------------------------------------------------------------------------------------------


@lru_cache
def dataset() -> list[dict[str, Any]]:
    if not DATASET.exists():
        raise FileNotFoundError(f"{DATASET} missing; download longmemeval_s_cleaned.json (see C1 docs)")
    return json.loads(DATASET.read_text(encoding="utf-8"))


def select_questions(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    data = dataset()
    if cfg.get("question_ids"):
        wanted = set(cfg["question_ids"])
        return [q for q in data if q["question_id"] in wanted]
    rng = random.Random(cfg.get("question_seed", 0))
    out = []
    for qtype, n in (cfg.get("questions_per_type") or {}).items():
        pool = [q for q in data if q["question_type"] == qtype and not q["question_id"].endswith("_abs")]
        out += rng.sample(pool, n)
    return out


def sessions_for(q: dict[str, Any], max_sessions: int | None, seed: int = 0) -> list[dict[str, Any]]:
    """Answer-bearing sessions plus distractors, in date order. `max_sessions` subsamples distractors."""
    rows = [{"id": sid, "date": d, "turns": s} for sid, d, s in
            zip(q["haystack_session_ids"], q["haystack_dates"], q["haystack_sessions"])]
    if max_sessions:
        answer = [r for r in rows if r["id"] in set(q["answer_session_ids"])]
        others = [r for r in rows if r["id"] not in set(q["answer_session_ids"])]
        keep = max(0, max_sessions - len(answer))
        others = random.Random(seed).sample(others, min(keep, len(others)))
        chosen = {r["id"] for r in answer + others}
        rows = [r for r in rows if r["id"] in chosen]
    return rows


def session_text(s: dict[str, Any]) -> str:
    return "\n".join(f"{t['role']}: {t['content']}" for t in s["turns"])


def split_text(text: str, max_chars: int) -> list[str]:
    """Consecutive pieces of at most `max_chars`, breaking at line ends where possible. Nothing is dropped."""
    pieces, cur = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > max_chars:
            if cur:
                pieces.append(cur)
                cur = ""
            pieces.append(line[:max_chars])
            line = line[max_chars:]
        if len(cur) + len(line) > max_chars:
            pieces.append(cur)
            cur = ""
        cur += line
    if cur:
        pieces.append(cur)
    return pieces


CHUNK_CHARS = 6000


def plan(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    qs = select_questions(cfg)
    trials = []
    for cond in conditions(cfg):
        for seed in cfg.get("seeds", [0]):
            for q in qs:
                for strat in cfg.get("strategies") or STRATEGIES:
                    trials.append({"trial_id": f"c1|{cond.id}|{seed}|{q['question_id']}|{strat}",
                                   "condition": cond.id, "seed": seed, "question_id": q["question_id"],
                                   "question_type": q["question_type"], "strategy": strat})
    return trials


def _usage_sum(results: list[Any]) -> dict[str, int]:
    return {"prompt_tokens": sum(int((r.usage or {}).get("prompt_tokens") or 0) for r in results),
            "completion_tokens": sum(int((r.usage or {}).get("completion_tokens") or 0) for r in results)}


ANSWER_SYSTEM = ("You are a personal assistant answering a question about your past conversations with the user. "
                 "Answer concisely in British English. If the information is not available, say so.")


async def _answer_from_context(model: ModelClient, context: str, q: dict[str, Any]) -> Any:
    msgs = [{"role": "system", "content": ANSWER_SYSTEM},
            {"role": "user", "content": f"{context}\n\nThe current date is {q['question_date']}.\n"
                                        f"Question: {q['question']}"}]
    return await model.chat(msgs)


async def run_trial(ctx: Any, trial: dict[str, Any]) -> dict[str, Any]:
    cfg = ctx.cfg
    cond = next(c for c in conditions(cfg) if c.id == trial["condition"])
    q = next(x for x in dataset() if x["question_id"] == trial["question_id"])
    sessions = sessions_for(q, cfg.get("max_sessions"), trial["seed"])
    strat = trial["strategy"]
    budget_chars = int(cfg.get("context_budget_tokens", 16000) * 3.5)
    spec = cond.env_spec(cfg, trial["seed"], ctx.endpoints)
    started = time.monotonic()
    ingest_results: list[Any] = []
    answer_tokens: dict[str, int] = {}
    approvals = 0
    async with JigEnv(spec, workdir(ctx, trial), keep=cfg.get("keep_raw", False)) as env:
        model = env.jig.model
        if strat == "full_context":
            blocks, used = [], 0
            for s in reversed(sessions):
                b = f"Conversation on {s['date']}:\n{session_text(s)}"
                if used + len(b) > budget_chars:
                    room = budget_chars - used
                    if room > 500:
                        blocks.insert(0, f"Conversation on {s['date']} (earlier part omitted):\n"
                                         f"{session_text(s)[-room:]}")
                    break
                blocks.insert(0, b)
                used += len(b)
            res = await _answer_from_context(model, "Past conversations:\n\n" + "\n\n".join(blocks), q)
            response, answer_tokens = res.content, _usage_sum([res])
        elif strat == "rolling_summary":
            summary = "(empty)"
            for s in sessions:
                parts = split_text(session_text(s), CHUNK_CHARS)
                for k, part in enumerate(parts, start=1):
                    r = await model.chat([
                        {"role": "system", "content": "You maintain a concise long-term memory summary about the user."},
                        {"role": "user", "content": f"Current summary:\n{summary}\n\nNew conversation on {s['date']} "
                                                    f"(part {k} of {len(parts)}):\n{part}\n\nRewrite the summary to "
                                                    "include every durable fact about the user, each with its date. "
                                                    "Replace facts that have changed. Keep it under 400 words. Reply "
                                                    "with the summary only."}])
                    ingest_results.append(r)
                    summary = r.content.strip() or summary
            res = await _answer_from_context(model, f"Memory summary:\n{summary}", q)
            response, answer_tokens = res.content, _usage_sum([res])
        else:
            timeout = cfg.get("task_timeout_s", 900)
            if strat == "retrieval":
                for s in sessions:
                    for t in s["turns"]:
                        for piece in split_text(t["content"], 1500):
                            env.jig.memory.add(f"[{s['date']}] {t['role']}: {piece}", kind="episode",
                                               tags=["origin:conversation"], source=f"session:{s['id']}")
            else:
                for i, s in enumerate(sessions, start=1):
                    parts = split_text(session_text(s), CHUNK_CHARS)
                    for k, part in enumerate(parts, start=1):
                        t = await env.run_task(
                            f"Memory extraction: conversation on {s['date']} (part {k} of {len(parts)})",
                            f"Below is part {k} of {len(parts)} of a conversation you had with the user on "
                            f"{s['date']}. Save every durable fact about the user (preferences, plans, possessions, "
                            "events, numbers) with memory_add, one fact per call, each starting with the date "
                            f"[{s['date'][:10]}]. Then reply 'done'.\n\n{part}", Mode.RESEARCH, timeout_s=timeout)
                        ingest_results.append(t)
                    if i % cfg.get("consolidate_every", 3) == 0 or i == len(sessions):
                        mems = env.jig.memory.list(limit=400)
                        listing = "\n".join(f"#{m['id']}: {m['content'][:300]}" for m in reversed(mems))
                        if strat == "hybrid_forget":
                            how = ("For every fact that a NEWER fact supersedes or contradicts, delete the outdated "
                                   "one with memory_forget(memory_id). Also delete exact duplicates.")
                        else:
                            how = ("Do not delete anything. For every fact that a NEWER fact supersedes or contradicts, "
                                   "add one memory with memory_add starting 'UPDATE [date]:' that says which fact is "
                                   "now current and which is outdated.")
                        c = await env.run_task(
                            f"Memory consolidation after session {i}",
                            f"Consolidate the user's long-term memory. Current memories (oldest first):\n{listing}\n\n"
                            f"{how} Then reply 'done'.", Mode.ACTION, timeout_s=timeout)
                        ingest_results.append(c)
            a = await env.run_task(
                "Answer a question about past conversations",
                f"The current date is {q['question_date']}. Use memory_search (try several queries) to recall what "
                f"the user told you in past conversations, then answer concisely: {q['question']}",
                Mode.RESEARCH, timeout_s=timeout)
            response = a.get("result") or f"[task {a['status']}: {a.get('error')}]"
            answer_tokens = {k: v for k, v in model_usage(env.audit(task_id=a["id"])).items()
                             if k in ("prompt_tokens", "completion_tokens")}
        ingest_usage = model_usage(env.audit())
        approvals = len(env.responder.requests)
        memories = len(env.jig.memory.list(limit=10_000))
        forgets = sum(1 for r in env.audit(kind="tool.result") if r["data"].get("tool") == "memory_forget")
    correct, judge_raw = await judge(ctx, q, response)
    return {
        **trial,
        "question": q["question"],
        "answer": q["answer"] if isinstance(q["answer"], str) else json.dumps(q["answer"]),
        "response": response[:2000],
        "correct": correct,
        "judge_raw": judge_raw[:50],
        "sessions": len(sessions),
        "answer_prompt_tokens": answer_tokens.get("prompt_tokens", 0),
        "answer_completion_tokens": answer_tokens.get("completion_tokens", 0),
        "total_prompt_tokens": ingest_usage["prompt_tokens"] + _usage_sum(
            [r for r in ingest_results if hasattr(r, "usage")])["prompt_tokens"] + (
            answer_tokens.get("prompt_tokens", 0) if strat in ("full_context", "rolling_summary") else 0),
        "total_model_calls": ingest_usage["model_calls"] + sum(1 for r in ingest_results if hasattr(r, "usage")) + (
            1 if strat in ("full_context", "rolling_summary") else 0),
        "memories_final": memories,
        "forgets": forgets,
        "approvals": approvals,
        "wall_s": round(time.monotonic() - started, 2),
    }


async def judge(ctx: Any, q: dict[str, Any], response: str) -> tuple[bool, str]:
    """Grade with LongMemEval's prompt on the configured judge endpoint (temperature 0). Fails loudly."""
    from jig.config import EndpointConfig

    jcfg = ctx.cfg["judge"]
    client = ModelClient(EndpointConfig(base_url=ctx.endpoints[jcfg["endpoint"]], name=jcfg.get("name", ""),
                                        max_tokens=jcfg.get("max_tokens", 2048), read_timeout_s=600,
                                        sampling={"temperature": 0.0, "seed": 0}), label="judge model")
    try:
        await client.health()
        prompt = judge_prompt(q["question_type"], q["question"], json.dumps(q["answer"]) if not isinstance(
            q["answer"], str) else q["answer"], response, q["question_id"].endswith("_abs"))
        r = await client.chat([{"role": "user", "content": prompt}])
    finally:
        await client.aclose()
    text = r.content.strip().lower()
    tail = text.split("</think>")[-1].strip()
    if tail.startswith("yes") or tail.rstrip(".") == "yes":
        return True, tail
    if tail.startswith("no") or tail.rstrip(".") == "no":
        return False, tail
    raise RuntimeError(f"judge reply is neither yes nor no: {r.content[:200]!r}")