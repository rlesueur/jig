"""Tool failures in the conversation: a reply that carries on shows one quiet line, and a reply that was stopped
keeps that line plainly visible. Both run in a real browser against a real `jig serve` on its own port, the real
model and real page fetches. Each failure stays in the Steps view."""

from __future__ import annotations

import re
import time

import httpx
import pytest

from .server_helpers import free_port, kill, start_jig, token, wait_health

DESKTOP = {"width": 1280, "height": 820}
PAGES = re.compile(r"^(\d+) pages couldn.t be read$")
STEPS = re.compile(r"^(\d+) steps had problems$|^(1) step had a problem$")


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("ui-quiet")
    port = free_port()
    proc, log = start_jig(root / "data", port, env={"JIG_SANDBOX_DIR": str(root / "sandbox")})
    try:
        wait_health(port, proc=proc, log=log)
        yield root / "data", f"http://127.0.0.1:{port}"
    finally:
        kill(proc)


def _failures(base: str, headers: dict[str, str]) -> list[dict]:
    events = httpx.get(f"{base}/events/recent", params={"type": "tool.end"}, headers=headers, timeout=10).json()
    return [event for event in events if event.get("data", {}).get("ok") is False]


def _approve(page) -> None:
    pending = page.locator('#corner-held [data-testid="approval"][data-status="pending"]')
    if not pending.count():
        return
    card = pending.first
    tool = card.get_attribute("data-tool")
    card.get_by_role("button", name=f"Yes, approve {tool}", exact=True).click()


def _open(playwright, browser, base: str, headers: dict[str, str]):
    code = httpx.post(f"{base}/auth/login-code", headers=headers, timeout=10).json()["code"]
    page = browser.new_page(viewport=DESKTOP)
    page.goto(f"{base}/#code={code}")
    page.wait_for_selector("#app:not([hidden])", timeout=60_000)
    return page


def _ask(page, urls: list[str]) -> None:
    message = (
        "Use the web_fetch tool on every address below. Do not use any other tool. "
        "After the fetches, reply in one sentence that you could not read those pages.\n\n"
        + "\n".join(urls)
    )
    page.get_by_test_id("chat-input").fill(message)
    page.get_by_test_id("chat-send").click()
    page.wait_for_function("() => document.getElementById('chat-send').disabled", timeout=30_000)


def _line_count(text: str) -> int:
    match = PAGES.fullmatch(text) or STEPS.fullmatch(text)
    assert match, text
    return int(next(group for group in match.groups() if group))


@pytest.mark.network
def test_many_recovered_page_failures_are_one_quiet_line(server):
    data, base = server
    playwright = pytest.importorskip("playwright.sync_api", reason="pip install playwright")
    headers = token(data)
    urls = [f"https://example.com/jig-quiet-{i}" for i in range(1, 5)]
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = _open(playwright, browser, base, headers)
            _ask(page, urls)
            deadline = time.monotonic() + 8 * 60
            while page.get_by_test_id("chat-send").is_disabled():
                assert time.monotonic() < deadline, "the reply did not finish"
                _approve(page)
                time.sleep(0.25)
            log = page.get_by_test_id("chat-log")
            trouble = log.get_by_test_id("chat-trouble")
            assert trouble.count() == 1, log.inner_text()[-800:]
            assert log.get_by_test_id("chat-problem").count() == 0
            assert "carried on without it" not in log.inner_text()
            text = trouble.inner_text().strip()
            count = _line_count(text)
            assert count >= 3, text
            assert trouble.get_attribute("data-count") == str(count)
            assert trouble.get_attribute("data-settled") == "false"
            assert "reply-trouble" in (trouble.get_attribute("class") or "")
            reply = log.locator('[data-testid="chat-jig"]').last
            answer = reply.locator(".content").inner_text().strip()
            assert answer, "the reply had no answer above the line"
            assert reply.evaluate("""(msg) => {
                const answer = msg.querySelector('.content');
                const line = msg.querySelector('[data-testid="chat-trouble"]');
                return Boolean(answer && line && (answer.compareDocumentPosition(line) & Node.DOCUMENT_POSITION_FOLLOWING));
            }""")
            # A refusal, a stop and a claim note are separate and were not part of this reply.
            assert reply.get_by_test_id("chat-stopped").count() == 0
            assert reply.get_by_test_id("chat-claim").count() == 0
            trouble.click()
            pane = page.get_by_test_id("steps")
            pane.wait_for()
            failed = pane.locator('[data-testid="step"][data-state="failed"]')
            assert failed.count() >= count
            assert "HTTP 404" in failed.first.inner_text()
            assert "example.com/jig-quiet-" in pane.inner_text()
        finally:
            browser.close()


@pytest.mark.network
def test_a_stopped_reply_keeps_the_failure_visible(server):
    data, base = server
    playwright = pytest.importorskip("playwright.sync_api", reason="pip install playwright")
    headers = token(data)
    urls = [f"https://example.com/jig-stopped-{i}" for i in range(1, 9)]
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = _open(playwright, browser, base, headers)
            before = len(_failures(base, headers))
            _ask(page, urls)
            page.get_by_test_id("chat-send").wait_for(state="visible")
            deadline = time.monotonic() + 8 * 60
            opened = False
            while page.locator('[data-testid="step"][data-state="failed"]').count() < 2:
                assert page.get_by_test_id("chat-send").is_disabled(), (
                    "the reply finished before two pages had failed:\n"
                    + page.get_by_test_id("chat-log").inner_text()[-800:]
                )
                assert time.monotonic() < deadline, "two page fetches did not fail"
                _approve(page)
                if not opened and page.get_by_test_id("chat-steps").count():
                    page.get_by_test_id("chat-steps").click()
                    opened = True
                elif len(_failures(base, headers)) >= before + 2 and not opened:
                    page.get_by_test_id("chat-steps").wait_for(timeout=30_000)
                    page.get_by_test_id("chat-steps").click()
                    opened = True
                time.sleep(0.25)
            page.get_by_test_id("chat-stop").click()
            page.get_by_test_id("chat-send").wait_for(state="attached")
            deadline = time.monotonic() + 60
            while page.get_by_test_id("chat-send").is_disabled():
                assert time.monotonic() < deadline, "the reply did not stop"
                time.sleep(0.2)
            if page.locator("#tab-chat").count():
                page.locator("#tab-chat").click()
            log = page.get_by_test_id("chat-log")
            problem = log.get_by_test_id("chat-problem")
            problem.wait_for()
            assert problem.count() == 1
            assert log.get_by_test_id("chat-trouble").count() == 0
            assert "carried on without it" not in log.inner_text()
            text = problem.inner_text().strip()
            count = _line_count(text)
            assert count >= 2, text
            assert problem.get_attribute("data-settled") == "true"
            assert "reply-problem" in (problem.get_attribute("class") or "")
            assert log.get_by_test_id("chat-stopped-by-you").count() == 1
            reply = log.locator('[data-testid="chat-jig"]').last
            assert reply.evaluate("""(msg) => {
                const answer = msg.querySelector('.content');
                const line = msg.querySelector('[data-testid="chat-problem"]');
                const stopped = msg.querySelector('[data-testid="chat-stopped-by-you"]');
                const after = Node.DOCUMENT_POSITION_FOLLOWING;
                return Boolean(answer && line && stopped
                    && (answer.compareDocumentPosition(line) & after)
                    && (line.compareDocumentPosition(stopped) & after));
            }""")
            problem.click()
            pane = page.get_by_test_id("steps")
            pane.wait_for()
            assert "HTTP 404" in pane.inner_text()
        finally:
            browser.close()
