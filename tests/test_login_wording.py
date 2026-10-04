"""The sign-in page, in a real browser against a real jig serve. No model calls: the page is shown before sign-in."""

from __future__ import annotations

import httpx
import pytest

from jig.auth import SESSION_COOKIE

from .server_helpers import free_port, kill, start_jig, wait_health

HELP = ("To sign in, open Jig's window from the Start menu or from its icon by the clock, "
        "then choose Open in browser. Or run jig ui --print-url and open the link it prints.")
ENDED = "Your session has ended. Please sign in again."


@pytest.fixture(scope="module")
def jig(tmp_path_factory):
    root = tmp_path_factory.mktemp("login-wording")
    port = free_port()
    proc, log = start_jig(root / "data", port, env={"JIG_SANDBOX_DIR": str(root / "sandbox")})
    try:
        wait_health(port, proc=proc, log=log)
        yield f"http://127.0.0.1:{port}"
    finally:
        kill(proc)


@pytest.fixture(scope="module")
def browser():
    playwright = pytest.importorskip("playwright.sync_api", reason="pip install playwright")
    with playwright.sync_playwright() as p:
        launched = p.chromium.launch()
        try:
            yield launched
        finally:
            launched.close()


def _page(browser, base: str, cookie: str | None = None):
    context = browser.new_context()
    if cookie is not None:
        context.add_cookies([{"name": SESSION_COOKIE, "value": cookie, "url": base}])
    page = context.new_page()
    page.goto(base + "/")
    page.wait_for_selector("#login[open], dialog#login", timeout=30_000)
    page.wait_for_function("() => document.getElementById('login').open")
    return context, page


def test_a_first_visit_says_how_to_sign_in(jig, browser):
    context, page = _page(browser, jig)
    try:
        assert page.locator("[data-testid=login-help]").inner_text() == HELP
        assert page.locator("#login-error").inner_text() == ""
        assert ENDED not in page.locator("#login").inner_text()
    finally:
        context.close()


def test_a_dead_session_cookie_says_the_session_ended(jig, browser):
    context, page = _page(browser, jig, cookie="not-a-real-session")
    try:
        assert page.locator("#login-error").inner_text() == ENDED
        assert page.locator("[data-testid=login-help]").inner_text() == HELP
    finally:
        context.close()


def test_the_session_flag_matches_the_page(jig):
    with httpx.Client(base_url=jig, timeout=10) as client:
        assert client.get("/auth/session").json()["prior_session"] is False
        client.cookies.set(SESSION_COOKIE, "not-a-real-session")
        assert client.get("/auth/session").json()["prior_session"] is True
