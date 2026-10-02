"""The live work view's tool summaries and the avatar's working pose.

Each file or code call through the real gate (with the real Sentinel and the container sandbox in real
Docker) publishes one ``tool.summary`` event: files read, listed and written (with the before/after diff),
the command or code run with its exit status and the end of its output, and the test counts and results
when it ran tests. The summaries hold the user's own content, so they must stay on the in-memory event bus:
never in the audit history or any log record, and dropped with the conversation they belong to.
The test counts are read from the real output of pytest and unittest, run here for real."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import textwrap

import pytest

from jig import work
from jig.config import load_config
from jig.constants import AvatarState, EventType, Mode, RunStatus
from jig.events import AvatarStateTracker, EventBus
from jig.runtime import Jig

from .sandbox_helpers import gated_call

MARK_FILE = "Quokkalantern"
MARK_OUT = "Pangolinbiscuit"

SUITE = textwrap.dedent("""
    import unittest

    def add(a, b):
        return a + b

    class TestAdd(unittest.TestCase):
        def test_small(self):
            self.assertEqual(add(1, 2), 3)

        def test_negative(self):
            self.assertEqual(add(-1, -1), -2)

        def test_wrong(self):
            self.assertEqual(add(2, 2), 5)

        @unittest.skip("not today")
        def test_skipped(self):
            pass

    if __name__ == "__main__":
        unittest.main()
""")


@pytest.fixture
async def cjig(tmp_path, capabilities):
    runtime = Jig(load_config(data_dir=tmp_path / "data", sandbox_dir=tmp_path / "sandbox", sandbox_backend="container"))
    await runtime.start(run_scheduler=False, check_capabilities=False)
    try:
        yield runtime
    finally:
        await runtime.stop()


def summaries(seen, run_id):
    return [e.data for e in seen if e.type == EventType.TOOL_SUMMARY and e.data["run_id"] == run_id]


async def test_file_and_command_calls_are_summarised_for_the_page_only(cjig, caplog):
    caplog.set_level(logging.DEBUG)
    seen = []
    cjig.bus.add_listener(seen.append)
    sid = "sess_workview"
    cjig.store.save_session(sid, [{"role": "user", "content": "Fix my sums"}])
    run_id = cjig.store.create_run(kind="chat", mode=Mode.ACTION, session_id=sid)
    intent = "Write a small Python module with unit tests in the workspace, run the tests and fix what fails."

    first = f"# {MARK_FILE}\n{SUITE}"
    out, _ = await gated_call(cjig, "write_file", {"path": "sums/test_sums.py", "content": first}, intent=intent,
                              run_id=run_id)
    assert out.ok, out.error
    out, _ = await gated_call(cjig, "run_command", {"command": f"echo {MARK_OUT} && cd sums && python3 -m unittest -v"},
                              intent=intent, run_id=run_id)
    assert out.ok, out.error
    fixed = first.replace("add(2, 2), 5", "add(2, 2), 4")
    out, _ = await gated_call(cjig, "write_file", {"path": "sums/test_sums.py", "content": fixed, "overwrite": True},
                              intent=intent, run_id=run_id)
    assert out.ok, out.error
    out, _ = await gated_call(cjig, "read_file", {"path": "sums/test_sums.py"}, intent=intent, run_id=run_id)
    assert out.ok, out.error
    out, _ = await gated_call(cjig, "list_files", {"path": "sums"}, intent=intent, run_id=run_id)
    assert out.ok, out.error
    out, _ = await gated_call(cjig, "read_file", {"path": "sums/missing.py"}, intent=intent, run_id=run_id)
    assert not out.ok

    got = summaries(seen, run_id)
    assert [s["tool"] for s in got] == ["write_file", "run_command", "write_file", "read_file", "list_files", "read_file"]
    created, ran, changed, read, listed, missing = (s["summary"] for s in got)
    assert all(s["call_id"] for s in got)

    assert created["kind"] == "write" and created["path"] == "sums/test_sums.py" and created["created"] is True
    assert created["added"] == len(first.splitlines()) and created["removed"] == 0

    assert ran["kind"] == "command" and ran["command"].endswith("python3 -m unittest -v")
    assert ran["exit_code"] == 1 and ran["timed_out"] is False
    assert MARK_OUT in ran["tail"]
    tests = ran["tests"]
    assert (tests["runner"], tests["passed"], tests["failed"], tests["errors"], tests["skipped"]) == ("unittest", 2, 1, 0, 1)
    outcomes = {r["name"].rsplit(".", 1)[-1]: r["outcome"] for r in tests["results"]}
    assert outcomes == {"test_small": "passed", "test_negative": "passed", "test_wrong": "failed",
                        "test_skipped": "skipped"}

    assert changed["created"] is False and (changed["added"], changed["removed"]) == (1, 1)
    rows = [row for hunk in changed["diff"] for row in hunk["lines"]]
    assert [r[0] for r in rows].count("-") == 1 and [r[0] for r in rows].count("+") == 1
    minus = next(r for r in rows if r[0] == "-")
    plus = next(r for r in rows if r[0] == "+")
    assert "add(2, 2), 5" in minus[3] and "add(2, 2), 4" in plus[3] and minus[1] == plus[2]
    assert any(r[0] == " " for r in rows), "the change is shown with a little context"

    assert read == {"kind": "read", "path": "sums/test_sums.py", "chars": len(fixed), "truncated": False}
    assert listed["kind"] == "list" and listed["count"] == 1 and listed["names"] == ["sums/test_sums.py"]
    assert missing["kind"] == "read" and "does not exist" in missing["error"] and not got[-1]["ok"]

    ran_again, _ = await gated_call(cjig, "run_command", {"command": "cd sums && python3 -m unittest"}, intent=intent,
                                    run_id=run_id)
    assert ran_again.ok
    again = summaries(seen, run_id)[-1]["summary"]
    assert again["exit_code"] == 0 and (again["tests"]["passed"], again["tests"]["failed"]) == (3, 0)

    # Only on the bus: not in the audit history, nor in any log record of the whole run.
    audit = repr(cjig.audit.query(limit=5000, run_id=run_id))
    assert MARK_FILE not in audit and MARK_OUT not in audit
    logged = "\n".join(f"{r.getMessage()} {r.args!r}" for r in caplog.records)
    assert MARK_FILE not in logged and MARK_OUT not in logged
    assert any(MARK_OUT in repr(e.data) for e in cjig.bus.recent)

    # Deleting the conversation drops them from the events a page could replay.
    cjig.store.finish_run(run_id, status=RunStatus.DONE, final="Fixed")
    cjig.store.delete_conversation(sid)
    assert not [e for e in cjig.bus.recent if e.data.get("run_id") == run_id]
    assert not any(MARK_FILE in repr(e.data) or MARK_OUT in repr(e.data) for e in cjig.bus.recent)


async def test_python_code_and_secrets_in_summaries(cjig):
    seen = []
    cjig.bus.add_listener(seen.append)
    run_id = cjig.store.create_run(kind="chat", mode=Mode.ACTION)
    code = "import sys\nprint('hello from the sandbox')\nsys.exit(3)"
    out, _ = await gated_call(cjig, "run_python", {"code": code}, intent="Run a short Python script.", run_id=run_id)
    assert out.ok, out.error
    s = summaries(seen, run_id)[-1]["summary"]
    assert s["kind"] == "code" and s["code"] == code.splitlines() and s["code_lines"] == 3
    assert s["exit_code"] == 3 and s["tail"] == ["hello from the sandbox"] and s["tests"] is None


def _real(*args, cwd):
    # -B: the suite is rewritten in place below, with the same size and maybe within the same second
    return subprocess.run([sys.executable, "-B", *args], cwd=cwd, capture_output=True, text=True, timeout=120)


def test_test_counts_come_from_real_pytest_and_unittest_output(tmp_path):
    (tmp_path / "test_sums.py").write_text(SUITE, encoding="utf-8")
    verbose = _real("-m", "pytest", "-v", "-p", "no:cacheprovider", "test_sums.py", cwd=tmp_path)
    quiet = _real("-m", "pytest", "-q", "-p", "no:cacheprovider", "test_sums.py", cwd=tmp_path)
    for run in (verbose, quiet):
        t = work.tests_in(run.stdout + run.stderr)
        assert (t["runner"], t["passed"], t["failed"], t["skipped"]) == ("pytest", 2, 1, 1), run.stdout
        assert {"name": "test_sums.py::TestAdd::test_wrong", "outcome": "failed"} in t["results"]
    named = {r["name"]: r["outcome"] for r in work.tests_in(verbose.stdout)["results"]}
    assert named["test_sums.py::TestAdd::test_small"] == "passed"
    assert named["test_sums.py::TestAdd::test_skipped"] == "skipped"

    plain = _real("-m", "unittest", "test_sums", cwd=tmp_path)
    t = work.tests_in(plain.stdout + plain.stderr)
    assert (t["runner"], t["passed"], t["failed"], t["errors"], t["skipped"]) == ("unittest", 2, 1, 0, 1)
    assert [r["name"] for r in t["results"]] == ["test_sums.TestAdd.test_wrong"]

    (tmp_path / "test_sums.py").write_text(SUITE.replace("add(2, 2), 5", "add(2, 2), 4"), encoding="utf-8")
    ok = _real("-m", "unittest", "test_sums", cwd=tmp_path)
    t = work.tests_in(ok.stdout + ok.stderr)
    assert (t["passed"], t["failed"], t["skipped"]) == (3, 0, 1)
    assert work.tests_in("total 4\ndrwxr-xr-x 2 jig jig 4096 .\n") is None


async def test_the_working_pose_stays_up_after_a_quick_tool_call():
    bus = EventBus()
    tracker = AvatarStateTracker(bus)
    pose = lambda: (tracker.current["state"], tracker.current["variant"])  # noqa: E731
    bus.publish(EventType.RUN_START, run_id="r1", kind="chat", mode=Mode.ACTION)
    assert pose() == (AvatarState.THINKING.value, None)
    bus.publish(EventType.TOOL_START, run_id="r1", tool="run_command", variant="coding", effect="side_effect")
    bus.publish(EventType.TOOL_END, run_id="r1", tool="run_command", ok=True)
    bus.publish(EventType.MODEL_START, run_id="r1")
    assert pose() == (AvatarState.WORKING.value, "coding"), "a quick call still shows its pose"
    bus.publish(EventType.TOOL_START, run_id="r1", tool="write_file", variant="writing", effect="side_effect")
    assert pose() == (AvatarState.WORKING.value, "writing"), "the next call takes over directly"
    bus.publish(EventType.TOOL_END, run_id="r1", tool="write_file", ok=True)
    bus.publish(EventType.MODEL_START, run_id="r1")
    await asyncio.sleep(1.0)
    assert pose() == (AvatarState.WORKING.value, "writing")
    await asyncio.sleep(2.0)
    assert pose() == (AvatarState.THINKING.value, None), "then Jig is thinking again"

    bus.publish(EventType.TOOL_START, run_id="r1", tool="run_command", variant="coding", effect="side_effect")
    bus.publish(EventType.TOOL_END, run_id="r1", tool="run_command", ok=True)
    bus.publish(EventType.CHAT_DELTA, run_id="r1", kind="content", text="Done")
    assert pose() == (AvatarState.TALKING.value, None), "an answer is shown at once"
    bus.publish(EventType.RUN_END, run_id="r1", status="done")
    assert pose() == (AvatarState.SUCCESS.value, None)
