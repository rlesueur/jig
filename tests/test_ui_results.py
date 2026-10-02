"""Task and goal results in "What Jig's up to" are shown formatted, like Jig's replies, not as raw Markdown.
Against a real `jig serve` on its own port and the real model, in a real browser."""

from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest

from .server_helpers import free_port, kill, start_jig, token, wait_health

TITLE = "Takings by shop"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("ui-results")
    port = free_port()
    proc, log = start_jig(root / "data", port, env={"JIG_SANDBOX_DIR": str(root / "sandbox")})
    try:
        wait_health(port, proc=proc, log=log)
        yield root / "data", f"http://127.0.0.1:{port}"
    finally:
        kill(proc)


def test_task_results_are_formatted(server):
    data, base = server
    playwright = pytest.importorskip("playwright.sync_api", reason="pip install playwright")
    headers = token(data)
    task = httpx.post(f"{base}/tasks", headers=headers, timeout=30, json={
        "title": TITLE, "mode": "research",
        "description": "Do not use any tools. Reply with exactly these three lines and nothing else:\n"
                       "**Leeds**: £1,392.70\n- Harrogate: £190.95\n- York: £165.00",
    }).json()
    deadline = time.monotonic() + 300
    while (t := httpx.get(f"{base}/tasks/{task['id']}", headers=headers, timeout=30).json())["status"] not in (
            "done", "failed", "cancelled", "blocked"):
        assert time.monotonic() < deadline, "the task did not finish"
        time.sleep(1)
    assert t["status"] == "done", t
    assert "**Leeds**" in t["result"], f"the model did not reply in Markdown, so this test proves nothing: {t['result']!r}"

    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            code = httpx.post(f"{base}/auth/login-code", headers=headers, timeout=10).json()["code"]
            page = browser.new_page()
            page.goto(f"{base}/#code={code}")
            page.wait_for_selector("#app:not([hidden])", timeout=60_000)
            page.get_by_test_id("doing-open").click()
            page.locator("summary", has_text="Everything, in detail").first.click()
            item = page.get_by_test_id("task").filter(has_text=TITLE)
            item.locator("summary", has_text="Result").click()
            body = item.locator(".md.result")
            body.wait_for(state="visible")
            assert body.locator("strong").first.inner_text() == "Leeds"
            assert body.locator("li").count() == 2
            assert "**" not in body.inner_text()
            assert item.locator("pre").count() == 0
        finally:
            browser.close()
