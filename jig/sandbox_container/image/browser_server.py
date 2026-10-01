"""Headless Chromium controlled by Jig over stdin/stdout (one JSON request per line, one JSON reply per line).

Runs inside the sandbox container. All browser traffic goes through the egress proxy given in
JIG_BROWSER_PROXY. Two guards stop the page from sending data on its own:

* every non-GET/HEAD/OPTIONS request (form posts, fetch/XHR writes, beacons) is aborted unless a
  ``submit`` or ``login`` command, which needs the user's approval, is running;
* ``submit`` events and ``form.submit()`` are cancelled outside those commands, so GET forms cannot be
  sent by a click or an Enter key either.

Values filled by ``login`` are remembered and redacted from every later reply.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

WORKSPACE = Path("/workspace")
NAV_TIMEOUT_MS = 30_000
ACTION_TIMEOUT_MS = 10_000
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
COMMANDS = {"open", "read", "screenshot", "click", "type", "fill", "submit", "login"}

SUBMIT_GUARD_JS = """
(() => {
  const state = { allow: false, blocked: 0 };
  Object.defineProperty(window, '__jigSubmitGuard', { value: state, writable: false, configurable: false });
  document.addEventListener('submit', (e) => {
    if (!state.allow) { e.preventDefault(); e.stopImmediatePropagation(); state.blocked += 1; }
  }, true);
  const original = HTMLFormElement.prototype.submit;
  HTMLFormElement.prototype.submit = function () {
    if (state.allow) { return original.call(this); }
    state.blocked += 1;
  };
})();
"""


class CommandError(Exception):
    pass


class Browser:
    def __init__(self) -> None:
        proxy = os.environ.get("JIG_BROWSER_PROXY")
        if not proxy:
            raise CommandError("JIG_BROWSER_PROXY is not set; refusing to start a browser without the egress proxy")
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=True,
            proxy={"server": proxy},
            args=["--disable-dev-shm-usage", "--disable-background-networking", "--no-pings"],
        )
        self._context = self._browser.new_context(
            service_workers="block", accept_downloads=False, locale="en-GB",
            viewport={"width": 1280, "height": 800},
        )
        self._context.set_default_timeout(ACTION_TIMEOUT_MS)
        self._context.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        self._context.add_init_script(SUBMIT_GUARD_JS)
        self._context.route("**/*", self._route)
        self.page = self._context.new_page()
        self.allow_writes = False
        self.blocked_writes: list[str] = []
        self.secrets: set[str] = set()

    def _route(self, route, request) -> None:
        if request.method.upper() not in SAFE_METHODS and not self.allow_writes:
            self.blocked_writes.append(f"{request.method} {request.url[:200]}")
            route.abort("blockedbyclient")
            return
        route.continue_()

    # Helpers ---------------------------------------------------------------
    def _settle(self) -> None:
        try:
            self.page.wait_for_load_state("load", timeout=ACTION_TIMEOUT_MS)
        except PlaywrightTimeout:
            pass

    def _blocked_submits(self) -> int:
        try:
            return int(self.page.evaluate("() => (window.__jigSubmitGuard || {blocked: 0}).blocked"))
        except PlaywrightError:
            return 0

    def _state(self, **extra) -> dict:
        info = {"url": self.page.url, "title": self.page.title(), **extra}
        if self.blocked_writes:
            info["blocked_requests"] = self.blocked_writes[-10:]
            self.blocked_writes.clear()
        return info

    def _page_text(self, max_chars: int) -> dict:
        text = self.page.inner_text("body") if self.page.query_selector("body") else ""
        text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        links = self.page.eval_on_selector_all(
            "a[href]",
            "els => els.slice(0, 40).map(a => ({text: (a.innerText || '').trim().slice(0, 80), href: a.href}))",
        )
        return {"text": text[:max_chars], "truncated": len(text) > max_chars,
                "links": [link for link in links if link["text"]][:25]}

    def _guarded(self, action) -> dict:
        before = self._blocked_submits()
        action()
        self._settle()
        blocked = self._blocked_submits() - before
        extra = {}
        if blocked > 0:
            extra["blocked_form_submissions"] = blocked
            extra["note"] = ("A form submission was blocked. Submitting forms needs the user's approval: "
                             "use browser_submit (or browser_login for sign-in forms).")
        return self._state(**extra)

    def _allow_writes(self, action) -> None:
        self.allow_writes = True
        try:
            self.page.evaluate("() => { window.__jigSubmitGuard.allow = true; }")
            with self.page.expect_navigation(wait_until="load", timeout=NAV_TIMEOUT_MS):
                action()
        except PlaywrightTimeout:
            pass  # single-page apps may not navigate; the state returned below shows what happened
        finally:
            try:
                self.page.evaluate("() => { if (window.__jigSubmitGuard) window.__jigSubmitGuard.allow = false; }")
            except PlaywrightError:
                pass
            self.allow_writes = False

    # Commands --------------------------------------------------------------
    def open(self, url: str, max_chars: int = 6000) -> dict:
        response = self.page.goto(url, wait_until="load")
        status = response.status if response else None
        return self._state(status=status, **self._page_text(max_chars))

    def read(self, format: str = "text", max_chars: int = 12000) -> dict:
        if format == "snapshot":
            snap = self.page.locator("body").aria_snapshot()
            return self._state(snapshot=snap[:max_chars], truncated=len(snap) > max_chars)
        return self._state(**self._page_text(max_chars))

    def screenshot(self, path: str, full_page: bool = False) -> dict:
        target = (WORKSPACE / path).resolve()
        if not target.is_relative_to(WORKSPACE / "screenshots"):
            raise CommandError("screenshots must be saved under screenshots/")
        target.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(target), full_page=full_page)
        return self._state(path=path, bytes=target.stat().st_size)

    def click(self, selector: str) -> dict:
        return self._guarded(lambda: self.page.click(selector))

    def type(self, selector: str, text: str, press_enter: bool = False) -> dict:
        def action() -> None:
            self.page.locator(selector).first.press_sequentially(text, delay=20)
            if press_enter:
                self.page.locator(selector).first.press("Enter")
        return self._guarded(action)

    def fill(self, selector: str, value: str) -> dict:
        return self._guarded(lambda: self.page.fill(selector, value))

    def submit(self, selector: str) -> dict:
        loc = self.page.locator(selector).first
        is_form = loc.evaluate("el => el.tagName === 'FORM'")
        self._allow_writes(lambda: loc.evaluate("f => f.requestSubmit()") if is_form else loc.click())
        return self._state(submitted=True)

    def login(self, username_selector: str, username: str, password_selector: str, password: str,
              submit_selector: str) -> dict:
        self.secrets.update(v for v in (username, password) if v)
        self.page.fill(username_selector, username)
        self.page.fill(password_selector, password)
        self._allow_writes(lambda: self.page.click(submit_selector))
        return self._state(submitted=True)

    def redact(self, value):
        if isinstance(value, str):
            for s in self.secrets:
                value = value.replace(s, "[REDACTED]")
            return value
        if isinstance(value, dict):
            return {k: self.redact(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.redact(v) for v in value]
        return value


def main() -> None:
    out = sys.stdout
    sys.stdout = sys.stderr  # nothing but replies may reach the protocol stream
    browser = Browser()
    out.write(json.dumps({"ok": True, "ready": True}) + "\n")
    out.flush()
    for line in sys.stdin:
        if not line.strip():
            continue
        started = time.monotonic()
        try:
            req = json.loads(line)
            command = req.pop("command")
            if command not in COMMANDS:
                raise CommandError(f"unknown command {command!r}")
            result = getattr(browser, command)(**req)
            reply = {"ok": True, "result": browser.redact(result)}
        except (CommandError, PlaywrightError, TypeError, ValueError, KeyError) as exc:
            reply = {"ok": False, "error": browser.redact(f"{type(exc).__name__}: {exc}"),
                     "error_type": type(exc).__name__}
        except Exception as exc:  # report, never swallow
            traceback.print_exc()
            reply = {"ok": False, "error": browser.redact(f"{type(exc).__name__}: {exc}"), "error_type": "internal"}
        reply["elapsed_s"] = round(time.monotonic() - started, 2)
        out.write(json.dumps(reply, default=str) + "\n")
        out.flush()


if __name__ == "__main__":
    main()
