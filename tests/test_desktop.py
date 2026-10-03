"""Jig's own window (jig.desktop) and the tray's approval notifications, for real: a real `jig serve` with the
real model on a free port, the real window (WebView2 through pywebview), driven over DevTools. The window opens
without taking the focus and is closed straight after."""

from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

import httpx
import pytest

from jig import desktop

from .server_helpers import free_port, kill, start_jig, token, wait_health

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Jig's window is for Windows")


def test_webview2_versions():
    assert desktop.parse_version("154.0.4258.48") == "154.0.4258.48"
    for missing in ("", None, "0.0.0.0", "0"):
        assert desktop.parse_version(missing) is None


def test_only_web_pages_mail_and_jig_links_leave_the_window():
    for ok in ("https://accounts.google.com/o/oauth2/auth?x=1", "http://example.com/", "mailto:a@b.c", "jig://start"):
        assert desktop.external_url(ok) == ok
    for refused in ("file:///C:/Windows/notepad.exe", "ftp://example.com/x", "javascript:alert(1)", "ms-settings:",
                    "https:///no-host", "C:\\Windows\\System32\\cmd.exe"):
        assert desktop.external_url(refused) is None
        assert desktop.open_external(refused) is False


def test_the_window_comes_back_where_it_was_unless_that_is_off_screen(tmp_path):
    state = tmp_path / "window.json"
    screens = [(0, 0, 1920, 1080), (1920, 0, 2560, 1440)]
    assert desktop.load_geometry(state, screens) == {"width": 1200, "height": 860}
    desktop.save_state(state, pid=1, hwnd=2)
    desktop.save_state(state, geometry={"x": 2000, "y": 100, "width": 900, "height": 700, "maximized": True})
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["pid"] == 1 and saved["hwnd"] == 2, "saving the geometry keeps the rest"
    assert desktop.load_geometry(state, screens) == {"x": 2000, "y": 100, "width": 900, "height": 700,
                                                     "maximized": True}
    assert "x" not in desktop.load_geometry(state, [(0, 0, 1920, 1080)]), "that screen was unplugged"
    desktop.save_state(state, geometry={"x": 10, "y": 10, "width": 50, "height": 50})
    assert desktop.load_geometry(state, screens)["width"] == desktop.MIN_SIZE[0]


def test_one_window_name_per_data_folder(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    assert desktop.window_name(a).startswith("JigWindow-") and desktop.window_name(a) != desktop.window_name(b)
    assert desktop.window_name(a)[len("JigWindow-"):] == desktop.instance_name(a)[len("JigTray-"):]


@windows_only
def test_webview2_is_found_on_this_computer():
    if desktop.webview2_version() is None:
        pytest.skip("WebView2 isn't installed here")
    assert desktop.webview2_version().split(".")[0].isdigit()


def _window_rect(hwnd: int) -> tuple[int, int, int, int]:
    user32 = ctypes.WinDLL("user32")
    user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    rect = wintypes.RECT()
    user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
    scale = user32.GetDpiForWindow(wintypes.HWND(hwnd)) / 96
    return (round(rect.left / scale), round(rect.top / scale), round((rect.right - rect.left) / scale),
            round((rect.bottom - rect.top) / scale))


def _wait_for(fn, what: str, timeout: float = 60.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if value := fn():
            return value
        time.sleep(0.3)
    raise AssertionError(f"timed out waiting for {what}")


def _open_window(data: Path, port: int, debug_port: int) -> subprocess.Popen[bytes]:
    return subprocess.Popen([sys.executable, "-m", "jig.desktop", "--data-dir", str(data), "--port", str(port),
                             "--no-activate", "--debug-port", str(debug_port)])


def _shown(state: Path) -> dict | None:
    try:
        saved = json.loads(state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return saved if saved.get("hwnd") else None


@windows_only
def test_jigs_window_for_real(tmp_path):
    if desktop.webview2_version() is None:
        pytest.skip("WebView2 isn't installed here")
    sync_api = pytest.importorskip("playwright.sync_api", reason="pip install playwright")
    data = tmp_path / "data"
    port, debug_port = free_port(), free_port()
    proc, log = start_jig(data, port, env={"JIG_SANDBOX_DIR": str(tmp_path / "sandbox")})
    window = None
    try:
        wait_health(port, proc=proc, log=log)
        state = data / desktop.GEOMETRY_FILE
        window = _open_window(data, port, debug_port)
        first = _wait_for(lambda: _shown(state), "the window")
        with sync_api.sync_playwright() as p:
            browser = _wait_for(lambda: _cdp(p, debug_port), "DevTools in the window")
            page = browser.contexts[0].pages[0]
            # Signed in with a one-time code: no token asked for.
            page.wait_for_selector("#app:not([hidden])", timeout=60_000)
            assert page.url == f"http://127.0.0.1:{port}/?app=desktop", "the code was dropped from the address"
            # The handler is installed as the document is created. Wait, rather than assuming the load event
            # has already finished evaluating it.
            page.wait_for_function("() => window.__jigWindowLinks === true", timeout=15_000)
            assert page.locator("#open-browser").is_visible()
            assert page.locator("#logout").is_hidden(), "signing out would only sign the window back in"
            assert page.locator("#login").evaluate("d => d.open") is False

            # Links that may not leave the window are stopped, and nothing loads in the window instead.
            page.evaluate("""() => { const a = document.createElement('a'); a.id = 'probe'; a.textContent = 'x';
                                     a.href = 'file:///C:/Windows/notepad.exe'; document.body.append(a); }""")
            page.click("#probe")
            time.sleep(1)
            assert page.url == f"http://127.0.0.1:{port}/?app=desktop"
            assert page.evaluate("window.pywebview.api.open_external('ftp://example.com/x')") is False

            # The session ends while the window is open: the window signs in again by itself.
            page.evaluate("fetch('/auth/logout', {method: 'POST', credentials: 'same-origin'})")
            page.reload()
            page.wait_for_selector("#app:not([hidden])", timeout=60_000)
            assert page.evaluate("fetch('/auth/session').then(r => r.json()).then(s => s.authenticated)")
            browser.close()

        # Opening Jig again brings this window forward instead of opening another.
        again = subprocess.run([sys.executable, "-m", "jig.desktop", "--data-dir", str(data), "--port", str(port),
                                "--no-activate"], timeout=60)
        assert again.returncode == 0
        assert _shown(state)["hwnd"] == first["hwnd"] and window.poll() is None
        assert "already open; brought it to the front" in (data / "logs" / "window.log").read_text(encoding="utf-8")

        # It remembers where it was.
        user32 = ctypes.WinDLL("user32")
        user32.SetWindowPos(wintypes.HWND(first["hwnd"]), None, 140, 90, 1000, 740, 0x14)  # no z-order, no focus
        time.sleep(1)
        expected = _window_rect(first["hwnd"])
        closed = subprocess.run([sys.executable, "-m", "jig.desktop", "--data-dir", str(data), "--close"], timeout=60)
        assert closed.returncode == 0
        window.wait(30)
        saved = json.loads(state.read_text(encoding="utf-8"))
        assert saved["hwnd"] is None
        geometry = saved["geometry"]
        assert (geometry["x"], geometry["y"], geometry["width"], geometry["height"]) == expected
        window = _open_window(data, port, free_port())
        reopened = _wait_for(lambda: _shown(state), "the window again")
        assert _window_rect(reopened["hwnd"]) == expected
        assert subprocess.run([sys.executable, "-m", "jig.desktop", "--data-dir", str(data), "--close"],
                              timeout=60).returncode == 0
        window.wait(30)
    finally:
        if window is not None and window.poll() is None:
            window.kill()
        kill(proc)


def _cdp(p, port: int):
    try:
        return p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    except Exception:  # not listening yet
        return None


@windows_only
def test_the_tray_says_when_a_job_waits_for_an_ok(tmp_path, caplog):
    """A real job asks for an OK (the real model calls a tool that needs one); the tray notices it once."""
    from jig.tray import Tray

    data = tmp_path / "data"
    port = free_port()
    proc, log = start_jig(data, port, env={"JIG_SANDBOX_DIR": str(tmp_path / "sandbox")})
    try:
        wait_health(port, proc=proc, log=log)
        base = f"http://127.0.0.1:{port}"
        h = token(data)
        task = httpx.post(f"{base}/tasks", headers=h, timeout=30, json={
            "title": "Set up the tech headlines schedule", "mode": "action",
            "description": "Use the schedule_create tool to set up a schedule named 'Tech headlines' that runs "
                           "every weekday at 08:00 and summarises the BBC News technology headlines."}).json()
        approval = _wait_for(lambda: next((a for a in httpx.get(f"{base}/approvals?status=pending", headers=h,
                                                                 timeout=30).json()
                                           if a["task_id"] == task["id"]), None), "the approval", timeout=300)
        tray = Tray(None, data, port, "127.0.0.1")
        with caplog.at_level("INFO", logger="jig.tray"):
            tray._check_approvals()
            tray._check_approvals()
        said = [r.getMessage() for r in caplog.records if r.name == "jig.tray"]
        assert said == ["Jig needs your OK: A job is waiting for your OK before carrying on. Click here to open "
                        "Jig."], "once, and without saying what the job wants to do"
        assert approval["id"] in tray.waiting_approvals
        httpx.post(f"{base}/approvals/{approval['id']}", headers=h, timeout=30, json={"approve": False})
        httpx.post(f"{base}/tasks/{task['id']}/cancel", headers=h, timeout=30)
        tray._check_approvals()
        assert approval["id"] not in tray.waiting_approvals
    finally:
        kill(proc)
