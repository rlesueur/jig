"""Short summaries of tool calls, for the live work view in Jig's own pages.

Each file or code tool call gets one ``tool.summary`` event when it finishes: which file was read or
listed, which file was written with a before/after diff, which command or code ran and how it ended,
and the test counts (and each test's result, when the runner lists them) if it ran tests.

The summaries carry what the user's work contains (file text, command output), so they live only on the
in-memory event bus: they go to the pages signed in to this Jig and to the chat stream, and are never
written to the log files or the audit history. ``EventBus.forget`` drops them with the conversation or
job they belong to, like the chat and tool events they sit beside.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from .errors import SandboxViolation
from .tools.registry import ToolContext

SUMMARISED = frozenset({"read_file", "list_files", "write_file", "run_command", "run_python"})

LINE_CHARS = 300  # longer lines are cut, with an ellipsis
DIFF_LINES = 400  # lines of a diff (context included) sent to the page
DIFF_SOURCE_BYTES = 400_000  # bigger files are reported by size only, without a diff
TAIL_LINES = 40  # the end of a command's output
CODE_LINES = 40  # the start of a piece of Python
COMMAND_CHARS = 2000
TEST_RESULTS = 300
ERROR_CHARS = 300
LISTED_NAMES = 12


def _line(text: str) -> str:
    return text if len(text) <= LINE_CHARS else f"{text[:LINE_CHARS - 1]}\u2026"


def before(name: str, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any] | None:
    """What a call is about to change, read before it runs: the current text of a file about to be written."""
    if name != "write_file" or not isinstance(args.get("path"), str):
        return None
    try:
        target = ctx.sandbox.resolve(args["path"])
    except SandboxViolation:
        return None  # outside the workspace: the call itself fails and says why
    if not target.is_file():
        return {"existed": False}
    size = target.stat().st_size
    if size > DIFF_SOURCE_BYTES:
        return {"existed": True, "bytes": size}
    return {"existed": True, "bytes": size, "text": target.read_text(encoding="utf-8", errors="replace")}


def summarise(name: str, args: dict[str, Any], result: Any, *, error: str | None,
              before: dict[str, Any] | None) -> dict[str, Any] | None:
    """The summary of one finished call (``result`` already redacted), or None for a tool it doesn't cover."""
    if name not in SUMMARISED:
        return None
    summary: dict[str, Any]
    if name == "read_file":
        summary = {"kind": "read", "path": _path(args, result)}
        if error is None:
            summary.update(chars=len(result["content"]), truncated=bool(result["truncated"]))
    elif name == "list_files":
        summary = {"kind": "list", "path": _path(args, result)}
        if error is None:
            entries = result["entries"]
            summary.update(count=len(entries), names=[e["path"] for e in entries[:LISTED_NAMES]])
    elif name == "write_file":
        summary = {"kind": "write", "path": _path(args, result)}
        if error is None:
            summary.update(bytes=result["bytes"], **_file_diff(before, str(args.get("content", ""))))
    elif name == "run_command":
        command = str(args.get("command", ""))
        summary = {"kind": "command", "command": command if len(command) <= COMMAND_CHARS
                   else f"{command[:COMMAND_CHARS - 1]}\u2026"}
        if error is None:
            summary.update(_run(result))
    else:
        code = str(args.get("code", "")).splitlines()
        summary = {"kind": "code", "code": [_line(c) for c in code[:CODE_LINES]], "code_lines": len(code)}
        if error is None:
            summary.update(_run(result))
    if error is not None:
        summary["error"] = error if len(error) <= ERROR_CHARS else f"{error[:ERROR_CHARS - 1]}\u2026"
    return summary


def _path(args: dict[str, Any], result: Any) -> str:
    if isinstance(result, dict) and isinstance(result.get("path"), str):
        return result["path"]
    return str(args.get("path", "."))


def _file_diff(before: dict[str, Any] | None, new: str) -> dict[str, Any]:
    """Added and removed line counts and the changed lines with a little context, as the page shows them."""
    if not before or not before["existed"]:
        old_lines: list[str] = []
        created = True
    elif "text" not in before:
        return {"created": False, "diff": None, "too_big": True}
    else:
        old_lines = before["text"].splitlines()
        created = False
    new_lines = new.splitlines()
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    added = removed = 0
    hunks: list[dict[str, Any]] = []
    shown = 0
    cut = False
    for group in matcher.get_grouped_opcodes(2):
        lines: list[list[Any]] = []
        for tag, i1, i2, j1, j2 in group:
            if tag in ("replace", "delete"):
                removed += i2 - i1
            if tag in ("replace", "insert"):
                added += j2 - j1
            if tag == "equal":
                rows = [[" ", i + 1, j1 + (i - i1) + 1, old_lines[i]] for i in range(i1, i2)]
            else:
                rows = [["-", i + 1, None, old_lines[i]] for i in range(i1, i2)]
                rows += [["+", None, j + 1, new_lines[j]] for j in range(j1, j2)]
            for row in rows:
                if shown >= DIFF_LINES:
                    cut = True
                    break
                row[3] = _line(row[3])
                lines.append(row)
                shown += 1
        if lines:
            hunks.append({"lines": lines})
    return {"created": created, "added": added, "removed": removed, "diff": hunks, "cut": cut}


# pytest: "==== 2 failed, 5 passed, 1 skipped in 0.31s ====" (also "-q": "2 failed, 5 passed in 0.31s")
_PYTEST_TOTALS = re.compile(r"^=*\s*((?:\d+ (?:failed|passed|errors?|skipped|xfailed|xpassed|deselected|warnings?)"
                            r"(?:, )?)+) in [\d.]+s(?: \([^)]*\))?\s*=*$", re.M)
_PYTEST_COUNT = re.compile(r"(\d+) (failed|passed|errors?|skipped|xfailed|xpassed)")
# pytest -v: "tests/test_x.py::test_a PASSED  [ 50%]"; the short summary: "FAILED tests/test_x.py::test_a - Assert..."
_PYTEST_VERBOSE = re.compile(r"^(\S+::\S+) (PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b", re.M)
_PYTEST_SHORT = re.compile(r"^(FAILED|ERROR) (\S+::\S+)", re.M)
# unittest: "Ran 6 tests in 0.002s" then "OK", "OK (skipped=1)" or "FAILED (failures=1, errors=1)"
_UNITTEST_RAN = re.compile(r"^Ran (\d+) tests? in [\d.]+s$", re.M)
_UNITTEST_END = re.compile(r"^(OK|FAILED)(?: \(([^)]*)\))?\s*$", re.M)
# unittest -v: "test_a (test_x.TestX.test_a) ... ok"; failures: "FAIL: test_a (test_x.TestX.test_a)"
_UNITTEST_VERBOSE = re.compile(r"^(\w+) \(([\w.]+)\)(?: \.\.\.|\n.*?\.\.\.) (ok|FAIL|ERROR|skipped|expected failure)",
                               re.M)
_UNITTEST_FAILED = re.compile(r"^(FAIL|ERROR): (\w+) \(([\w.]+)\)", re.M)

_OUTCOME = {"PASSED": "passed", "FAILED": "failed", "ERROR": "error", "SKIPPED": "skipped", "XFAIL": "skipped",
            "XPASS": "passed", "ok": "passed", "FAIL": "failed", "skipped": "skipped",
            "expected failure": "skipped"}


def _run(result: dict[str, Any]) -> dict[str, Any]:
    out = f"{result.get('stdout', '')}{result.get('stderr', '')}"
    lines = out.rstrip("\n").splitlines()
    return {
        "exit_code": result["exit_code"],
        "timed_out": bool(result.get("timed_out")),
        "output_lines": len(lines),
        "tail": [_line(t) for t in lines[-TAIL_LINES:]],
        "tests": tests_in(out),
    }


def tests_in(output: str) -> dict[str, Any] | None:
    """The test counts (and named results) from pytest or unittest output, or None if it ran no tests."""
    if totals := list(_PYTEST_TOTALS.finditer(output)):
        counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
        for n, word in _PYTEST_COUNT.findall(totals[-1].group(1)):
            key = {"error": "errors", "xfailed": "skipped", "xpassed": "passed"}.get(word, word)
            counts[key] += int(n)
        results = {name: _OUTCOME[o] for name, o in _PYTEST_VERBOSE.findall(output)}
        for o, name in _PYTEST_SHORT.findall(output):
            results.setdefault(name, _OUTCOME[o])
        return {"runner": "pytest", **counts, "results": _named(results)}
    if (ran := list(_UNITTEST_RAN.finditer(output))) and (end := list(_UNITTEST_END.finditer(output))):
        total = int(ran[-1].group(1))
        detail = dict(part.split("=") for part in (end[-1].group(2) or "").replace(" ", "").split(",") if "=" in part)
        failed, errors = int(detail.get("failures", 0)), int(detail.get("errors", 0))
        skipped = int(detail.get("skipped", 0)) + int(detail.get("expected_failures", 0))
        results = {_unittest_name(name, where): _OUTCOME[o] for name, where, o in _UNITTEST_VERBOSE.findall(output)}
        for o, name, where in _UNITTEST_FAILED.findall(output):
            results[_unittest_name(name, where)] = _OUTCOME[o]
        return {"runner": "unittest", "passed": total - failed - errors - skipped, "failed": failed,
                "errors": errors, "skipped": skipped, "results": _named(results)}
    return None


def _unittest_name(name: str, where: str) -> str:
    """``module.Class.test`` (Python 3.11 and later print it so; earlier versions print only ``module.Class``)."""
    return where if where.endswith(f".{name}") else f"{where}.{name}"


def _named(results: dict[str, str]) -> list[dict[str, str]]:
    return [{"name": _line(n), "outcome": o} for n, o in list(results.items())[:TEST_RESULTS]]
