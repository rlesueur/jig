"""Jig's corner and the work view, in a real browser, against a real `jig serve` on its own port, the real model and
the container sandbox: a real coding task with failing tests, followed from the corner (one line, a pass/fail meter),
into the steps (the command and each test's result, the change as before and after), on a wide and a narrow window.
The page itself never scrolls, the status line says Jig is busy while it replies, "Stop the reply" stays in Jig's
corner (the only Stop for a reply, on a narrow window too), approvals wait in Jig's corner, and the per-step details
live only in the page: they leave the event history when the conversation is deleted."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import httpx
import pytest

from jig.config import load_config

from .server_helpers import free_port, kill, start_jig, token, wait_health

FIXTURE = Path(__file__).resolve().parents[1] / "demos" / "fixtures" / "coding"
ASK = ("The tests for report.py in your workspace are failing. Run them in your sandbox with python3 -m unittest -v, "
       "find out why, fix report.py (standard library only) and run the tests again until they all pass.")
DESKTOP = {"width": 1280, "height": 820}
NARROW = {"width": 390, "height": 844}


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("ui-work")
    workspace = root / "sandbox" / load_config().runtime.agent_id
    shutil.copytree(FIXTURE, workspace)
    port = free_port()
    proc, log = start_jig(root / "data", port, env={"JIG_SANDBOX_DIR": str(root / "sandbox"),
                                                    "JIG_SANDBOX_BACKEND": "container"})
    try:
        wait_health(port, proc=proc, log=log)
        yield root / "data", f"http://127.0.0.1:{port}", workspace
    finally:
        kill(proc)


def _page_scrolls(page) -> int:
    return page.evaluate("document.scrollingElement.scrollHeight - innerHeight")


def _visible_jigs(page) -> list[int]:
    return page.evaluate("""[...document.querySelectorAll('jig-avatar')]
        .filter((n) => n.offsetParent !== null && n.getBoundingClientRect().width > 0)
        .map((n) => Math.round(n.getBoundingClientRect().width))""")


def _work_words(page) -> str:
    """The work line's words, without its mark."""
    line = page.get_by_test_id("work-line")
    if not line.count():
        return ""
    return line.evaluate("""(el) => [...el.childNodes]
        .filter((n) => !(n.nodeType === 1 && n.getAttribute('aria-hidden')))
        .map((n) => n.textContent).join('').trim()""")


def _now(page) -> dict:
    """In one look, so a reply that ends in between cannot mix two moments: whether Jig is still replying (Send is
    off), the status line, and whether "Stop the reply" is on screen."""
    return page.evaluate("""() => {
        const stop = document.getElementById('chat-stop');
        const r = stop.getBoundingClientRect();
        return { replying: document.getElementById('chat-send').disabled,
                 doing: document.getElementById('doing-text').innerText,
                 stop: stop.checkVisibility() && r.width > 0 && r.top >= 0 && r.bottom <= innerHeight,
                 workStops: document.querySelectorAll('[data-testid="work-stop"]').length };
    }""")


def _wait_for_work_words(page, words: str, timeout_s: float = 10) -> None:
    end = time.monotonic() + timeout_s
    while (now := _work_words(page)) != words:
        assert time.monotonic() < end, f"the work line said {now!r}, not {words!r}"
        time.sleep(0.2)


def test_a_coding_task_from_the_corner_to_each_step(server):
    data, base, workspace = server
    playwright = pytest.importorskip("playwright.sync_api", reason="pip install playwright")
    headers = token(data)
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            code = httpx.post(f"{base}/auth/login-code", headers=headers, timeout=10).json()["code"]
            page = browser.new_page(viewport=DESKTOP)
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{base}/#code={code}")
            page.wait_for_selector("#app:not([hidden])", timeout=60_000)
            corner = page.get_by_test_id("avatar-card")
            assert corner.get_by_role("status").inner_text().strip(), "Jig's caption is the words for its pose"
            jigs = _visible_jigs(page)
            assert len(jigs) == 1 and 230 <= jigs[0] <= 280, jigs

            page.get_by_test_id("chat-input").fill(ASK)
            page.get_by_test_id("chat-send").click()

            states, tasks, doing, lines, held = set(), set(), set(), set(), 0
            grew, narrow_stop = [], None
            deadline = time.monotonic() + 15 * 60
            while page.get_by_test_id("chat-send").is_disabled():
                assert time.monotonic() < deadline, "the coding task did not finish"
                pending = page.locator('#corner-held [data-testid="approval"][data-status="pending"]')
                if pending.count():
                    aid = pending.first.get_attribute("data-id")
                    card = page.locator(f'#corner-held [data-testid="approval"][data-id="{aid}"]')
                    tool = card.get_attribute("data-tool")
                    held += 1
                    # one Stop for the reply, in the corner, while the question waits too
                    now = _now(page)
                    assert not now["replying"] or now["stop"], "no Stop the reply while a question was held"
                    # held in the corner, with a pointer to it in the reply, and the conversation still in view
                    assert page.get_by_test_id("chat-log").locator('[data-testid="approval"][data-status="pending"]').count() == 0
                    # the pointer, the caption and the work line follow approval.requested over the socket
                    playwright.expect(page.get_by_test_id("chat-waiting").last).to_be_visible(timeout=10_000)
                    playwright.expect(corner.get_by_role("status")).to_have_text("Waiting for your answer.", timeout=10_000)
                    _wait_for_work_words(page, "I need your OK to carry on.")
                    card.locator(".why-ask > summary").click()
                    assert card.get_by_test_id("sentinel-verdict").is_visible()
                    card.get_by_role("button", name=f"Yes, approve {tool}", exact=True).click()
                    # this card, not an earlier yes to the same tool, moves into the reply
                    page.get_by_test_id("chat-log").locator(f'[data-testid="approval"][data-id="{aid}"][data-status="approved"]').wait_for(timeout=15_000)
                    continue
                states.add(page.evaluate("document.documentElement.dataset.jigState || ''"))
                if page.evaluate("document.documentElement.dataset.jigState") == "working":
                    tasks.add(page.evaluate("document.getElementById('avatar').task || ''"))
                now = _now(page)
                assert now["workStops"] == 0, "the work line has a Stop of its own"
                if now["replying"]:
                    doing.add(now["doing"])
                    assert now["stop"], "Stop the reply was not on screen while Jig replied"
                if words := _work_words(page):
                    lines.add(words)
                    if narrow_stop is None and now["replying"]:
                        # a narrow window shows the work line in the band, and Stop the reply beside it
                        page.set_viewport_size(NARROW)
                        page.wait_for_timeout(400)
                        narrow = _now(page)
                        narrow_stop = narrow["stop"] or not narrow["replying"]
                        page.set_viewport_size(DESKTOP)
                        page.wait_for_timeout(300)
                if (n := _page_scrolls(page)) > 1:
                    grew.append(n)
                time.sleep(0.15)

            assert not grew, f"the page itself scrolled (by {grew[:3]}px) instead of the conversation"
            assert "working" in states and "coding" in tasks, (states, tasks)
            assert any(t.startswith("Replying to you") for t in doing), doing
            assert not any(t.startswith("Nothing on the go") for t in doing if t), \
                f"the status line said nothing was happening while Jig replied: {doing}"
            assert any("test" in t for t in lines), lines
            assert narrow_stop, "on a narrow window Stop the reply was not on screen beside the work line"

            # the corner after the run: one line and the meter
            page.get_by_test_id("work-meter").wait_for()
            meter = page.get_by_test_id("work-meter").get_by_role("img").get_attribute("aria-label")
            assert meter.startswith("Tests: ") and "passed" in meter, meter
            line = _work_words(page)
            assert line.startswith(("All done", "Done, but", "I couldn")), line

            # the steps, in plain words; each one opens to what was really run, or what changed
            page.get_by_test_id("work-steps-toggle").click()
            pane = page.get_by_test_id("steps")
            pane.wait_for()
            assert page.get_by_test_id("chat-log").is_hidden()
            assert page.locator("#tab-steps").get_attribute("aria-pressed") == "true"
            steps = pane.get_by_test_id("step")
            assert steps.count() == int(page.locator("#steps-count").inner_text())
            tested = pane.locator('[data-testid="step"][data-tool="run_command"]').filter(has_text="Ran the tests")
            assert tested.count() >= 1, pane.inner_text()
            first = tested.first
            first.get_by_test_id("step-toggle").click()
            detail = first.get_by_test_id("step-detail")
            assert "unittest" in detail.get_by_test_id("step-command").inner_text()
            results = detail.get_by_test_id("test-result")
            assert results.count() >= 1
            assert {results.nth(i).get_attribute("data-outcome") for i in range(results.count())} <= {"passed", "failed", "error", "skipped"}
            assert first.get_by_test_id("step-toggle").get_attribute("aria-expanded") == "true"
            first.get_by_test_id("step-toggle").click()
            assert detail.is_hidden()

            changed = pane.locator('[data-testid="step"][data-tool="write_file"]')
            assert changed.count() >= 1, "Jig did not change report.py"
            last = changed.last
            last.get_by_test_id("step-toggle").click()
            diff = last.get_by_test_id("step-diff")
            assert diff.is_visible()
            added = [t.strip() for t in diff.get_by_test_id("diff-add").locator(".dt").all_text_contents()]
            now = (workspace / "report.py").read_text(encoding="utf-8")
            assert added and any(a and a in now for a in added), added[:5]
            assert _page_scrolls(page) <= 1, "opening a change made the page scroll"
            assert len(_visible_jigs(page)) == 1

            # narrow: a band across the top; the steps come up in a sheet with Jig peeking over it
            page.set_viewport_size(NARROW)
            page.wait_for_timeout(500)
            assert page.locator("#corner").evaluate("(n) => n.classList.contains('is-sheet')")
            assert page.locator("#corner-steps [data-testid='steps']").count() == 1
            jigs = _visible_jigs(page)
            assert len(jigs) == 1 and jigs[0] >= 112, jigs
            page.get_by_test_id("steps-close").click()
            assert not page.locator("#corner").evaluate("(n) => n.classList.contains('is-sheet')")
            assert page.get_by_test_id("chat-log").is_visible()
            assert 112 <= _visible_jigs(page)[0] <= 160
            assert _page_scrolls(page) <= 1
            assert not errors, errors
        finally:
            browser.close()

    # the details were for the page only: not in the audit history, and gone with the conversation
    summaries = httpx.get(f"{base}/events/recent", params={"type": "tool.summary"}, headers=headers, timeout=10).json()
    assert summaries, "no step details were sent to the page"
    run_id = summaries[-1]["data"]["run_id"]
    audit = httpx.get(f"{base}/audit", params={"run_id": run_id, "limit": 1000}, headers=headers, timeout=10).json()
    assert audit and not any(a["kind"] == "tool.summary" for a in audit)
    assert "test_prices_with_thousands_separators" not in str(audit), "a test's name from the output reached the audit"
    session = httpx.get(f"{base}/runs/{run_id}", headers=headers, timeout=10).json()["session_id"]
    assert httpx.delete(f"{base}/sessions/{session}", headers=headers, timeout=10).status_code == 204
    after = httpx.get(f"{base}/events/recent", params={"type": "tool.summary"}, headers=headers, timeout=10).json()
    assert not [e for e in after if e["data"]["run_id"] == run_id]


def test_jig_is_off_shows_jig_asleep_in_its_corner_even_mid_question(server):
    data, base, _ = server
    playwright = pytest.importorskip("playwright.sync_api", reason="pip install playwright")
    headers = token(data)
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            code = httpx.post(f"{base}/auth/login-code", headers=headers, timeout=10).json()["code"]
            page = browser.new_page(viewport=DESKTOP)
            page.goto(f"{base}/#code={code}")
            page.wait_for_selector("#app:not([hidden])", timeout=60_000)
            # turned off while a question waits in the corner: Jig sleeps full size, with nothing left to answer
            page.get_by_test_id("chat-new").click()
            page.get_by_test_id("chat-input").fill("Run python3 -m unittest -v in your sandbox and tell me how many tests pass.")
            page.get_by_test_id("chat-send").click()
            held = page.locator('#corner-held [data-testid="approval"][data-status="pending"]')
            deadline = time.monotonic() + 600
            page.wait_for_timeout(1000)
            while not held.count():
                assert page.get_by_test_id("chat-send").is_disabled(), \
                    f"the reply ended without asking to run the tests: {page.get_by_test_id('chat-log').inner_text()[-300:]}"
                assert time.monotonic() < deadline, "Jig did not ask to run the tests"
                time.sleep(0.5)
            page.goto(f"{base}/#settings/power")
            page.locator("#power-off").click()
            page.locator("#confirm-ok").click()
            page.get_by_test_id("off-state").wait_for(timeout=60_000)
            playwright.expect(page.get_by_test_id("avatar-says")).to_contain_text("switched off", timeout=60_000)
            assert page.evaluate("document.documentElement.dataset.jigState") == "paused"
            assert _visible_jigs(page)[0] >= 230
            assert page.get_by_test_id("doing").is_hidden()
            assert not page.locator('#corner-held [data-testid="approval"][data-status="pending"]').first.is_visible()
            assert page.get_by_test_id("avatar-card").get_attribute("data-holding") == "none"
            page.set_viewport_size(NARROW)
            page.wait_for_timeout(400)
            assert _visible_jigs(page)[0] >= 112
            assert _page_scrolls(page) <= 1
        finally:
            browser.close()
