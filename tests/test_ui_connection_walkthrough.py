"""The guided set-up in Settings > Connections, in a real browser against a real `jig serve` and the providers' real
services: one step at a time, Next only once a step is done, and each check answered by the provider itself (a
made-up Google client file, a real public Discord app, matrix.org, a signal-cli that isn't there, GitHub's real
device sign-in). Screenshots of every walkthrough go to JIG_UI_SCREENSHOTS (default: the test's tmp dir)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from .server_helpers import free_port, kill, start_jig, token, wait_health

MADE_UP_GOOGLE = {"installed": {"client_id": "000000000000-jiguiwalk.apps.googleusercontent.com",
                                "client_secret": "GOCSPX-jiguiwalknotarealsecret000",
                                "project_id": "jig-ui-walk", "redirect_uris": ["http://localhost"]}}
MIDJOURNEY_APP = "936929561302675456"
W = '[data-testid="walkthrough"]'


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("ui-walkthrough")
    data = root / "data"
    port = free_port()
    proc, log = start_jig(data, port, env={"JIG_SANDBOX_DIR": str(root / "sandbox")})
    try:
        wait_health(port, proc=proc, log=log)
        yield data, f"http://127.0.0.1:{port}"
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


def signed_in(browser, server, scheme: str = "light"):
    data, base = server
    code = httpx.post(f"{base}/auth/login-code", headers=token(data), timeout=10).json()["code"]
    context = browser.new_context(color_scheme=scheme, viewport={"width": 1100, "height": 1000},
                                  timezone_id="Europe/London", locale="en-GB")
    page = context.new_page()
    page.goto(f"{base}/#code={code}")
    page.wait_for_selector("#app:not([hidden])", timeout=60_000)
    return context, page


def open_walkthrough(page, base: str, provider: str):
    page.goto(f"{base}/#settings/connections")
    page.locator(f'[data-testid="connection-walkthrough-{provider}"]').click()
    page.wait_for_selector(f'{W}[data-provider="{provider}"]')


def step(page) -> str:
    return page.locator(W).get_attribute("data-step")


def snap(page, shots: Path, name: str):
    page.locator(W).screenshot(path=str(shots / f"{name}.png"))


def test_the_google_walkthrough_checks_each_step(server, browser, shots, tmp_path):
    data, base = server
    context, page = signed_in(browser, server)
    try:
        open_walkthrough(page, base, "gmail")
        assert page.locator("#connection-list").is_hidden()
        assert page.get_by_test_id("walkthrough-count").inner_text() == "Gmail \u00b7 step 1 of 9"
        assert page.get_by_role("progressbar").get_attribute("aria-valuenow") == "1"
        snap(page, shots, "gmail-1-intro")
        page.get_by_test_id("walkthrough-next").click()

        assert step(page) == "google-project"
        page.get_by_test_id("walkthrough-project").fill("Not A Project")
        assert "lower-case" in page.locator(".walk-result").inner_text()
        page.get_by_test_id("walkthrough-project").fill("jig-ui-walk")
        assert "my links will open in that project" in page.locator(".walk-result").inner_text()
        snap(page, shots, "gmail-2-project")
        page.get_by_test_id("walkthrough-next").click()

        assert step(page) == "google-apis"
        assert page.get_by_test_id("walkthrough-next").is_disabled()
        hrefs = [a.get_attribute("href") for a in page.get_by_test_id("walkthrough-link").all()]
        assert hrefs and all(h.startswith("https://console.cloud.google.com/") and "project=jig-ui-walk" in h
                             for h in hrefs), hrefs
        snap(page, shots, "gmail-3-apis")
        page.get_by_test_id("walkthrough-done").click()
        assert step(page) == "google-consent"
        snap(page, shots, "gmail-4-consent")
        page.get_by_test_id("walkthrough-done").click()
        assert step(page) == "google-publish"
        assert "In production" in page.locator(W).inner_text()
        snap(page, shots, "gmail-5-publish")
        page.get_by_test_id("walkthrough-done").click()

        assert step(page) == "google-client"
        made_up = tmp_path / "client_secret_made_up.json"
        made_up.write_text(json.dumps(MADE_UP_GOOGLE), encoding="utf-8")
        page.get_by_test_id("walkthrough-client-file").set_input_files(str(made_up))
        page.get_by_test_id("walkthrough-client-upload").click()
        result = page.get_by_test_id("walkthrough-result")
        result.wait_for(timeout=60_000)
        assert "doesn't recognise this app" in result.inner_text().replace("\u2019", "'")
        assert step(page) == "google-client" and page.get_by_test_id("walkthrough-next").is_disabled()
        assert MADE_UP_GOOGLE["installed"]["client_secret"] not in page.content()
        snap(page, shots, "gmail-6-client-not-recognised")

        page.get_by_test_id("walkthrough-back").click()
        assert step(page) == "google-publish" and not page.get_by_test_id("walkthrough-next").is_disabled()
        page.get_by_test_id("walkthrough-next").click()
        assert step(page) == "google-client"

        # The other two Google accounts share the app: their guide starts at signing in.
        open_walkthrough(page, base, "google-calendar")
        assert step(page) == "connect"
        assert "already set up" in page.locator(".walk-skip").inner_text()
        snap(page, shots, "google-calendar-1-connect")
        page.get_by_test_id("walkthrough-close").click()
        page.locator("#connection-list").wait_for(state="visible")
        assert page.get_by_test_id("connection-walkthrough").is_hidden()
    finally:
        httpx.delete(f"{base}/connections/google/client", headers=token(data), timeout=10)
        context.close()


def test_the_unverified_app_warning_comes_before_signing_in(server, browser, shots):
    data, base = server
    context, page = signed_in(browser, server, scheme="dark")
    try:
        page.goto(f"{base}/#settings/connections/google-drive")
        page.wait_for_selector(W)
        page.evaluate("""() => {
            const s = JSON.parse(sessionStorage.getItem('jig.walk.google-drive') || '{}');
            sessionStorage.setItem('jig.walk.google-drive', JSON.stringify({...s, index: 6}));
        }""")
        page.goto(f"{base}/#settings/connections")
        page.goto(f"{base}/#settings/connections/google-drive")
        page.wait_for_selector(f'{W}[data-step="google-warning"]')
        text = page.locator(W).inner_text().replace("\u2019", "'")
        assert "Google hasn't verified this app" in text and "Advanced" in text
        snap(page, shots, "google-drive-7-warning-dark")
    finally:
        context.close()


def test_discord_matrix_and_signal_checks_ask_the_real_services(server, browser, shots, tmp_path):
    data, base = server
    context, page = signed_in(browser, server)
    try:
        open_walkthrough(page, base, "discord")
        assert page.get_by_test_id("walkthrough-next").is_disabled()
        page.get_by_test_id("walkthrough-input-application_id").fill("100000000000000001")
        page.get_by_test_id("walkthrough-check").click()
        page.get_by_test_id("walkthrough-result").wait_for(timeout=30_000)
        assert "doesn't know an app" in page.get_by_test_id("walkthrough-result").inner_text().replace("\u2019", "'")
        page.get_by_test_id("walkthrough-input-application_id").fill(MIDJOURNEY_APP)
        page.get_by_test_id("walkthrough-check").click()
        page.wait_for_function("() => document.querySelector('.walk-result.ok')")
        assert "Midjourney" in page.get_by_test_id("walkthrough-result").inner_text()
        snap(page, shots, "discord-1-app-found")
        page.get_by_test_id("walkthrough-next").click()
        assert step(page) == "token" and page.get_by_test_id("walkthrough-next").is_disabled()
        snap(page, shots, "discord-2-token")

        open_walkthrough(page, base, "matrix")
        page.get_by_test_id("walkthrough-input-homeserver").fill("matrix.org")
        page.get_by_test_id("walkthrough-check").click()
        page.wait_for_function("() => document.querySelector('.walk-result.ok')", timeout=30_000)
        assert "https://" in page.get_by_test_id("walkthrough-result").inner_text()
        snap(page, shots, "matrix-1-server-found")
        page.get_by_test_id("walkthrough-next").click()
        assert page.get_by_test_id("walkthrough-input-homeserver").input_value().startswith("https://")
        snap(page, shots, "matrix-2-sign-in")

        open_walkthrough(page, base, "signal")
        page.get_by_test_id("walkthrough-signal-found").wait_for(timeout=30_000)
        page.locator(f"{W} details summary", has_text="I have my own signal-cli").click()
        page.get_by_test_id("walkthrough-input-signal_cli").fill(str(tmp_path / "signal-cli" / "bin" / "signal-cli.bat"))
        page.get_by_test_id("walkthrough-check").click()
        page.wait_for_function("() => document.querySelector('.walk-result.not-yet')", timeout=30_000)
        assert page.get_by_test_id("walkthrough-result").inner_text().startswith("signal-cli was not found")
        assert page.get_by_test_id("walkthrough-next").is_disabled()
        snap(page, shots, "signal-1-not-found")

        open_walkthrough(page, base, "slack")
        link = page.get_by_test_id("walkthrough-link").first.get_attribute("href")
        assert link.startswith("https://api.slack.com/apps?new_app=1&manifest_json=")
        assert json.loads(httpx.URL(link).params["manifest_json"])["display_information"]["name"] == "Jig"
        snap(page, shots, "slack-1-create")
    finally:
        context.close()


def test_github_shows_its_sign_in_code_big_and_microsoft_is_one_click(server, browser, shots):
    data, base = server
    context, page = signed_in(browser, server)
    try:
        open_walkthrough(page, base, "microsoft")
        assert step(page) == "connect" and page.get_by_test_id("walkthrough-connect").is_visible()
        snap(page, shots, "microsoft-1-connect")

        open_walkthrough(page, base, "github")
        assert step(page) == "install"
        assert page.get_by_test_id("walkthrough-link").first.get_attribute("href").startswith(
            "https://github.com/apps/")
        snap(page, shots, "github-1-install")
        page.get_by_test_id("walkthrough-done").click()
        assert step(page) == "connect"
        with context.expect_page() as opened:
            page.get_by_test_id("walkthrough-connect").click()
        # A browser that isn't signed in to GitHub is sent to its sign-in page first, then on to the device page.
        assert re.match(r"https://github\.com/login(/device|\?return_to=https%3A%2F%2Fgithub\.com%2Flogin%2Fdevice)",
                        opened.value.url), opened.value.url
        opened.value.close()
        code = page.get_by_test_id("walkthrough-user-code")
        code.wait_for(timeout=30_000)
        assert re.fullmatch(r"[A-Z0-9]{4}-[A-Z0-9]{4}", code.inner_text())
        assert page.get_by_test_id("walkthrough-copy-code").is_visible()
        snap(page, shots, "github-2-code")
    finally:
        context.close()


SIGNAL_TOOLS = os.environ.get("JIG_TEST_SIGNAL_TOOLS", "")


def own_jig(tmp_path, tools: str | None = None):
    """A Jig of its own; with ``tools``, its data folder's tools folder is that one (a junction)."""
    data = tmp_path / "data"
    data.mkdir(parents=True)
    if tools:
        subprocess.run(["cmd", "/c", "mklink", "/J", str(data / "tools"), tools], check=True, capture_output=True)
    port = free_port()
    # signal-cli keeps its accounts in XDG_DATA_HOME: a temporary folder, never the profile's own.
    proc, log = start_jig(data, port, env={"JIG_SANDBOX_DIR": str(tmp_path / "sandbox"),
                                           "XDG_DATA_HOME": str(tmp_path / "signal-cli-data")})
    wait_health(port, proc=proc, log=log)
    return proc, (data, f"http://127.0.0.1:{port}")


@pytest.mark.skipif(not SIGNAL_TOOLS or sys.platform != "win32",
                    reason="set JIG_TEST_SIGNAL_TOOLS to a folder with java/ and signal-cli/ (Windows)")
def test_signal_links_with_a_qr_code_shown_in_jig(browser, shots, tmp_path):
    """Jig finds its Java and signal-cli, runs signal-cli's link step and shows the link as a QR code with the
    steps for the phone. Never scanned: the test stops the link, and Jig offers a new code."""
    proc, server = own_jig(tmp_path, SIGNAL_TOOLS)
    data, base = server
    context, page = signed_in(browser, server)
    try:
        open_walkthrough(page, base, "signal")
        found = page.get_by_test_id("walkthrough-signal-found")
        found.wait_for(timeout=60_000)
        assert "Java 25" in found.inner_text() and "downloaded by Jig: ready" in found.inner_text()
        assert page.get_by_test_id("walkthrough-next").is_disabled()
        snap(page, shots, "signal-1-found")
        page.get_by_test_id("walkthrough-signal-use-found").click()
        page.wait_for_function("() => document.querySelector('.walk-result.ok')", timeout=60_000)
        assert page.get_by_test_id("walkthrough-result").inner_text().startswith("signal-cli runs (signal-cli 0.")
        page.get_by_test_id("walkthrough-next").click()
        assert step(page) == "link" and page.get_by_test_id("walkthrough-next").is_disabled()
        snap(page, shots, "signal-2-link")

        page.get_by_test_id("walkthrough-signal-link-start").click()
        qr = page.get_by_test_id("walkthrough-signal-qr")
        qr.wait_for(timeout=90_000)
        uri = httpx.get(f"{base}/connections/signal/setup", headers=token(data), timeout=30).json()["link"]["uri"]
        assert re.fullmatch(r"sgnl://linkdevice\?uuid=[^&]+&pub_key=\S+", uri)
        assert qr.get_attribute("src").startswith("data:image/svg+xml")
        assert page.evaluate("() => document.querySelector('[data-testid=walkthrough-signal-qr]').naturalWidth") > 0
        assert "Settings > Linked devices > Link new device" in page.locator(W).inner_text()
        snap(page, shots, "signal-3-qr")
        (shots / "signal-3-qr-uri.txt").write_text(uri, encoding="utf-8")

        page.get_by_test_id("walkthrough-signal-link-stop").click()
        page.get_by_test_id("walkthrough-signal-link-start").wait_for(timeout=30_000)
        assert page.get_by_test_id("walkthrough-signal-link-start").inner_text() == "Make a new code"
        assert httpx.get(f"{base}/connections/signal/setup", headers=token(data),
                         timeout=30).json()["link"] == {"status": "cancelled"}
        snap(page, shots, "signal-4-stopped")
    finally:
        context.close()
        kill(proc)


@pytest.mark.network
@pytest.mark.skipif(os.environ.get("JIG_TEST_SIGNAL_DOWNLOAD") != "1",
                    reason="set JIG_TEST_SIGNAL_DOWNLOAD=1 to download signal-cli and Java (about 180 MB)")
def test_signal_download_for_me_gets_signal_cli_ready(browser, shots, tmp_path):
    proc, server = own_jig(tmp_path)
    data, base = server
    context, page = signed_in(browser, server)
    try:
        open_walkthrough(page, base, "signal")
        page.get_by_test_id("walkthrough-signal-found").wait_for(timeout=60_000)
        snap(page, shots, "signal-0-nothing-yet")
        page.get_by_test_id("walkthrough-signal-download-start").click()
        progress = page.get_by_test_id("walkthrough-signal-download")
        progress.wait_for(timeout=30_000)
        page.wait_for_function("() => /Downloading .* \\d+ MB of \\d+ MB/.test(document.querySelector("
                               "'[data-testid=walkthrough-signal-download]')?.textContent || '')", timeout=60_000)
        snap(page, shots, "signal-0-downloading")
        page.wait_for_function("() => document.querySelector('.walk-result.ok')", timeout=900_000)
        assert page.get_by_test_id("walkthrough-result").inner_text().startswith("signal-cli runs (signal-cli 0.")
        assert "downloaded by Jig" in page.get_by_test_id("walkthrough-signal-found").inner_text()
        assert page.get_by_test_id("walkthrough-next").is_enabled()
        snap(page, shots, "signal-0-downloaded")
        kinds = [r["data"]["what"] for r in httpx.get(f"{base}/audit", params={"kind": "connector.signal_download"},
                                                      headers=token(data), timeout=30).json()]
        assert "signal-cli" in kinds
    finally:
        context.close()
        kill(proc)


def test_a_chat_link_opens_the_walkthrough_in_the_same_tab(server, browser):
    data, base = server
    context, page = signed_in(browser, server)
    try:
        page.goto(f"{base}/web/markdown.js")
        out = page.evaluate("""async () => {
            const { renderMarkdown } = await import('/web/markdown.js');
            const box = document.createElement('div');
            box.append(...renderMarkdown('Open [Connect Gmail](#settings/connections/gmail) or #settings/connections/slack.'));
            return [...box.querySelectorAll('a')].map((a) => [a.getAttribute('href'), a.target, a.textContent]);
        }""")
        assert out == [["#settings/connections/gmail", "", "Connect Gmail"],
                       ["#settings/connections/slack", "", "#settings/connections/slack"]]
    finally:
        context.close()
