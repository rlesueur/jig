"""Settings > Conversations and jobs, the job drawer, Forget everything and the History wording: conversations and
finished jobs read and deleted one at a time or all at once, with their words gone from the database files, against
a real `jig serve` (port 8794), the real model and a real browser. Screenshots go to JIG_UI_SCREENSHOTS (default:
the test's tmp dir)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx
import pytest

from jig.config import load_config
from jig.db import Database

from .server_helpers import kill, start_jig, token, wait_health

PORT = 8794
BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("ui-conversations")
    data = root / "data"
    # Two History entries as an older Jig wrote them, quoting a conversation, before the no-content rule.
    db = Database(load_config(data_dir=data).db_path)
    for kind, payload in (("tool.call", {"tool": "web_search", "args": {"query": "flats near the old mill"}}),
                          ("run.end", {"status": "done", "final_preview": "Here are three flats"})):
        db.execute("INSERT INTO audit(ts, kind, actor, summary, data_json) VALUES "
                   "('2026-03-14T09:30:00.000+00:00', ?, 'agent', 'older entry', ?)", (kind, json.dumps(payload)))
    db.close()
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


@pytest.fixture(scope="module")
def browser():
    playwright = pytest.importorskip("playwright.sync_api", reason="pip install playwright")
    with playwright.sync_playwright() as p:
        b = p.chromium.launch()
        try:
            yield b
        finally:
            b.close()


def call(data: Path, method: str, path: str, **kw) -> httpx.Response:
    return httpx.request(method, BASE + path, headers=token(data), timeout=300, **kw)


def chat(data: Path, message: str) -> dict:
    """One real chat turn through the model; returns the final stream item."""
    r = call(data, "POST", "/chat", json={"message": message})
    assert r.status_code == 200, r.text
    last = json.loads(r.text.strip().splitlines()[-1])
    assert last["type"] == "done", last
    return last


def task_done(data: Path, title: str, description: str) -> dict:
    task = call(data, "POST", "/tasks", json={"title": title, "description": description, "mode": "research"}).json()
    wait_until(lambda: call(data, "GET", f"/tasks/{task['id']}").json()["status"] in ("done", "failed"), title)
    return call(data, "GET", f"/tasks/{task['id']}").json()


def files(data: Path) -> bytes:
    path = load_config(data_dir=data).db_path
    wal = path.with_name(path.name + "-wal")
    return path.read_bytes() + (wal.read_bytes() if wal.exists() else b"")


def wait_until(fn, what: str, timeout: float = 300.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if value := fn():
            return value
        time.sleep(0.5)
    raise AssertionError(f"timed out waiting for {what}")


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


def test_conversations_and_jobs_in_settings(server, browser, shots):
    data = server
    lemon = chat(data, "In one short sentence: what colour is a ripe lemon? Start your answer with 'Lemons'. "
                       "(Code word: dugongtrellis.)")
    chat(data, "Say hello to Persimmonwhistle in five words or fewer.")
    job = task_done(data, "Name a fruit (grebeharp)", "Reply with just the name of one fruit, nothing else.")
    later = call(data, "POST", "/tasks", json={"title": "Later job", "description": "Reply with: ok",
                                               "mode": "research", "delay_s": 3600}).json()

    context, page = signed_in(browser, data, "light", "#settings/conversations")
    page.wait_for_selector('#conversations-list-heading:text-is("Conversations (2)")')
    assert page.get_by_test_id("settings-nav-conversations").inner_text() == "Conversations and jobs"
    privacy = page.get_by_test_id("conversations-privacy").inner_text()
    assert "stored only in its database on this computer" in privacy and "Delete any of them, or all of them" in privacy
    first = page.locator(f'[data-testid="conversation"][data-id="{lemon["session_id"]}"]')
    assert first.locator(".title").inner_text().startswith("In one short sentence: what colour is a ripe lemon?")
    assert first.locator(".meta").inner_text().startswith("2 messages")
    first.locator("summary").click()
    said = first.get_by_test_id("conversation-said")
    said.nth(1).wait_for()
    assert said.nth(0).locator(".said-who").inner_text() == "You" and "dugongtrellis" in said.nth(0).inner_text()
    assert said.nth(1).locator(".said-who").inner_text() == "Jig" and said.nth(1).locator(".said-text").inner_text()

    page.wait_for_selector('#jobs-heading:text-is("Finished jobs (1)")')
    assert page.locator('[data-testid="job"]').get_attribute("data-id") == job["id"]
    assert "stop it first" in page.locator("#set-conversations").inner_text()
    page.screenshot(path=str(shots / "conversations-light.png"), full_page=True)

    page.once("dialog", lambda d: d.accept())
    first.get_by_role("button", name="Delete the conversation", exact=False).click()
    page.wait_for_selector('#conversations-saved:text-is("The conversation is deleted.")')
    page.wait_for_selector('#conversations-list-heading:text-is("Conversations (1)")')
    assert call(data, "GET", f"/sessions/{lemon['session_id']}/transcript").status_code == 404
    assert b"dugongtrellis" not in files(data), "the deleted conversation's words are still in the database files"
    assert b"Persimmonwhistle" in files(data)

    page.once("dialog", lambda d: d.accept())
    page.get_by_test_id("jobs").get_by_test_id("task-delete").click()
    page.wait_for_selector('#conversations-saved:text-is("The task is deleted.")')
    page.wait_for_selector('#jobs-heading:text-is("Finished jobs (0)")')
    assert call(data, "GET", f"/tasks/{job['id']}").status_code == 404 and b"grebeharp" not in files(data)
    assert call(data, "GET", f"/tasks/{later['id']}").json()["status"] == "queued", "unfinished jobs stay"

    page.locator('[data-testid="conversations-wipe-details"] > summary').click()
    page.get_by_test_id("conversations-wipe").click()
    page.get_by_test_id("confirm-dialog").wait_for(state="visible")
    assert page.get_by_test_id("confirm-title").inner_text() == "Delete all conversations?"
    body = page.get_by_test_id("confirm-body").inner_text()
    assert "What Jig remembers about you, its notes and its jobs stay." in body and "can\u2019t be undone" in body
    page.screenshot(path=str(shots / "conversations-wipe-confirm-light.png"))
    page.get_by_test_id("confirm-cancel").click()
    assert len(call(data, "GET", "/sessions").json()) == 1, "cancel deletes nothing"
    page.get_by_test_id("conversations-wipe").click()
    page.get_by_test_id("confirm-ok").click()
    page.wait_for_selector('#conversations-saved:text-is("Deleted 1 conversation.")')
    page.wait_for_selector('#conversations-list-heading:text-is("Conversations (0)")')
    assert b"Persimmonwhistle" not in files(data)
    page.screenshot(path=str(shots / "conversations-empty-light.png"), full_page=True)

    audit = call(data, "GET", "/audit", params={"limit": 2000}).json()
    kinds = [a["kind"] for a in audit]
    assert {"conversation.deleted", "task.deleted", "conversation.wiped"} <= set(kinds)
    newer = json.dumps([a for a in audit if a["summary"] != "older entry"])
    for word in ("dugongtrellis", "Persimmonwhistle", "grebeharp", "Lemons", "lemon"):
        assert word not in newer, f"{word!r} was copied into the audit log"
    call(data, "POST", f"/tasks/{later['id']}/cancel")
    context.close()


def test_job_drawer_and_the_open_chat(server, browser, shots):
    data = server
    job = task_done(data, "Name a colour", "Reply with just the name of one colour, nothing else.")
    waiting = call(data, "POST", "/tasks", json={"title": "Not yet", "description": "Reply with: ok",
                                                 "mode": "research", "delay_s": 3600}).json()
    context, page = signed_in(browser, data, "dark")
    page.get_by_test_id("doing-open").click()
    page.locator('[data-testid="activity-more"] > summary').click()
    finished = page.locator(f'[data-testid="task"][data-id="{job["id"]}"]')
    finished.get_by_test_id("task-delete").wait_for()
    assert page.locator(f'[data-testid="task"][data-id="{waiting["id"]}"] [data-testid="task-delete"]').count() == 0
    assert "has to be stopped first" in page.get_by_test_id("activity-delete-hint").inner_text()
    finished.scroll_into_view_if_needed()
    page.screenshot(path=str(shots / "activity-delete-dark.png"))
    page.once("dialog", lambda d: d.accept())
    finished.get_by_test_id("task-delete").click()
    finished.wait_for(state="detached")
    assert call(data, "GET", f"/tasks/{job['id']}").status_code == 404
    refused = call(data, "DELETE", f"/tasks/{waiting['id']}")
    assert refused.status_code == 409 and "stop it first" in refused.json()["error"]
    call(data, "POST", f"/tasks/{waiting['id']}/cancel")
    page.get_by_test_id("activity-close").click()

    # Deleting the conversation that is open in the chat (from another device, say) clears the chat.
    page.get_by_test_id("chat-input").fill("Reply with just the word: ready")
    page.get_by_test_id("chat-input").press("Enter")
    page.locator('[data-testid="chat-jig"] .content:not(.typing)').wait_for(timeout=300_000)
    session = call(data, "GET", "/sessions").json()[0]
    assert call(data, "DELETE", f"/sessions/{session['id']}").status_code == 204
    page.get_by_test_id("chat-welcome").wait_for(timeout=10_000)
    assert page.locator('[data-testid="chat-user"]').count() == 0
    context.close()


def test_forget_everything_and_the_history_wording(server, browser, shots):
    data = server
    chat(data, "Reply with just the word: fine. (Code word: kittiwakemuffin.)")
    job = task_done(data, "Name an animal (ocelotgrammar)", "Reply with just the name of one animal, nothing else.")
    call(data, "POST", "/memory", json={"content": "Robyn likes the shagcormorant cafe"})

    context, page = signed_in(browser, data, "light", "#settings/history")
    older = page.get_by_test_id("audit-older")
    older.wait_for(state="visible")
    assert older.inner_text().startswith("2 older History entries, written by an earlier version of Jig up to 14/03/2026")
    assert "never hold what was said" in page.locator("#set-history .hint").first.inner_text()
    page.screenshot(path=str(shots / "history-wording-light.png"))

    page.goto(f"{BASE}/#settings/memory")
    page.locator('[data-testid="memory-wipe-details"] > summary').click()
    assert "every conversation, and every finished job" in page.get_by_test_id("memory-wipe-details").inner_text()
    page.get_by_test_id("memory-wipe").click()
    page.get_by_test_id("confirm-dialog").wait_for(state="visible")
    assert page.get_by_test_id("confirm-title").inner_text() == "Forget everything Jig keeps about you?"
    body = page.get_by_test_id("confirm-body").inner_text()
    for line in ("Every memory and note, every conversation, and every finished job with its results are deleted from "
                 "this computer, with the questions Jig asked you about them. Jig starts again knowing nothing about "
                 "you.",
                 "A job that is still going, or a reply Jig is writing right now, stays.",
                 "Your settings, rules, schedules and connected accounts stay, and so do files Jig made in its folder.",
                 "The History records that you did this and how many were deleted, never what they said.",
                 "2 older History entries"):
        assert line in body, line
    page.screenshot(path=str(shots / "forget-everything-confirm-light.png"))
    page.get_by_test_id("confirm-ok").click()
    page.wait_for_selector("#memory-saved:text-matches('^Jig deleted [0-9]+ memor.*, 1 conversation and [0-9]+ tasks?.$')")
    assert call(data, "GET", "/sessions").json() == [] and call(data, "GET", "/memory").json() == []
    assert call(data, "GET", f"/tasks/{job['id']}").status_code == 404
    on_disk = files(data)
    for word in (b"kittiwakemuffin", b"ocelotgrammar", b"shagcormorant"):
        assert word not in on_disk, f"{word!r} is still in the database files"
    page.screenshot(path=str(shots / "forget-everything-done-light.png"), full_page=True)
    last = call(data, "GET", "/audit", params={"kind": "everything", "newest_first": True}).json()[0]
    assert last["data"]["memories"] >= 1 and last["data"]["conversations"] == 1 and last["actor"] == "user"
    context.close()
