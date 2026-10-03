"""Measure Jig's progress check (jig.progress) on real text, replayed as a stream.

- Captured runs: every model output recorded by the structured-output debug harness (``--runs``, the folder of
  run folders each holding all.jsonl and fail-*.json), streamed in pieces the size of the model's own tokens
  (characters per token from the usage it reported). The loops must be caught; nothing else may be.
- Legitimately repetitive text, from real documents and real model outputs: code (Jig's own and Python's standard
  library, in a fence as a model writes it), Markdown tables, CSV files, JSON lists, LongMemEval's real assistant
  replies (``--longmemeval``), and a real sentence written N times when the user asked for it N times.
- Going round in circles: the captured circling runs (tests/captured/circling-*.json) against genuine text fed as
  reasoning: real reasoning (the harness's, and Jig's own with ``--reasoning``), LongMemEval's replies, Jig's
  documents, and real code and plans redrafted. Reports the highest share each set holds against the threshold.

Reports, for each set, the false alarms with Jig's guards and with the bare ratio rule, the lowest ratio seen and
the margin to the threshold. Text is never printed: only counts, ratios and positions.

    python scripts/measure_progress_check.py --runs C:/Users/you/jig-so-debug/runs --out progress-check.json
"""

from __future__ import annotations

import argparse
import glob
import json
import random
import sys
import time
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jig import progress  # noqa: E402
from jig.progress import ProgressCheck, Stop, requested_repeats  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


TIMING = {"checks": 0, "seconds": 0.0}


@dataclass
class Seen:
    """What the check saw at the points it looked: the lowest ratio and the longest exact repeat."""

    lowest: float = 1.0
    longest_repeat: int = 0

    def add(self, other: Seen) -> None:
        self.lowest = min(self.lowest, other.lowest)
        self.longest_repeat = max(self.longest_repeat, other.longest_repeat)


def stream(check: ProgressCheck, parts: list[tuple[str, str]], chars_per_token: float) -> tuple[Stop | None, Seen]:
    """Feed ``parts`` ((part, text), in the order a server streams them) in token-sized pieces. Returns the stop,
    if any, and what the check saw where it looked."""
    size = max(1, round(chars_per_token))
    seen = Seen()
    for part, text in parts:
        if part.startswith("tool_arguments:") and (stop := check.tool_call(int(part.split(":")[1]))):
            return stop, seen
        for i in range(0, len(text), size):
            check.chunk()
            piece = text[i:i + size]
            before = check._parts.get(part)
            unchecked = (before.unchecked if before else 0) + len(piece)
            started = time.perf_counter()
            stop = check.feed(part, piece)
            state = check._parts[part]
            if unchecked >= progress.CHECK_EVERY and len(state.buf) >= progress.WINDOW:
                TIMING["checks"] += 1
                TIMING["seconds"] += time.perf_counter() - started
                if (ratio := progress.window_ratio(state.buf)) is not None:
                    seen.lowest = min(seen.lowest, ratio)
                seen.longest_repeat = max(seen.longest_repeat, progress.repeated_block(state.buf)[1])
            if stop:
                return stop, seen
    return None, seen


def last_window_ratio(text: str) -> float:
    tail = text[-progress.WINDOW:].encode("utf-8")
    return len(zlib.compress(tail, 6)) / len(tail)


# Captured runs -------------------------------------------------------------------------------------------------

def captured(runs: Path) -> Iterator[dict[str, Any]]:
    """Every distinct model output in the harness's records: its parts, request and usage."""
    seen: set[str] = set()
    files = sorted(glob.glob(str(runs / "*" / "all.jsonl"))) + sorted(glob.glob(str(runs / "*" / "fail-*.json")))
    for name in files:
        lines = Path(name).read_text(encoding="utf-8").splitlines() if name.endswith(".jsonl") else \
            [Path(name).read_text(encoding="utf-8")]
        for line in lines:
            if not line.strip():
                continue
            rec = json.loads(line)
            for http in rec.get("http") or []:
                if http.get("status") != 200:
                    continue
                if "sse" in http:
                    text = "\n".join(raw for _, raw in http["sse"])
                    if text in seen:
                        continue
                    seen.add(text)
                    out = from_stream(http["sse"])
                else:
                    text = http.get("response_text") or ""
                    if text in seen:
                        continue
                    seen.add(text)
                    try:
                        out = from_message(json.loads(text))
                    except json.JSONDecodeError:
                        continue
                yield {"run": Path(name).parent.name, "file": Path(name).name,
                       "request": http.get("request") or {}, **out}


def from_message(data: dict[str, Any]) -> dict[str, Any]:
    """A whole (non-streamed) reply, as the harness recorded them before every request streamed."""
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    parts = []
    if reasoning := msg.get("reasoning_content") or msg.get("reasoning"):
        parts.append(("reasoning", reasoning))
    if msg.get("content"):
        parts.append(("content", msg["content"]))
    for i, call in enumerate(msg.get("tool_calls") or []):
        args = (call.get("function") or {}).get("arguments") or ""
        parts.append((f"tool_arguments:{i}", args if isinstance(args, str) else json.dumps(args)))
    return {"parts": parts, "usage": data.get("usage") or {}, "finish_reason": choice.get("finish_reason")}


def from_stream(sse: list[list[Any]]) -> dict[str, Any]:
    """A streamed reply from its raw SSE lines ([seconds, line] as the harness recorded them). One Jig stopped
    has no finish_reason and no error from the server (``stopped_by_jig``); its token count is then the number
    of chunks."""
    reasoning: list[str] = []
    content: list[str] = []
    calls: dict[int, list[str]] = {}
    usage: dict[str, Any] = {}
    finish = None
    chunks = 0
    errored = False
    for _, raw in sse:
        if not raw.startswith("data:") or raw[5:].strip() == "[DONE]":
            continue
        try:
            chunk = json.loads(raw[5:])
        except json.JSONDecodeError:
            continue
        errored = errored or "error" in chunk
        usage = chunk.get("usage") or usage
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            chunks += bool(delta)
            if text := delta.get("reasoning_content") or delta.get("reasoning"):
                reasoning.append(text)
            if delta.get("content"):
                content.append(delta["content"])
            for pos, tc in enumerate(delta.get("tool_calls") or []):
                args = (tc.get("function") or {}).get("arguments")
                calls.setdefault(tc.get("index", pos), []).append(args if isinstance(args, str) else
                                                                  json.dumps(args) if args else "")
            finish = choice.get("finish_reason") or finish
    parts = [("reasoning", "".join(reasoning))] if reasoning else []
    if content:
        parts.append(("content", "".join(content)))
    parts += [(f"tool_arguments:{i}", "".join(a)) for i, a in sorted(calls.items())]
    return {"parts": parts, "usage": usage or ({"completion_tokens": chunks} if finish is None else {}),
            "finish_reason": finish, "stopped_by_jig": finish is None and not errored}


def answer_part(request: dict[str, Any]) -> str | None:
    if request.get("response_format"):
        return "content"
    if any((t.get("function") or {}).get("name") == "respond" for t in request.get("tools") or []):
        return "tool_arguments"
    return None


def measure_captured(runs: Path) -> dict[str, Any]:
    rows = []
    for out in captured(runs):
        total = sum(len(t) for _, t in out["parts"])
        tokens = out["usage"].get("completion_tokens") or 0
        cpt = total / tokens if tokens else 3.5
        request = out["request"]
        req = requested_repeats(request.get("messages") or [])
        stop, seen = stream(ProgressCheck(answer=answer_part(request), requested=req), out["parts"], cpt)
        bare, _ = stream(ProgressCheck(requested=req), out["parts"], cpt)
        # A loop, judged on the whole recorded output rather than by the check: cut off at the limit, with its
        # longest part ending in text that compresses below 0.05.
        longest = max(out["parts"], key=lambda p: len(p[1]), default=("", ""))[1]
        looped = (out["finish_reason"] == "length" and len(longest) >= progress.WINDOW
                  and last_window_ratio(longest) < 0.05)
        rows.append({"run": out["run"], "file": out["file"], "chars": total, "tokens": tokens,
                     "chars_per_token": round(cpt, 2), "looped": looped, "mode": answer_part(request),
                     "longest_part": len(longest), "seen": seen,
                     "end_ratio": round(last_window_ratio(longest), 4) if len(longest) >= progress.WINDOW else None,
                     "stop": stop.record() if stop else None,
                     "repetition_only": bare.record() if bare else None,
                     "stopped_live": out.get("stopped_by_jig", False)})
    # A reply Jig stopped as it streamed ended where the check fired, so it cannot be judged a loop by how it
    # ended: these are listed on their own, to be judged by reading them.
    live = [r for r in rows if r["stopped_live"]]
    rows = [r for r in rows if not r["stopped_live"]]
    loops = [r for r in rows if r["looped"]]
    normal = [r for r in rows if not r["looped"]]
    long_normal = [r for r in normal if r["longest_part"] >= progress.WINDOW]
    seen = Seen()
    for r in normal:
        seen.add(r["seen"])
    worst = round(seen.lowest, 4) if seen.lowest < 1 else None
    return {
        "outputs": len(rows) + len(live),
        "stopped_live": [{k: r[k] for k in ("run", "file", "mode", "chars", "tokens", "stop")} for r in live],
        "loops": [{k: r[k] for k in ("run", "file", "mode", "chars", "tokens", "chars_per_token", "end_ratio", "stop",
                                     "repetition_only")} for r in loops],
        "normal_outputs": len(normal),
        "normal_outputs_with_a_part_of_1000_chars_or_more": len(long_normal),
        "false_alarms": [{k: r[k] for k in ("run", "file", "mode", "chars", "stop")} for r in normal if r["stop"]],
        "false_alarms_repetition_only": sum(1 for r in normal if r["repetition_only"]),
        "lowest_ratio_of_a_normal_output": worst,
        "margin_to_min_ratio": round(worst / progress.MIN_RATIO, 2) if worst else None,
        "longest_exact_repeat_in_a_normal_output": seen.longest_repeat,
    }


# Legitimately repetitive text ---------------------------------------------------------------------------------

def files(patterns: list[str], limit: int, seed: int = 7) -> list[Path]:
    found = sorted({Path(p) for pattern in patterns for p in glob.glob(pattern, recursive=True)})
    found = [p for p in found if p.is_file() and p.stat().st_size >= progress.WINDOW]
    random.Random(seed).shuffle(found)
    return found[:limit]


def read(path: Path, limit: int = 60_000) -> str:
    return path.read_text(encoding="utf-8", errors="replace")[:limit]


def markdown_tables(text: str) -> list[str]:
    tables, block = [], []
    for line in text.splitlines() + [""]:
        if line.strip().startswith("|"):
            block.append(line)
            continue
        if len(block) >= 3 and sum(len(b) + 1 for b in block) >= progress.WINDOW:
            tables.append("\n".join(block))
        block = []
    return tables


def longmemeval_replies(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    seen: set[str] = set()
    for entry in data:
        for session in entry["haystack_sessions"]:
            for msg in session:
                if msg["role"] == "assistant" and len(msg["content"]) >= progress.WINDOW:
                    seen.add(msg["content"])
    return sorted(seen)


def as_tool_arguments(text: str, key: str = "content") -> str:
    return json.dumps({"path": "out.txt", key: text})


def legit_sets(longmemeval: Path | None) -> dict[str, list[tuple[list[dict[str, Any]], list[tuple[str, str]]]]]:
    """Each set: (request messages, parts) cases, all built from real text."""
    ask = [{"role": "user", "content": "Please help with this."}]
    sets: dict[str, list] = {}
    py = files([str(REPO / "jig" / "**" / "*.py"), str(Path(sys.base_prefix) / "Lib" / "*.py"),
                str(Path(sys.base_prefix) / "Lib" / "json" / "*.py")], 250)
    js = files([str(REPO / "jig" / "web" / "*.js"), str(REPO / "avatar" / "*.js")], 20)
    sets["code in a fence (Python, JavaScript)"] = [
        (ask, [("content", f"Here it is:\n\n```{'python' if p.suffix == '.py' else 'javascript'}\n{read(p)}\n```\n")])
        for p in py + js]
    sets["code written to a file (tool-call arguments)"] = [
        (ask, [("tool_arguments:0", as_tool_arguments(read(p)))]) for p in py[:120]]
    csvs = files([str(Path(sys.prefix) / "Lib" / "site-packages" / "**" / "*.csv"),
                  str(REPO / "research" / ".venv" / "Lib" / "site-packages" / "**" / "*.csv")], 60)
    sets["CSV in a fence"] = [(ask, [("content", f"```csv\n{read(p, 30_000)}\n```")]) for p in csvs]
    sets["CSV as plain text"] = [(ask, [("content", read(p, 30_000))]) for p in csvs]
    md = files([str(REPO / "**" / "*.md"), str(Path(sys.prefix) / "Lib" / "site-packages" / "**" / "*.md")], 4000)
    tables = [t for p in md for t in markdown_tables(read(p, 400_000))]
    sets["Markdown tables"] = [(ask, [("content", f"Here is the table:\n\n{t}\n")]) for t in tables]
    js_files = files([str(REPO / "research" / ".venv" / "Lib" / "site-packages" / "**" / "*.json"),
                      str(REPO / "research" / "results" / "**" / "*.json"),
                      str(Path(sys.prefix) / "Lib" / "site-packages" / "**" / "*.json")], 200)
    lists = []
    for p in js_files:
        try:
            value = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        found = _json_lists(value)
        if found:
            lists.append(json.dumps(found, indent=2, ensure_ascii=False)[:30_000])
    sets["JSON lists of similar items (in a fence)"] = [(ask, [("content", f"```json\n{t}\n```")]) for t in lists]
    sets["JSON lists of similar items (tool-call arguments)"] = [
        (ask, [("tool_arguments:0", json.dumps({"items": json.loads(t) if _whole(t) else t}))]) for t in lists]
    sentences = _sentences(read(REPO / "README.md", 200_000))
    repeats = []
    for n in (10, 20, 50, 100):
        for s in sentences[:10]:
            request = [{"role": "user", "content": f"Write this sentence {n} times, one per line: {s}"}]
            repeats.append((request, [("content", "\n".join([s] * n) + "\n")]))
            repeats.append((request, [("content", "\n".join(f"{i}. {s}" for i in range(1, n + 1)) + "\n")]))
    sets["a sentence written N times, as asked"] = repeats
    if longmemeval:
        replies = longmemeval_replies(longmemeval)
        sets["LongMemEval assistant replies (real model output)"] = [(ask, [("content", r)]) for r in replies]
        sets["LongMemEval replies with a Markdown table"] = [(ask, [("content", r)]) for r in replies if "\n|" in r]
        sets["LongMemEval replies with code"] = [(ask, [("content", r)]) for r in replies if "```" in r]
    return sets


def _json_lists(value: Any) -> list[Any] | None:
    """The longest list of two or more objects in ``value``."""
    best: list[Any] | None = None
    stack = [value]
    while stack:
        v = stack.pop()
        if isinstance(v, list):
            if len(v) >= 2 and all(isinstance(x, dict) for x in v) and (best is None or len(v) > len(best)):
                best = v
            stack.extend(v)
        elif isinstance(v, dict):
            stack.extend(v.values())
    return best


def _whole(text: str) -> bool:
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


def _sentences(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if 40 <= len(line) <= 160 and line[0].isupper() and line.endswith(".") and "|" not in line:
            out.append(line)
    return out


RATIO_BUCKETS = (0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.12, 0.15, 0.20)


def measure_legit(longmemeval: Path | None) -> dict[str, Any]:
    """Each set with Jig's guards, then with every guard off (the bare ratio and repeat rules, and no allowance
    for a repeat the user asked for)."""
    results = {}
    unguarded = progress.guarded
    for name, cases in legit_sets(longmemeval).items():
        row: dict[str, Any] = {"cases": len(cases), "chars": sum(len(t) for _, parts in cases for _, t in parts)}
        for label, guard in (("false_alarms", unguarded), ("false_alarms_without_guards", lambda state: False)):
            progress.guarded = guard
            alarms, reasons, seen, lows = 0, {}, Seen(), []
            for request, parts in cases:
                req = requested_repeats(request) if label == "false_alarms" else None
                stop, case_seen = stream(ProgressCheck(requested=req), parts, 3.5)
                seen.add(case_seen)
                lows.append(case_seen.lowest)
                if stop:
                    alarms += 1
                    reasons[stop.reason] = reasons.get(stop.reason, 0) + 1
            row[label] = alarms
            row[f"{label}_by_reason"] = reasons
            if label == "false_alarms":
                row["lowest_ratio"] = round(seen.lowest, 4) if seen.lowest < 1 else None
                row["longest_exact_repeat"] = seen.longest_repeat
                row["cases_whose_lowest_ratio_is_below"] = {str(b): sum(1 for x in lows if x < b) for b in RATIO_BUCKETS}
        progress.guarded = unguarded
        results[name] = row
    return results


# Going round in circles ----------------------------------------------------------------------------------------

def circling_trace(text: str, piece: int = 16) -> list[tuple[int, float, bool]]:
    """At each look of the circling check over ``text`` as reasoning: the position, the share of recent phrases
    written CIRCLE_TIMES times or more, and whether the text there is guarded. Watches jig.progress's own state."""
    check = ProgressCheck()
    check._repetition = lambda state: None  # type: ignore[method-assign]
    out: list[tuple[int, float, bool]] = []
    saved = progress.CIRCLE_REUSED, progress.CIRCLE_LOOKS, progress.guarded
    unguarded = progress.guarded

    def watch(state: Any) -> bool:
        g = unguarded(state)
        if state.unlooked == 0:
            out.append((state.chars, sum(state.recent_again) / progress.CIRCLE_WINDOW, g))
        return g

    progress.CIRCLE_REUSED, progress.CIRCLE_LOOKS, progress.guarded = -1.0, 10**9, watch
    try:
        for i in range(0, len(text), piece):
            check.chunk()
            check.feed("reasoning", text[i:i + piece])
    finally:
        progress.CIRCLE_REUSED, progress.CIRCLE_LOOKS, progress.guarded = saved
    return out


def held(trace: list[tuple[int, float, bool]], looks: int) -> float:
    """The highest share held, unguarded, for ``looks`` looks in a row: what the check compares with
    CIRCLE_REUSED."""
    best, run = 0.0, []
    for _, reused, guarded in trace:
        run = [] if guarded else (run + [reused])[-looks:]
        if len(run) == looks:
            best = max(best, min(run))
    return best


def circling_sets(runs: Path, longmemeval: Path | None, reasoning: Path | None) -> dict[str, list[str]]:
    """Genuine text, fed as reasoning: real reasoning, real replies and documents, and real code and plans
    redrafted the way reasoning redrafts them."""
    sets: dict[str, list[str]] = {}
    if reasoning:
        for line in reasoning.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            sets.setdefault(f"Jig's stored reasoning ({row.get('kind')})", []).append(row["text"])
    harness, plans = [], []
    for out in captured(runs):
        for part, text in out["parts"]:
            if part == "reasoning" and len(text) >= 2000 and not out.get("stopped_by_jig") and not (
                    out["finish_reason"] == "length" and last_window_ratio(text) < 0.05):
                harness.append(text)
            if part.startswith("tool_arguments") and len(text) >= 800 and '"tasks"' in text:
                plans.append(f"Plan:\n{text}\n\nLet me check each task. Final:\n{text.replace('research', 'action', 1)}\n")
    sets["the harness's reasoning (planner, Sentinel, probe)"] = harness
    sets["a real plan drafted, then redrafted"] = plans
    if longmemeval:
        sets["LongMemEval assistant replies, as reasoning"] = longmemeval_replies(longmemeval)
    sets["Jig's Markdown documents, as reasoning"] = [t for p in files([str(REPO / "**" / "*.md")], 400)
                                                      if len(t := read(p, 120_000)) >= 4000]
    rng = random.Random(7)
    twice, thrice = [], []
    for p in files([str(REPO / "jig" / "**" / "*.py")], 120):
        lines = (t := read(p, 12_000)).splitlines()
        if len(lines) < 40:
            continue
        i = rng.randrange(len(lines))
        changed = "\n".join(lines[:i] + ["    # fixed: handle the empty case"] + lines[i:])
        twice.append(f"Draft:\n{t}\n\nThat misses the empty case. Again:\n{changed}\n\nGood.")
        thrice.append(f"{t}\n\nAgain:\n{changed}\n\nOnce more, cleaner:\n{t}\n")
    sets["a real file drafted, then redrafted"] = twice
    sets["a real file drafted three times"] = thrice
    return sets


def measure_circling(runs: Path, longmemeval: Path | None, reasoning: Path | None) -> dict[str, Any]:
    """The captured circling runs (tests/captured/circling-*.json) must stop; genuine text as reasoning must not.
    For each set: the false alarms, and the highest share held over CIRCLE_LOOKS looks, against CIRCLE_REUSED."""
    looks = progress.CIRCLE_LOOKS
    positives = {}
    for path in sorted((REPO / "tests" / "captured").glob("circling-*.json")):
        rec = json.loads(path.read_text(encoding="utf-8"))
        text = dict(rec["parts"])["reasoning"]
        stop, _ = stream(ProgressCheck(answer=rec["answer"]), rec["parts"], rec["chars_per_token"])
        positives[path.name] = {"source": rec.get("source"), "chars": len(text),
                                "held": round(held(circling_trace(text), looks), 3),
                                "stop": stop.record() if stop else None}
    negatives = {}
    for name, texts in circling_sets(runs, longmemeval, reasoning).items():
        heights = [held(circling_trace(t), looks) for t in texts]
        negatives[name] = {"cases": len(texts), "chars": sum(len(t) for t in texts),
                           "false_alarms": sum(1 for h in heights if h >= progress.CIRCLE_REUSED),
                           "highest_held": round(max(heights, default=0.0), 3)}
    top = max((n["highest_held"] for n in negatives.values()), default=0.0)
    low = min((p["held"] for p in positives.values()), default=None)
    return {"positives": positives, "negatives": negatives, "highest_held_by_genuine_text": top,
            "lowest_held_by_a_circling_run": low,
            "gap": round(low - top, 3) if low is not None else None}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=Path, required=True)
    ap.add_argument("--longmemeval", type=Path, default=REPO / "research" / "data" / "longmemeval" /
                    "longmemeval_s_cleaned.json")
    ap.add_argument("--reasoning", type=Path, help="a private JSONL of real reasoning ({kind, text} per line)")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    lme = a.longmemeval if a.longmemeval.is_file() else None
    report: dict[str, Any] = {
        "thresholds": {"window": progress.WINDOW, "check_every": progress.CHECK_EVERY,
                       "min_ratio": progress.MIN_RATIO, "block_chars": progress.BLOCK_CHARS,
                       "guarded_block_chars": progress.GUARDED_BLOCK_CHARS,
                       "relaxed_block_chars": progress.RELAXED_BLOCK_CHARS,
                       "circle_phrase": progress.CIRCLE_PHRASE, "circle_times": progress.CIRCLE_TIMES,
                       "circle_window": progress.CIRCLE_WINDOW, "circle_every": progress.CIRCLE_EVERY,
                       "circle_reused": progress.CIRCLE_REUSED, "circle_looks": progress.CIRCLE_LOOKS},
        "captured": measure_captured(a.runs),
        "legitimately_repetitive": measure_legit(lme),
        "circling": measure_circling(a.runs, lme, a.reasoning)}
    report["cost"] = {"checks": TIMING["checks"],
                      "microseconds_per_check": round(1e6 * TIMING["seconds"] / max(1, TIMING["checks"]), 1)}
    text = json.dumps(report, indent=1)
    if a.out:
        a.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
