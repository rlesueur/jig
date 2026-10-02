"""Measure how much of each prompt the model server reuses from its cache, over a long chat with real tools.

Runs a scripted conversation through a real Jig runtime, importing Jig from ``--src`` (so two versions can be
compared), against a real llama.cpp server, and records for every model call the server's own timings
(``cache_n``: prompt tokens reused from its cache; ``prompt_n``: prompt tokens it had to evaluate) and the time
to the first streamed token. The chat starts from a long saved conversation, so a prompt that changes near
its start costs a lot.

A server shared with other clients may serve a request from a slot that holds someone else's prompt, so
``cache_n`` is noisy there. Each request is therefore also rendered with the server's own chat template
(``/apply-template``, ``/tokenize``) and compared with the previous request: ``new_since_previous_call`` is
what a slot kept for this chat would have to evaluate, whatever else is using the server.

    python scripts/measure_prompt_cache.py --src <jig source tree> --base-url http://127.0.0.1:8080/v1 \
        --label after --out results.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

import httpx

EARLIER_TURNS = 24
TURNS = [
    "What files are in my workspace? Just list their names.",
    "Read plan.txt and summarise it in two sentences.",
    "Remember that my train to Edinburgh leaves at 07:42 from platform 3.",
    "What time is it in Tokyo right now?",
    "From everything we've discussed, what is the first thing I should pack? One sentence.",
    "Remember that I'm allergic to penicillin.",
    "Search your memory for anything about trains and tell me what you find.",
]
MEMORIES = [
    "Robyn lives in Bristol", "Robyn prefers tea to coffee", "Robyn's sister is called Ffion",
    "Robyn cycles to work most days", "Robyn is learning Welsh", "Robyn's dentist is Dr Patel",
    "Robyn likes walking holidays in Scotland", "Robyn's cat is called Biscuit",
    "Robyn's car is a 2019 Honda Jazz", "Robyn works on open-source software",
    "Robyn's favourite author is Ursula Le Guin", "Robyn doesn't eat mushrooms",
]


def earlier_conversation() -> list[dict]:
    """A long, realistic earlier conversation about planning a trip, saved before the measured turns."""
    topics = ["the route north", "where to stay in Pitlochry", "walking boots", "the weather in the Cairngorms",
              "train tickets", "a packing list", "midges", "a day in Edinburgh", "maps and phone signal",
              "what to eat on the trail", "budget for the week", "booking the sleeper train"]
    out = []
    for i in range(EARLIER_TURNS):
        topic = topics[i % len(topics)]
        out.append({"role": "user", "content": f"Turn {i + 1}: I'm still planning my walking week in Scotland. "
                                               f"What should I think about regarding {topic}?"})
        out.append({"role": "assistant", "content": " ".join(
            f"On {topic}, point {k + 1}: consider the timing, the cost and the distance, check what the locals "
            f"recommend, keep a spare option, and write it down in your plan so nothing is forgotten on day "
            f"{(i + k) % 7 + 1}." for k in range(6))})
    return out


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--src", required=True, help="a Jig source tree (the folder that holds jig/)")
    p.add_argument("--base-url", required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    sys.path.insert(0, str(Path(a.src).resolve()))
    os.environ["JIG_MODEL_BASE_URL"] = a.base_url
    from jig.config import load_config
    from jig.runtime import Jig
    import jig as jig_package

    assert Path(jig_package.__file__).resolve().is_relative_to(Path(a.src).resolve()), jig_package.__file__
    base = Path(tempfile.mkdtemp(prefix=f"jig-cache-{a.label}-"))
    config = load_config(Path(a.src) / "jig.toml", data_dir=base / "data", sandbox_dir=base / "sandbox")
    runtime = Jig(config)
    await runtime.start(run_scheduler=False, check_capabilities=False)
    calls: list[dict] = []
    previous: list[int] = []
    original = runtime.model.chat
    server = a.base_url.rstrip("/").removesuffix("/v1")
    client = httpx.AsyncClient(timeout=60)

    async def rendered_tokens(messages, tools) -> list[int]:
        """The prompt exactly as the server's chat template renders it, as tokens."""
        full = runtime.model._body(messages, tools=tools, stream=False, model=None, max_tokens=None,
                                   response_schema=None)
        body = {k: full[k] for k in ("messages", "tools") if k in full}
        prompt = (await client.post(f"{server}/apply-template", json=body)).raise_for_status().json()["prompt"]
        reply = await client.post(f"{server}/tokenize", json={"content": prompt})
        return reply.raise_for_status().json()["tokens"]

    async def chat(messages, **kw):
        started, first = time.perf_counter(), None
        sink = kw.get("on_delta")

        async def on_delta(kind: str, text: str) -> None:
            nonlocal first
            if first is None:
                first = time.perf_counter() - started
            if sink is not None:
                await sink(kind, text)

        kw["on_delta"] = on_delta
        tokens = await rendered_tokens(messages, kw.get("tools"))
        shared = next((i for i, (x, y) in enumerate(zip(tokens, previous)) if x != y), min(len(tokens), len(previous)))
        previous[:] = tokens
        result = await original(messages, **kw)
        calls.append({"turn": current_turn, "ttft_s": first, "elapsed_s": time.perf_counter() - started,
                      "prompt_tokens": len(tokens), "new_since_previous_call": len(tokens) - shared,
                      "cache_n": result.timings.get("cache_n"), "prompt_n": result.timings.get("prompt_n"),
                      "prompt_ms": result.timings.get("prompt_ms"), "predicted_n": result.timings.get("predicted_n"),
                      "tool_calls": [c.name for c in result.tool_calls]})
        return result

    runtime.model.chat = chat
    turns = []
    try:
        (runtime.sandbox.root / "plan.txt").write_text(
            "Week in Scotland: Monday sleeper to Edinburgh; Tuesday train to Pitlochry; Wednesday to Friday "
            "walking in the Cairngorms; Saturday back to Edinburgh; Sunday home.", encoding="utf-8")
        for m in MEMORIES:
            runtime.memory.add(m)
        session = "sess_cachemeasure"
        runtime.store.save_session(session, earlier_conversation())
        for current_turn, message in enumerate(TURNS, 1):
            started = time.perf_counter()
            items = [item async for item in runtime.chat(message, session_id=session)]
            end = items[-1]
            turns.append({"turn": current_turn, "message": message, "type": end["type"],
                          "final": end.get("final") or end.get("error"), "wall_s": time.perf_counter() - started,
                          "tools": [c for call in calls if call["turn"] == current_turn for c in call["tool_calls"]]})
    finally:
        await runtime.stop()
        await client.aclose()
        shutil.rmtree(base, ignore_errors=True)
    evaluated = sum(c["prompt_n"] or 0 for c in calls)
    reused = sum(c["cache_n"] or 0 for c in calls)
    new = sum(c["new_since_previous_call"] for c in calls)
    sent = sum(c["prompt_tokens"] for c in calls)
    firsts = [next(c for c in calls if c["turn"] == t["turn"]) for t in turns]
    summary = {"label": a.label, "model_calls": len(calls), "prompt_tokens_evaluated": evaluated,
               "prompt_tokens_reused": reused, "reused_share": round(reused / max(1, reused + evaluated), 3),
               "prompt_tokens_sent": sent, "new_since_previous_call": new,
               "reusable_share": round(1 - new / max(1, sent), 3),
               "median_first_call_ttft_s": round(statistics.median(c["ttft_s"] for c in firsts), 2),
               "median_turn_wall_s": round(statistics.median(t["wall_s"] for t in turns), 2),
               "total_wall_s": round(sum(t["wall_s"] for t in turns), 1)}
    Path(a.out).write_text(json.dumps({"summary": summary, "turns": turns, "calls": calls}, indent=1),
                           encoding="utf-8")
    print(json.dumps(summary))


if __name__ == "__main__":
    asyncio.run(main())
