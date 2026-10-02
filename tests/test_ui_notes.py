"""Settings > Memory and notes: Jig's notes listed, read, edited and deleted one at a time or all at once, and the
privacy wording, against a real `jig serve` (port 8794), the real model and a real browser. Screenshots go to
JIG_UI_SCREENSHOTS (default: the test's tmp dir)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx
import pytest

from jig.config import load_config
from jig.db import Database
from jig.store import Store

from .server_helpers import kill, start_jig, token, wait_health

PORT = 8794
BASE = f"http://127.0.0.1:{PORT}"
LONG_BODY = ("Compared three train operators for Leeds to York on weekday mornings. " * 6
             + "\n\nThe 07:42 is the quickest and the 08:10 the cheapest.")


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("ui-notes")
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
    return httpx.request(method, BASE + path, headers=token(data), timeout=120, **kw)


def write_notes(data: Path, *notes: tuple[str, str, str | None]) -> list[dict]:
    """Notes written straight into the running Jig's real database, as the agent's note_write does."""
    db = Database(load_config(data_dir=data).db_path)
    try:
        store = Store(db)
        return [store.add_note(title=t, body=b, task_id=task) for t, b, task in notes]
    finally:
        db.close()


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


def test_notes_in_settings(server, browser, shots):
    data = server
    short, long, _ = write_notes(data, ("Shopping", "Oat milk, bread and lemons", None),
                                 ("Train research", LONG_BODY, "t_research"),
                                 ("Gift ideas", "A rowing book for Kate", None))
    call(data, "POST", "/memory", json={"content": "Robyn takes her coffee black"})

    context, page = signed_in(browser, data, "light", "#settings/memory")
    page.wait_for_selector('#notes-heading:text-is("Jig\u2019s notes (3)")')
    assert page.get_by_test_id("settings-nav-memory").inner_text() == "Memory and notes"
    privacy = page.get_by_test_id("memory-privacy").inner_text()
    assert "stored only in its database on this computer" in privacy and "delete any of them, at any time" in privacy
    assert not page.get_by_test_id("memory-cloud-note").is_visible(), "a local model sends nothing anywhere"

    first = page.locator(f'[data-testid="note"][data-id="{short["id"]}"]')
    assert first.locator(".note-body").inner_text() == "Oat milk, bread and lemons"
    assert "from a conversation" in first.locator(".meta").inner_text()
    longer = page.locator(f'[data-testid="note"][data-id="{long["id"]}"]')
    assert "from job t_research" in longer.locator(".meta").inner_text()
    assert not longer.locator(".note-body").is_visible(), "a long note starts folded"
    longer.locator("summary").click()
    assert "The 07:42 is the quickest" in longer.locator(".note-body").inner_text()
    page.get_by_test_id("notes").scroll_into_view_if_needed()
    page.screenshot(path=str(shots / "notes-light.png"), full_page=True)

    first.get_by_role("button", name=f"Edit note {short['id']}", exact=True).click()
    page.get_by_label(f"Title of note #{short['id']}").fill("Shopping list")
    page.get_by_label(f"Text of note #{short['id']}").fill("Oat milk and lemons")
    page.screenshot(path=str(shots / "note-edit-light.png"), full_page=True)
    first.get_by_role("button", name="Save", exact=True).click()
    first.locator(".title:text-is('Shopping list')").wait_for()
    assert first.locator(".note-body").inner_text() == "Oat milk and lemons"
    assert "edited" in first.locator(".meta").inner_text()
    assert call(data, "GET", f"/notes/{short['id']}").json()["body"] == "Oat milk and lemons"

    page.once("dialog", lambda d: d.accept())
    longer.get_by_role("button", name=f"Delete note {long['id']}", exact=True).click()
    page.wait_for_selector(f'#memory-saved:text-is("Note #{long["id"]} is deleted.")')
    page.wait_for_selector('#notes-heading:text-is("Jig\u2019s notes (2)")')
    assert call(data, "GET", f"/notes/{long['id']}").status_code == 404

    page.locator('[data-testid="notes-wipe-details"] > summary').click()
    page.get_by_test_id("notes-wipe").click()
    page.get_by_test_id("confirm-dialog").wait_for(state="visible")
    assert page.get_by_test_id("confirm-title").inner_text() == "Delete all of Jig\u2019s notes?"
    body = page.get_by_test_id("confirm-body").inner_text()
    assert "What it remembers about you stays." in body and "can\u2019t be undone" in body
    page.screenshot(path=str(shots / "notes-wipe-confirm-light.png"))
    page.get_by_test_id("confirm-cancel").click()
    assert len(call(data, "GET", "/notes").json()) == 2, "cancel deletes nothing"
    page.get_by_test_id("notes-wipe").click()
    page.get_by_test_id("confirm-ok").click()
    page.wait_for_selector('#memory-saved:text-is("Deleted 2 notes.")')
    page.wait_for_selector('#notes-heading:text-is("Jig\u2019s notes (0)")')
    assert page.locator('[data-testid="notes"] .empty').inner_text() == "Jig hasn\u2019t written any notes yet."
    assert call(data, "GET", "/notes").json() == [] and len(call(data, "GET", "/memory").json()) == 1

    kinds = [(a["kind"], a["data"].get("note_id"), a["data"].get("count"))
             for a in call(data, "GET", "/audit", params={"kind": "note"}).json()]
    assert kinds == [("note.edited", short["id"], None), ("note.deleted", long["id"], None), ("note.wiped", None, 2)]
    context.close()


def test_agent_note_appears_live_and_forget_everything_clears_notes(server, browser, shots):
    data = server
    context, page = signed_in(browser, data, "light", "#settings/memory")
    page.wait_for_selector("#notes-heading:text-matches('^Jig\u2019s notes')")
    task = call(data, "POST", "/tasks", json={
        "title": "Write a note", "mode": "research",
        "description": "Use the note_write tool once to save a note titled 'Bin day' with the body "
                       "'Green bin goes out on Thursday.' Then reply with just: done"}).json()
    # The open page follows the note.changed event; nobody reloads it.
    page.locator('[data-testid="note"]', has_text="Bin day").wait_for(timeout=300_000)
    wait_until(lambda: call(data, "GET", f"/tasks/{task['id']}").json()["status"] in ("done", "failed"), "the task")
    note = call(data, "GET", "/notes").json()[0]
    assert note["title"] == "Bin day" and note["task_id"] == task["id"]
    tool_calls = [a for a in call(data, "GET", "/audit", params={"kind": "tool.call", "task_id": task["id"]}).json()
                  if a["data"]["tool"] == "note_write"]
    assert tool_calls and all("Thursday" not in str(a) for a in tool_calls), "the audit log keeps sizes, not text"

    call(data, "POST", "/memory", json={"content": "Robyn's cat is called Biscuit"})
    page.wait_for_selector("#memory-heading:text-matches('^Memories .[1-9]')")
    page.locator('[data-testid="memory-wipe-details"] > summary').click()
    page.get_by_test_id("memory-wipe").click()
    page.get_by_test_id("confirm-dialog").wait_for(state="visible")
    body = page.get_by_test_id("confirm-body").inner_text()
    assert ("Every memory and note, every conversation, and every finished job with its results are deleted from "
            "this computer") in body
    page.screenshot(path=str(shots / "forget-everything-confirm-light.png"))
    page.get_by_test_id("confirm-ok").click()
    page.wait_for_selector("#memory-saved:text-matches('^Jig deleted .*1 note and 1 task.$')")
    page.wait_for_selector('#notes-heading:text-is("Jig\u2019s notes (0)")')
    assert call(data, "GET", "/notes").json() == [] and call(data, "GET", "/memory").json() == []
    context.close()


def test_privacy_wording_and_dark_theme(server, browser, shots):
    data = server
    write_notes(data, ("Reading list", "Finish the book on the Humber bridge", None))
    context, page = signed_in(browser, data, "dark", "#settings/memory")
    page.get_by_test_id("note").first.wait_for()
    page.screenshot(path=str(shots / "notes-dark.png"), full_page=True)
    page.goto(f"{BASE}/#settings/chat")
    hint = page.locator("#read-only-hint")
    hint.wait_for(state="visible")
    text = hint.inner_text()
    assert "won't change, send or save anything of yours" in text
    assert "stored only in its database on this computer" in text and "view, change or delete them" in text
    assert not page.get_by_test_id("read-only-cloud").is_visible()
    page.screenshot(path=str(shots / "read-only-hint-dark.png"))
    context.close()
