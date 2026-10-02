"""Settings > Schedules, forgetting every memory and the "Running code" messaging, against a real `jig serve` (port
8794) and the real model, in a real browser. Screenshots go to JIG_UI_SCREENSHOTS (default: the test's tmp dir)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx
import pytest

from .server_helpers import kill, start_jig, token, wait_health

PORT = 8794
BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("ui-schedules")
    data = root / "data"
    proc, log = start_jig(data, PORT, env={"JIG_SANDBOX_DIR": str(root / "sandbox")})
    try:
        wait_health(PORT, proc=proc, log=log)
        yield data
    finally:
        kill(proc)


@pytest.fixture(scope="module")
def shots(tmp_path_factory) -> Path:
    path = Path(os.environ.get("JIG_UI_SCREENSHOTS") or tmp_path_factory.mktemp("screenshots"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def call(data: Path, method: str, path: str, **kw) -> httpx.Response:
    return httpx.request(method, BASE + path, headers=token(data), timeout=120, **kw)


def wait_until(fn, what: str, timeout: float = 300.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if value := fn():
            return value
        time.sleep(0.5)
    raise AssertionError(f"timed out waiting for {what}")


def audit(data: Path, kind: str) -> list[dict]:
    return call(data, "GET", f"/audit?kind={kind}").json()


def test_schedules_memory_and_sandbox_api(server):
    data = server
    r = call(data, "POST", "/schedules", json={"name": "Morning summary", "prompt": "Summarise the news",
                                              "repeat": {"kind": "weekdays", "at": "08:00"}})
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["timezone"] == "Europe/London", "defaults to [runtime] timezone"
    assert s["repeat_text"] == "Every weekday (Monday to Friday) at 08:00" and s["created_by"] == "user"
    legacy = call(data, "POST", "/schedules", json={"name": "Old style", "prompt": "p", "interval_s": 3600,
                                                   "start_in_s": 3600}).json()
    assert legacy["repeat"] == {"kind": "interval", "interval_s": 3600.0} and legacy["repeat_text"] == "Every hour"
    bad = call(data, "POST", "/schedules", json={"name": "x", "prompt": "y", "repeat": {"kind": "cron", "cron": "x"}})
    assert bad.status_code == 400 and "five fields" in bad.json()["error"]
    both = call(data, "POST", "/schedules", json={"name": "x", "prompt": "y", "interval_s": 60,
                                                 "repeat": {"kind": "daily", "at": "08:00"}})
    assert both.status_code == 400

    paused = call(data, "PATCH", f"/schedules/{s['id']}", json={"enabled": False}).json()
    assert paused["enabled"] is False
    assert audit(data, "schedule.paused")[-1]["data"]["schedule_id"] == s["id"]
    moved = call(data, "PATCH", f"/schedules/{legacy['id']}", json={"repeat": {"kind": "daily", "at": "21:00"}}).json()
    assert moved["repeat_text"] == "Every day at 21:00" and moved["timezone"] == "Europe/London"
    for x in (s, legacy):
        assert call(data, "DELETE", f"/schedules/{x['id']}").status_code == 204
    assert call(data, "GET", "/schedules").json() == []
    assert [a["summary"] for a in audit(data, "schedule.deleted")] == ["schedule 'Morning summary' deleted",
                                                                      "schedule 'Old style' deleted"]

    for text in ("Robyn's bike is green", "Robyn's sister is called Kate"):
        assert call(data, "POST", "/memory", json={"content": text}).status_code == 201
    refused = call(data, "POST", "/memory/wipe", json={})
    assert refused.status_code == 400 and len(call(data, "GET", "/memory").json()) == 2
    out = call(data, "POST", "/memory/wipe", json={"confirm": True}).json()
    assert out["forgotten"] == 2 and call(data, "GET", "/memory").json() == []
    entry = audit(data, "memory.wiped")[-1]
    assert entry["data"]["count"] == 2 and "Kate" not in str(entry)

    sb = call(data, "GET", "/sandbox").json()
    assert sb["backend"] == "directory" and sb["available"] is False and sb["tools"] == []
    assert sb["summary"].startswith("Off:")
    assert any('backend = "container"' in step for step in sb["steps"])
    assert set(sb["docker"]) >= {"cli", "daemon", "image_built", "problem"}


@pytest.fixture(scope="module")
def browser():
    playwright = pytest.importorskip("playwright.sync_api", reason="pip install playwright")
    with playwright.sync_playwright() as p:
        b = p.chromium.launch()
        try:
            yield b
        finally:
            b.close()


def signed_in(browser, data: Path, scheme: str, path: str = ""):
    code = httpx.post(f"{BASE}/auth/login-code", headers=token(data), timeout=10).json()["code"]
    context = browser.new_context(color_scheme=scheme, viewport={"width": 1280, "height": 900},
                                  timezone_id="Europe/London", locale="en-GB")
    page = context.new_page()
    page.goto(f"{BASE}/#code={code}")
    page.wait_for_selector("#app:not([hidden])", timeout=60_000)
    if path:
        page.goto(f"{BASE}/{path}")
    return context, page


def test_schedules_in_settings(server, browser, shots):
    data = server
    # One that runs straight away with the real model, so the list shows a real last result.
    ran = call(data, "POST", "/schedules", json={"name": "Quick check", "prompt": "Reply with just the word: ready",
                                                "interval_s": 3600}).json()
    wait_until(lambda: (s := call(data, "GET", "/schedules").json()[0])["last_task"] and
               s["last_task"]["status"] in ("done", "failed"), "the first scheduled run")
    assert call(data, "GET", "/schedules").json()[0]["last_task"]["status"] == "done"

    context, page = signed_in(browser, data, "light", "#settings/schedules")
    page.get_by_test_id("settings-schedules").wait_for(state="visible")
    first = page.get_by_test_id("schedule").first
    first.wait_for()
    assert first.get_by_test_id("schedule-when").inner_text() == "Every hour"
    assert first.get_by_test_id("schedule-next").inner_text().startswith("Next: ")
    assert "finished" in first.get_by_test_id("schedule-last").inner_text()
    assert first.locator("summary", has_text="Last result").count() == 1

    page.locator("#schedule-add-details > summary").click()
    assert page.get_by_test_id("schedule-tz").inner_text() == "Times are in your timezone, Europe/London."
    assert page.get_by_test_id("schedule-at").is_visible() and not page.get_by_test_id("schedule-every").is_visible()
    page.get_by_test_id("schedule-name").fill("Tech headlines")
    page.get_by_test_id("schedule-prompt").fill("Summarise the BBC technology headlines.")
    page.get_by_test_id("schedule-add").click()
    item = page.locator('[data-testid="schedule"]', has_text="Tech headlines")
    item.wait_for()
    assert item.get_by_test_id("schedule-when").inner_text() == "Every weekday (Monday to Friday) at 08:00"
    assert item.get_by_test_id("schedule-last").inner_text() == "Hasn\u2019t run yet."
    assert "First run:" in page.get_by_test_id("schedules-saved").inner_text()

    page.get_by_test_id("schedule-repeat").select_option("weekly")
    assert page.get_by_test_id("schedule-days").is_visible()
    page.locator('input[name="schedule-day"][value="wed"]').check()
    page.get_by_test_id("schedule-at").fill("18:30")
    page.get_by_test_id("schedule-name").fill("Bins")
    page.get_by_test_id("schedule-prompt").fill("Remind me which bins go out tomorrow.")
    page.get_by_test_id("schedule-add").click()
    bins = page.locator('[data-testid="schedule"]', has_text="Bins")
    bins.wait_for()
    assert bins.get_by_test_id("schedule-when").inner_text() == "Every Monday and Wednesday at 18:30"
    page.get_by_test_id("schedule-repeat").select_option("interval")
    assert page.get_by_test_id("schedule-every").is_visible() and not page.get_by_test_id("schedule-at").is_visible()
    page.get_by_test_id("schedule-repeat").select_option("weekdays")
    page.screenshot(path=str(shots / "schedules-light.png"), full_page=True)

    ran_item = page.locator(f'[data-testid="schedule"][data-id="{ran["id"]}"]')
    ran_item.get_by_test_id("schedule-toggle").click()
    page.wait_for_selector(f'[data-testid="schedule"][data-id="{ran["id"]}"][data-enabled="false"]')
    assert ran_item.get_by_test_id("schedule-next").inner_text() == "Paused: it won\u2019t run until you resume it."
    assert ran_item.locator(".status").inner_text() == "paused"
    page.screenshot(path=str(shots / "schedules-paused-light.png"), full_page=True)

    item.get_by_test_id("schedule-delete").click()
    page.get_by_test_id("confirm-dialog").wait_for(state="visible")
    assert page.get_by_test_id("confirm-title").inner_text() == "Delete \u201cTech headlines\u201d?"
    page.screenshot(path=str(shots / "schedules-delete-confirm-light.png"))
    page.get_by_test_id("confirm-ok").click()
    item.wait_for(state="detached")
    assert [s["name"] for s in call(data, "GET", "/schedules").json()] == ["Quick check", "Bins"]
    context.close()

    context, page = signed_in(browser, data, "dark", "#settings/schedules")
    page.get_by_test_id("schedule").first.wait_for()
    page.locator("#schedule-add-details > summary").click()
    page.screenshot(path=str(shots / "schedules-dark.png"), full_page=True)
    context.close()

    # Resumed from the API, the open page follows the schedule.changed event.
    context, page = signed_in(browser, data, "light", "#settings/schedules")
    page.wait_for_selector(f'[data-testid="schedule"][data-id="{ran["id"]}"][data-enabled="false"]')
    call(data, "PATCH", f"/schedules/{ran['id']}", json={"enabled": True})
    page.wait_for_selector(f'[data-testid="schedule"][data-id="{ran["id"]}"][data-enabled="true"]', timeout=10_000)
    call(data, "PATCH", f"/schedules/{ran['id']}", json={"enabled": False})
    context.close()


def test_forget_everything_in_settings(server, browser, shots):
    data = server
    for text in ("Robyn takes her coffee black", "Robyn's cat is called Biscuit", "Robyn rows on Saturdays"):
        call(data, "POST", "/memory", json={"content": text})
    context, page = signed_in(browser, data, "light", "#settings/memory")
    page.wait_for_selector('#memory-heading:text-is("Memories (3)")')
    page.locator('[data-testid="memory-wipe-details"] > summary').click()
    page.get_by_test_id("memory-wipe").click()
    page.get_by_test_id("confirm-dialog").wait_for(state="visible")
    assert page.get_by_test_id("confirm-title").inner_text() == "Forget everything Jig keeps about you?"
    assert "can\u2019t be undone" in page.get_by_test_id("confirm-body").inner_text()
    assert "btn-danger" in page.get_by_test_id("confirm-ok").get_attribute("class")
    page.screenshot(path=str(shots / "memory-wipe-confirm-light.png"))
    page.get_by_test_id("confirm-cancel").click()
    assert len(call(data, "GET", "/memory").json()) == 3, "cancel forgets nothing"

    page.get_by_test_id("memory-wipe").click()
    page.get_by_test_id("confirm-ok").click()
    # The finished jobs the schedule tests left behind go too.
    page.wait_for_selector("#memory-saved:text-matches('^Jig deleted 3 memories')")
    page.wait_for_selector('#memory-heading:text-is("Memories (0)")')
    assert page.locator('[data-testid="memories"] .empty').inner_text() == "Jig hasn\u2019t remembered anything yet."
    page.screenshot(path=str(shots / "memory-after-wipe-light.png"), full_page=True)
    assert call(data, "GET", "/memory").json() == []
    assert audit(data, "everything.forgotten")[-1]["data"]["memories"] == 3
    context.close()


def test_running_code_is_explained(server, browser, shots):
    data = server
    for scheme in ("light", "dark"):
        context, page = signed_in(browser, data, scheme, "#settings/model")
        page.wait_for_selector('#st-code:text-matches("^(On|Off):")', timeout=60_000)
        assert page.get_by_test_id("code-execution").inner_text().startswith("Off: Jig is using the folder-only sandbox")
        assert page.get_by_test_id("code-help").is_visible()
        # Turned on from here, with no terminal: a button once Docker is ready, or the steps to get it ready.
        actions = page.get_by_test_id("code-actions")
        actions.locator("button").first.wait_for(timeout=60_000)
        buttons = actions.locator("button").all_inner_texts()
        assert any(b in buttons for b in ("Turn on running code\u2026", "Build the safe container",
                                          "Check for Docker again")), buttons
        assert 'backend = "container"' not in page.get_by_test_id("code-steps").inner_text()
        page.screenshot(path=str(shots / f"running-code-{scheme}.png"), full_page=True)
        page.goto(f"{BASE}/#settings/rules")
        note = page.get_by_test_id("no-code-note")
        note.wait_for(state="visible")
        assert note.inner_text().startswith("Running code and using a web browser are off")
        context.close()


def test_agent_schedule_proposal_card(server, browser, shots):
    """A background job asks to set up a schedule; the card says exactly what will be saved and when it runs."""
    data = server
    task = call(data, "POST", "/tasks", json={
        "title": "Set up the tech headlines schedule", "mode": "action",
        "description": "Use the schedule_create tool to set up a schedule named 'Tech headlines' that runs every "
                       "weekday at 08:00 and summarises the BBC News technology headlines."}).json()
    approval = wait_until(lambda: next((a for a in call(data, "GET", "/approvals?status=pending").json()
                                        if a["task_id"] == task["id"]), None), "the schedule approval")
    assert approval["tool"] == "schedule_create"
    context, page = signed_in(browser, data, "light")
    card = page.locator(f'[data-testid="approval"][data-id="{approval["id"]}"]')
    card.wait_for(timeout=60_000)
    assert card.locator(".ask-title").inner_text().startswith("Can I set up a schedule called")
    assert "At the times below it will do this by itself" in card.get_by_test_id("approval-will").inner_text()
    about = card.get_by_test_id("approval-about").inner_text()
    assert "Repeats" in about and "Next runs" in about and "Europe/London" in about
    assert card.get_by_test_id("approval-preview").is_visible()
    card.scroll_into_view_if_needed()
    page.screenshot(path=str(shots / "schedule-approval-light.png"))
    card.get_by_test_id("approval-deny").click()
    page.wait_for_selector(f'[data-testid="approval"][data-id="{approval["id"]}"][data-status="denied"]')
    call(data, "POST", f"/tasks/{task['id']}/cancel")
    assert all(s["name"] != "Tech headlines" for s in call(data, "GET", "/schedules").json()), \
        "denied, so nothing was saved"
    context.close()
