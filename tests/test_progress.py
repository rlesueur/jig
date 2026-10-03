"""Jig's progress check on a streamed reply (jig.progress), on real text only: the start of two loops recorded from
real model servers (tests/captured/loop-*.json), and this repository's own code, tables and files. With the captured
runs at hand (JIG_SO_DEBUG_RUNS), every recorded output is replayed too; scripts/measure_progress_check.py reports
the margins over the full set, including real CSV files and LongMemEval's assistant replies."""

from __future__ import annotations

import csv
import io
import json
import os
import re
from pathlib import Path

import pytest

from jig import progress
from jig.progress import ProgressCheck, Stop, requested_repeats

REPO = Path(__file__).resolve().parent.parent
DATA = Path(__file__).resolve().parent / "captured"
RUNS = Path(os.environ.get("JIG_SO_DEBUG_RUNS", Path.home() / "jig-so-debug" / "runs"))


def stream(check: ProgressCheck, parts: list[tuple[str, str]], chars_per_token: float = 3.5) -> Stop | None:
    """Feed the parts in token-sized pieces, as a server streams them."""
    size = max(1, round(chars_per_token))
    for part, text in parts:
        if part.startswith("tool_arguments:") and (stop := check.tool_call(int(part.split(":")[1]))):
            return stop
        for i in range(0, len(text), size):
            check.chunk()
            if stop := check.feed(part, text[i:i + size]):
                return stop
    return None


def loop(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def sources(limit: int = 40) -> list[str]:
    files = sorted((REPO / "jig").rglob("*.py")) + sorted((REPO / "jig" / "web").glob("*.js"))
    return [f.read_text(encoding="utf-8")[:20_000] for f in files if f.stat().st_size > 3000][:limit]


def readme_tables() -> list[str]:
    text = (REPO / "README.md").read_text(encoding="utf-8")
    return [m.group(0) for m in re.finditer(r"(?:^\|.*\n){3,}", text, re.MULTILINE)]


def repo_files() -> list[dict]:
    return [{"path": p.relative_to(REPO).as_posix(), "bytes": p.stat().st_size, "suffix": p.suffix}
            for p in sorted((REPO / "jig").rglob("*")) if p.is_file() and "__pycache__" not in p.parts]


# The loops ------------------------------------------------------------------------------------------------------

def test_the_recorded_tool_call_loop_is_stopped_early_without_its_text():
    rec = loop("loop-main.json")
    stop = stream(ProgressCheck(answer=rec["answer"]), rec["parts"], rec["chars_per_token"])
    assert stop is not None and stop.kind == "repetition"
    assert (stop.reason, stop.part, stop.period) == ("repeated_block", "content", 14)
    assert 1000 <= stop.repeat_chars < 1300 and stop.token < 700
    record = stop.record()
    assert set(record) == {"kind", "reason", "part", "token", "chars", "repeat_chars", "ratio", "period"}
    assert "answer" not in json.dumps(record) and "answer" not in stop.describe()
    assert stop.describe("the model").startswith("the model was repeating itself in its reply: a 14-character "
                                                 "block repeated over 1,")


def test_the_recorded_reasoning_collapse_is_stopped():
    rec = loop("loop-small.json")
    stop = stream(ProgressCheck(answer=rec["answer"]), rec["parts"], rec["chars_per_token"])
    assert stop is not None and (stop.reason, stop.part, stop.period) == ("repeated_block", "reasoning", 2)
    assert stop.repeat_chars >= progress.BLOCK_CHARS and stop.ratio < progress.MIN_RATIO
    # The model's sensible start is not what stopped it: it ran on for 2,000 characters before collapsing.
    assert stop.chars > 2000


def test_a_reply_the_user_chose_to_continue_stops_only_at_a_very_long_exact_repeat():
    rec = loop("loop-main.json")
    assert stream(ProgressCheck(relaxed=True), rec["parts"], rec["chars_per_token"]) is None
    text = dict(rec["parts"])["content"]
    long = [("content", text[:len(text) // 14 * 14] * 4)]  # its own 14-character block, kept going
    stop = stream(ProgressCheck(relaxed=True), long, rec["chars_per_token"])
    assert stop is not None and stop.repeat_chars >= progress.RELAXED_BLOCK_CHARS


@pytest.mark.skipif(not RUNS.is_dir(), reason=f"the captured runs are not at {RUNS} (set JIG_SO_DEBUG_RUNS)")
def test_every_captured_run_replayed_stops_only_the_loops():
    import sys
    sys.path.insert(0, str(REPO / "scripts"))
    from measure_progress_check import measure_captured

    report = measure_captured(RUNS)
    assert len(report["loops"]) == 2 and all(r["stop"] for r in report["loops"])
    assert report["false_alarms_repetition_only"] == 0
    # The one answer_complete stop is an answer of three respond calls, which Jig refuses anyway (wrong_calls).
    assert all(a["stop"]["reason"] == "answer_complete" for a in report["false_alarms"])
    assert report["lowest_ratio_of_a_normal_output"] > 2 * progress.MIN_RATIO


# Output that is repetitive by nature -------------------------------------------------------------------------------

def test_code_is_not_stopped_in_a_fence_or_as_tool_arguments():
    for code in sources():
        assert stream(ProgressCheck(), [("content", f"Here it is:\n\n```python\n{code}\n```\n")]) is None
        args = json.dumps({"path": "x.py", "content": code})
        assert stream(ProgressCheck(answer="tool_arguments"), [("tool_arguments:0", args)]) is None


def test_tables_csv_and_json_lists_are_not_stopped():
    tables = readme_tables()
    assert len(tables) >= 5
    for table in tables:
        assert stream(ProgressCheck(), [("content", f"These are the settings:\n\n{table}\n")]) is None
    rows = repo_files()
    out = io.StringIO()
    csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator="\n").writerows(rows)
    assert len(out.getvalue()) > 3000
    assert stream(ProgressCheck(), [("content", out.getvalue())]) is None
    listing = json.dumps(rows, indent=2)
    assert stream(ProgressCheck(), [("content", f"```json\n{listing}\n```")]) is None
    assert stream(ProgressCheck(), [("tool_arguments:0", json.dumps({"files": rows}))]) is None


def test_repetition_the_user_asked_for_is_not_stopped_until_it_runs_well_past_the_count():
    sentence = next(s for s in re.split(r"(?<=\.)\s+", (REPO / "README.md").read_text(encoding="utf-8"))
                    if 60 < len(s) < 160 and "\n" not in s)
    asked = [{"role": "user", "content": f"Write this sentence 50 times: {sentence}"}]
    assert requested_repeats(asked) == 50
    assert requested_repeats([{"role": "user", "content": "Please repeat that."}]) == 0
    assert requested_repeats([{"role": "user", "content": "What is York like?"}]) is None
    fifty = "\n".join([sentence] * 50)
    assert stream(ProgressCheck(requested=50), [("content", fifty)]) is None
    # Not asked for, the same text is a loop.
    assert stream(ProgressCheck(), [("content", fifty)]) is not None
    runaway = "\n".join([sentence] * 400)
    stop = stream(ProgressCheck(requested=50), [("content", runaway)])
    assert stop is not None and stop.repeat_chars // (len(sentence) + 1) > 50 * progress.REQUESTED_SLACK


def test_ordinary_prose_is_not_stopped():
    text = (REPO / "README.md").read_text(encoding="utf-8")
    assert stream(ProgressCheck(), [("reasoning", text[:30_000]), ("content", text[30_000:60_000])]) is None


# A structured answer that is already complete ------------------------------------------------------------------

def test_a_complete_json_answer_followed_by_more_is_stopped():
    verdict = json.dumps({"verdict": "allow", "risk": "low", "reason": "Reading a public page changes nothing."})
    assert stream(ProgressCheck(answer="content"), [("content", verdict + "\n\n")]) is None
    stop = stream(ProgressCheck(answer="content"), [("content", verdict + "\n" + verdict)])
    assert stop is not None and (stop.reason, stop.part) == ("answer_complete", "content")
    assert "kept writing after its answer was complete" in stop.describe()
    # Braces inside strings do not close the answer.
    tricky = json.dumps({"verdict": "allow", "risk": "low", "reason": "a } and a { in the reason"})
    assert stream(ProgressCheck(answer="content"), [("content", tricky)]) is None
    # Only the part holding the answer is checked: in tool-call mode, text before the call is left alone.
    assert stream(ProgressCheck(answer="tool_arguments"), [("content", verdict + " and more"),
                                                           ("tool_arguments:0", verdict)]) is None


def test_a_second_respond_call_is_stopped():
    verdict = json.dumps({"verdict": "allow", "risk": "low", "reason": "fine"})
    stop = stream(ProgressCheck(answer="tool_arguments"), [("tool_arguments:0", verdict),
                                                           ("tool_arguments:1", verdict)])
    assert stop is not None and (stop.reason, stop.part) == ("answer_complete", "tool_arguments")
    # An ordinary reply may make several tool calls.
    assert stream(ProgressCheck(), [("tool_arguments:0", verdict), ("tool_arguments:1", verdict)]) is None
