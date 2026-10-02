"""Headless Chromium controlled by Jig over stdin/stdout (one JSON request per line, one JSON reply per line).

Runs inside the sandbox container. All browser traffic goes through the egress proxy given in
JIG_BROWSER_PROXY. Two guards stop the page from sending data on its own:

* every non-GET/HEAD/OPTIONS request (form posts, fetch/XHR writes, beacons) is aborted unless a
  ``submit`` or ``login`` command, which needs the user's approval, is running;
* ``submit`` events and ``form.submit()`` are cancelled outside those commands, so GET forms cannot be
  sent by a click or an Enter key either.

Values filled by ``login`` are remembered and redacted from every later reply.

Jig never types payment details: ``type``, ``fill`` and ``login`` refuse any field that is for a card
number, expiry, security code, cardholder name or bank account (by its autocomplete hint, name, id,
label or placeholder). ``inspect_submit`` describes, read-only, what a form or button would submit
(the site, the button, the fields' kinds, amounts and lines on the page, payment-provider frames), so
Jig can recognise a checkout, payment or booking and show it to the user before asking.
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
COMMANDS = {"open", "read", "screenshot", "click", "type", "fill", "submit", "login", "inspect_submit"}

# Describes a field without its value. Shared by the payment-field guard and inspect_submit.
FIELD_JS = """
(f) => {
  const labels = f.labels ? Array.from(f.labels).map((l) => l.innerText || '') : [];
  return {
    tag: f.tagName.toLowerCase(), type: (f.getAttribute('type') || '').toLowerCase(),
    name: f.getAttribute('name') || '', id: f.id || '',
    autocomplete: (f.getAttribute('autocomplete') || '').toLowerCase(),
    label: (labels.join(' ') || f.getAttribute('aria-label') || '').trim().slice(0, 80),
    placeholder: (f.getAttribute('placeholder') || '').slice(0, 80),
  };
}
"""

INSPECT_JS = """
(el) => {
  const describe = %s;
  const form = el.tagName === 'FORM' ? el : (el.form || el.closest('form'));
  let button = el;
  if (el.tagName === 'FORM') {
    button = el.querySelector('button[type=submit], input[type=submit], button:not([type])') || el;
  }
  const meta = (n) => (document.querySelector(`meta[property="${n}"], meta[name="${n}"]`) || {}).content || '';
  const fields = form ? Array.from(form.elements).filter((f) => f.tagName !== 'BUTTON' && f.type !== 'submit'
    && f.type !== 'hidden').slice(0, 40).map(describe) : [];
  const frames = Array.from(document.querySelectorAll('iframe[src]')).map((f) => {
    try { return new URL(f.src, location.href).host; } catch (e) { return ''; }
  }).filter(Boolean);
  const heads = Array.from(document.querySelectorAll('h1, h2, h3')).map((h) => (h.innerText || '').trim())
    .filter(Boolean).slice(0, 12);
  return {
    url: location.href, host: location.host, title: document.title,
    site_name: meta('og:site_name') || meta('application-name'),
    button: ((button.innerText || button.value || button.getAttribute('aria-label') || '') + '').trim().slice(0, 120),
    form_action: form ? (form.getAttribute('action') || '') : '', form_method: form ? (form.method || 'get') : '',
    is_form: Boolean(form), fields, frames, headings: heads,
    text: (document.body ? document.body.innerText : '').slice(0, 20000),
  };
}
""" % FIELD_JS.strip()

_PAYMENT_AUTOCOMPLETE = ("cc-", "transaction-amount")
_PAYMENT_WORDS = ("card", "cvv", "cvc", "csc", "security code", "securitycode", "expir", "expmonth", "expyear",
                  "exp-month", "exp-year", "iban", "sort code", "sortcode", "account number", "accountnumber",
                  "routing")


def payment_field(info: dict) -> bool:
    """True for a field meant for payment details (it must never be typed into by Jig)."""
    if any(info.get("autocomplete", "").startswith(p) or f" {p}" in info.get("autocomplete", "")
           for p in _PAYMENT_AUTOCOMPLETE):
        return True
    text = " ".join(str(info.get(k, "")) for k in ("name", "id", "label", "placeholder")).lower()
    return any(w in text for w in _PAYMENT_WORDS)

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

    def _refuse_payment_field(self, selector: str) -> None:
        loc = self.page.locator(selector).first
        info = loc.evaluate(FIELD_JS)
        if payment_field(info):
            what = info.get("label") or info.get("name") or info.get("id") or info.get("autocomplete")
            raise CommandError(f"refused: {what!r} is a payment field (card or bank details). Jig never types "
                               "payment details; the user enters them in their own browser.")

    def click(self, selector: str) -> dict:
        return self._guarded(lambda: self.page.click(selector))

    def type(self, selector: str, text: str, press_enter: bool = False) -> dict:
        self._refuse_payment_field(selector)

        def action() -> None:
            self.page.locator(selector).first.press_sequentially(text, delay=20)
            if press_enter:
                self.page.locator(selector).first.press("Enter")
        return self._guarded(action)

    def fill(self, selector: str, value: str) -> dict:
        self._refuse_payment_field(selector)
        return self._guarded(lambda: self.page.fill(selector, value))

    def inspect_submit(self, selector: str) -> dict:
        """What submitting this form or clicking this button would send, read-only, without any values."""
        info = self.page.locator(selector).first.evaluate(INSPECT_JS)
        for field in info["fields"]:
            field["payment"] = payment_field(field)
        return info

    def submit(self, selector: str) -> dict:
        loc = self.page.locator(selector).first
        is_form = loc.evaluate("el => el.tagName === 'FORM'")
        self._allow_writes(lambda: loc.evaluate("f => f.requestSubmit()") if is_form else loc.click())
        return self._state(submitted=True)

    def login(self, username_selector: str, username: str, password_selector: str, password: str,
              submit_selector: str) -> dict:
        self._refuse_payment_field(username_selector)
        self._refuse_payment_field(password_selector)
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
