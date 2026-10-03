"""Measure Jig's progress check (jig.progress) on real text, replayed as a stream.

- Captured runs: every model output recorded by the structured-output debug harness (``--runs``, the folder of
  run folders each holding all.jsonl and fail-*.json), streamed in pieces the size of the model's own tokens
  (characters per token from the usage it reported). The loops must be caught; nothing else may be.
- Legitimately repetitive text, from real documents and real model outputs: code (Jig's own and Python's standard
  library, in a fence as a model writes it), Markdown tables, CSV files, JSON lists, LongMemEval's real assistant
  replies (``--longmemeval``), and a real sentence written N times when the user asked for it N times.

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
                text = http.get("response_text") or ""
                if text in seen or http.get("status") != 200:
                    continue
                seen.add(text)
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    continue
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
                yield {"run": Path(name).parent.name, "file": Path(name).name, "parts": parts,
                       "request": http.get("request") or {}, "usage": data.get("usage") or {},
                       "finish_reason": choice.get("finish_reason")}


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
                     "repetition_only": bare.record() if bare else None})
    loops = [r for r in rows if r["looped"]]
    normal = [r for r in rows if not r["looped"]]
    long_normal = [r for r in normal if r["longest_part"] >= progress.WINDOW]
    seen = Seen()
    for r in normal:
        seen.add(r["seen"])
    worst = round(seen.lowest, 4) if seen.lowest < 1 else None
    return {
        "outputs": len(rows),
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=Path, required=True)
    ap.add_argument("--longmemeval", type=Path, default=REPO / "research" / "data" / "longmemeval" /
                    "longmemeval_s_cleaned.json")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    report: dict[str, Any] = {
        "thresholds": {"window": progress.WINDOW, "check_every": progress.CHECK_EVERY,
                       "min_ratio": progress.MIN_RATIO, "block_chars": progress.BLOCK_CHARS,
                       "guarded_block_chars": progress.GUARDED_BLOCK_CHARS,
                       "relaxed_block_chars": progress.RELAXED_BLOCK_CHARS},
        "captured": measure_captured(a.runs),
        "legitimately_repetitive": measure_legit(a.longmemeval if a.longmemeval.is_file() else None)}
    report["cost"] = {"checks": TIMING["checks"],
                      "microseconds_per_check": round(1e6 * TIMING["seconds"] / max(1, TIMING["checks"]), 1)}
    text = json.dumps(report, indent=1)
    if a.out:
        a.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
