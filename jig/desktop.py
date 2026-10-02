"""Jig's own window on Windows: ``pythonw -m jig.desktop --config ... --data-dir ... --port ...``.

Jig's web UI in an app window of its own (title "Jig", Jig's icon, its own taskbar button) instead of a
browser tab. pywebview draws it with Microsoft Edge WebView2, the browser engine that is part of Windows 11
(and that Edge installs on Windows 10), so nothing big is bundled. The window:

- signs in the way 'jig ui' does, with a one-time code, so there is no token to type, and signs in again by
  itself if its session ends while it is open;
- is one per data folder: opening Jig again brings the open window to the front;
- opens links to other sites (provider sign-in pages, docs) in the default browser, and jig:// links in the
  tray app, never inside the window, so sign-ins happen in the browser as before;
- remembers its size and position (window.json in the data folder);
- has "Open in browser" for anyone who wants the browser after all.

Without WebView2 it says so and lets the person choose: get WebView2, or open Jig in the browser this time.
It never falls back to pywebview's old Internet Explorer engine. Closing the window leaves Jig running (the
tray icon turns it off). Elsewhere than Windows, Jig opens in the browser.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from .config import load_config
from .tray import instance_name

log = logging.getLogger("jig.desktop")

ICON = Path(__file__).parent / "web" / "favicon.ico"
APP_ID = "Jig.App"  # the taskbar groups windows by this; the Start menu shortcut carries the same ID
WEBVIEW2_CLIENT = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"  # the Evergreen WebView2 Runtime
WEBVIEW2_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"
GEOMETRY_FILE = "window.json"
DEFAULT_SIZE = (1200, 860)
MIN_SIZE = (420, 520)
EXTERNAL_SCHEMES = {"http", "https", "mailto", "jig"}
SIGN_IN_GAP_S = 20.0  # at most one automatic sign-in this often, so a refused code can't loop

MB_YESNOCANCEL, MB_OK, MB_ICONWARNING, MB_ICONERROR, MB_SETFOREGROUND = 0x3, 0x0, 0x30, 0x10, 0x10000
IDYES, IDNO = 6, 7
SW_RESTORE = 9
WM_CLOSE = 0x0010
ERROR_ALREADY_EXISTS = 183

# Run in the page after each load. Links to other origins, and other schemes (jig://start on the "Jig is off"
# page), go to the default browser or their app through the window's API instead of loading in the window.
LINKS_JS = r"""
(() => {
  if (window.__jigWindowLinks) return;
  window.__jigWindowLinks = true;
  const outside = (href) => {
    try { return new URL(href, location.href).origin !== location.origin; } catch (e) { return false; }
  };
  const send = (href) => window.pywebview.api.open_external(new URL(href, location.href).href);
  document.addEventListener('click', (e) => {
    const a = e.target instanceof Element ? e.target.closest('a[href]') : null;
    if (!a || !outside(a.href) || e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    send(a.href);
  }, true);
  const open = window.open.bind(window);
  window.open = (url, ...rest) => {
    if (url && outside(String(url))) { send(String(url)); return null; }
    return open(url, ...rest);
  };
})();
"""


def window_name(data_dir: Path) -> str:
    """The mutex name of the window for this data folder."""
    return instance_name(data_dir).replace("JigTray-", "JigWindow-", 1)


def parse_version(value: Any) -> str | None:
    """A WebView2 'pv' registry value as a version, or None when it means "not installed"."""
    text = str(value or "").strip()
    if not text or all(part == "0" for part in text.split(".")):
        return None
    return text


def webview2_version() -> str | None:
    """The installed WebView2 Runtime's version, as Microsoft documents finding it, or None."""
    import winreg

    places = [(winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT}"),
              (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT}"),
              (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT}")]
    for hive, path in places:
        try:
            with winreg.OpenKey(hive, path) as key:
                version = parse_version(winreg.QueryValueEx(key, "pv")[0])
        except OSError:
            continue
        if version:
            return version
    return None


def external_url(url: str) -> str | None:
    """``url`` if the window may hand it to Windows (a web page, an email address or a jig:// link)."""
    parts = urlsplit(str(url))
    if parts.scheme.lower() not in EXTERNAL_SCHEMES:
        return None
    if parts.scheme.lower() in ("http", "https") and not parts.netloc:
        return None
    return str(url)


def open_external(url: str) -> bool:
    checked = external_url(url)
    if checked is None:
        log.warning("not opening a link with scheme %r", urlsplit(str(url)).scheme)
        return False
    if sys.platform == "win32":
        os.startfile(checked)  # noqa: S606 - the default browser, or the jig:// tray app
        return True
    return webbrowser.open(checked)


# ----- remembering where the window was -----

def load_geometry(path: Path, screens: list[tuple[int, int, int, int]]) -> dict[str, Any]:
    """Saved size and position, if the window would still be on a screen (x, y, width, height each)."""
    try:
        saved = json.loads(path.read_text(encoding="utf-8")).get("geometry") or {}
        x, y, w, h = (int(saved[k]) for k in ("x", "y", "width", "height"))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {"width": DEFAULT_SIZE[0], "height": DEFAULT_SIZE[1]}
    w, h = max(w, MIN_SIZE[0]), max(h, MIN_SIZE[1])
    out: dict[str, Any] = {"width": w, "height": h, "maximized": bool(saved.get("maximized"))}
    # At least a strip of the title bar must be on some screen, or the window opens where Windows chooses.
    if any(x + w - 80 > sx and x + 80 < sx + sw and sy <= y < sy + sh - 40 for sx, sy, sw, sh in screens):
        out |= {"x": x, "y": y}
    return out


def save_state(path: Path, **values: Any) -> None:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    state.update(values)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ----- Windows -----

def _user32() -> Any:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    return user32


def focus_existing(state_path: Path) -> bool:
    """Bring this data folder's open window to the front. False if it isn't open (or not yet shown)."""
    from ctypes import wintypes

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        hwnd, pid = int(state["hwnd"]), int(state["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    user32 = _user32()
    owner = wintypes.DWORD()
    if not user32.IsWindow(hwnd):
        return False
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
    if owner.value != pid:
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)
    return True


def close_existing(state_path: Path, timeout_s: float = 20.0) -> bool:
    """Close this data folder's open window (for the uninstaller). True once no window is open."""
    from ctypes import wintypes

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        hwnd, pid = int(state["hwnd"]), int(state["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return True
    user32 = _user32()
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    owner = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
    if not user32.IsWindow(hwnd) or owner.value != pid:
        return True
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not user32.IsWindow(hwnd):
            return True
        time.sleep(0.3)
    return False


def message(text: str, buttons: int = MB_OK | MB_ICONERROR) -> int:
    return _user32().MessageBoxW(None, text, "Jig", buttons | MB_SETFOREGROUND)


# ----- Jig -----

class Jig:
    def __init__(self, data_dir: Path, base: str):
        self.data_dir = data_dir
        self.base = base

    def answering(self) -> bool:
        try:
            return httpx.get(f"{self.base}/health", timeout=3).status_code == 200
        except (httpx.HTTPError, OSError):
            return False

    def login_code(self) -> str:
        token = (self.data_dir / "api-token").read_text(encoding="utf-8").strip()
        r = httpx.post(f"{self.base}/auth/login-code", headers={"Authorization": f"Bearer {token}"}, timeout=10)
        r.raise_for_status()
        return r.json()["code"]

    def window_url(self) -> str:
        # ?app=desktop tells the page it is in Jig's window; it survives reloads, unlike the one-time code.
        return f"{self.base}/?app=desktop#code={self.login_code()}"

    def browser_url(self) -> str:
        return f"{self.base}/#code={self.login_code()}"


class WindowApi:
    """What the page in the window can ask for (window.pywebview.api)."""

    def __init__(self, jig: Jig):
        self._jig = jig
        self._last_sign_in = 0.0

    def open_external(self, url: str) -> bool:
        return open_external(url)

    def open_in_browser(self) -> bool:
        return webbrowser.open(self._jig.browser_url())

    def login_code(self) -> str | None:
        """The session ended while the window was open: a fresh one-time code for the page to sign in with."""
        if time.monotonic() - self._last_sign_in < SIGN_IN_GAP_S:
            return None
        self._last_sign_in = time.monotonic()
        return self._jig.login_code()


def _set_app_id() -> None:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)


def run_window(jig: Jig, state_path: Path, *, activate: bool = True, debug_port: int | None = None) -> int:
    import webview
    from webview import settings as webview_settings

    webview_settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    webview_settings["ALLOW_DOWNLOADS"] = True
    webview_settings["SHOW_DEFAULT_MENUS"] = True
    if debug_port:
        webview_settings["REMOTE_DEBUGGING_PORT"] = debug_port

    import webview.platforms.winforms as winforms  # picks the engine when imported

    if winforms.renderer != "edgechromium":
        message(f"Jig's window couldn't use Microsoft Edge WebView2 (pywebview chose {winforms.renderer!r}). "
                f"Open Jig in your browser instead: run jig ui --browser.")
        return 1

    screens = [(s.x, s.y, s.width, s.height) for s in webview.screens]
    geometry = load_geometry(state_path, screens)
    api = WindowApi(jig)
    window = webview.create_window(
        "Jig", jig.window_url(), js_api=api, width=geometry["width"], height=geometry["height"],
        x=geometry.get("x"), y=geometry.get("y"), maximized=geometry.get("maximized", False), min_size=MIN_SIZE,
        text_select=True, zoomable=True, focus=activate, background_color="#FBFAF7")
    current = dict(geometry)

    def on_moved(x: int, y: int) -> None:
        if not current.get("maximized"):
            current.update(x=x, y=y)

    def on_resized(width: int, height: int) -> None:
        if not current.get("maximized"):
            current.update(width=width, height=height)

    def on_shown() -> None:
        hwnd = int(window.native.Handle.ToInt64())
        save_state(state_path, pid=os.getpid(), hwnd=hwnd)
        log.info("window open (pid %s)", os.getpid())

    def on_loaded() -> None:
        # pywebview turns the right-click menu (copy, paste) off outside its debug mode; people need it.
        from System import Func, Type  # pythonnet, loaded by pywebview

        def menus() -> None:
            window.native.browser.webview.CoreWebView2.Settings.AreDefaultContextMenusEnabled = True

        window.native.Invoke(Func[Type](menus))
        window.evaluate_js(LINKS_JS)

    def on_closing() -> None:
        save_state(state_path, geometry={k: current[k] for k in ("x", "y", "width", "height") if k in current}
                   | {"maximized": bool(current.get("maximized"))}, pid=None, hwnd=None)

    window.events.moved += on_moved
    window.events.resized += on_resized
    window.events.maximized += lambda: current.update(maximized=True)
    window.events.restored += lambda: current.update(maximized=False)
    window.events.shown += on_shown
    window.events.loaded += on_loaded
    window.events.closing += on_closing
    webview.start(gui="edgechromium", icon=str(ICON), private_mode=True,
                  storage_path=str(state_path.parent / "window-webview"))
    return 0


def offer_browser_instead(jig: Jig) -> int:
    """No WebView2: say so, and let the person choose. Nothing happens without their choice."""
    answer = message("Jig's window needs Microsoft Edge WebView2, which isn't on this computer. It's free from "
                     "Microsoft and comes with Windows 11.\n\n"
                     "Yes: get WebView2 now (opens Microsoft's page in your browser), then open Jig again.\n"
                     "No: open Jig in your browser this time.\n"
                     "Cancel: do nothing.", MB_YESNOCANCEL | MB_ICONWARNING)
    if answer == IDYES:
        webbrowser.open(WEBVIEW2_URL)
    elif answer == IDNO:
        webbrowser.open(jig.browser_url())
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m jig.desktop", description="Jig in its own window (Windows).")
    p.add_argument("--config", help="Jig's config file (default: as 'jig' finds it)")
    p.add_argument("--data-dir", help="data folder (default: from the config)")
    p.add_argument("--port", type=int, help="port (default: from the config)")
    p.add_argument("--browser", action="store_true", help="open Jig in the default browser instead")
    p.add_argument("--no-activate", action="store_true", help="show the window without bringing it to the front")
    p.add_argument("--debug-port", type=int, help="let DevTools connect to the window on this port (diagnostics)")
    p.add_argument("--close", action="store_true", help="close the open window (for the uninstaller)")
    args = p.parse_args(argv)

    config = load_config(args.config, **({"data_dir": args.data_dir} if args.data_dir else {}))
    data_dir = config.data_dir
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=data_dir / "logs" / "window.log", level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("pywebview").setLevel(logging.WARNING)
    host = config.server.host
    jig = Jig(data_dir, f"http://{'127.0.0.1' if host in ('0.0.0.0', '::', '') else host}:"
                        f"{args.port or config.server.port}")
    if args.browser or sys.platform != "win32":
        if not jig.answering():
            print(f"Jig isn't running at {jig.base}. Start it with: jig serve", file=sys.stderr)
            return 1
        return 0 if webbrowser.open(jig.browser_url()) else 1

    state_path = data_dir / GEOMETRY_FILE
    if args.close:
        return 0 if close_existing(state_path) else 1
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    ctypes.set_last_error(0)
    mutex = kernel32.CreateMutexW(None, False, f"Local\\{window_name(data_dir)}")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        focused = focus_existing(state_path)
        log.info("Jig's window is already open%s", "; brought it to the front" if focused else " (still opening)")
        return 0
    try:
        if not jig.answering():
            message(f"Jig isn't running, so its window can't open. Start Jig from the Start menu (Jig), or turn "
                    f"it on from its tray icon. (Nothing answered at {jig.base}.)")
            return 1
        if webview2_version() is None:
            log.warning("WebView2 isn't installed; asked whether to get it or use the browser")
            return offer_browser_instead(jig)
        _set_app_id()
        return run_window(jig, state_path, activate=not args.no_activate, debug_port=args.debug_port)
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(mutex))


def open_window(config_path: str | None, data_dir: Path, port: int, *, browser: bool = False) -> None:
    """Open (or bring to the front) Jig's window from another process, without a console."""
    import subprocess

    exe = Path(sys.executable)
    gui = exe.with_name("pythonw.exe") if exe.name.lower() == "python.exe" else exe
    cmd = [str(gui if gui.is_file() else exe), "-m", "jig.desktop"]
    if config_path:
        cmd += ["--config", str(config_path)]
    cmd += ["--data-dir", str(data_dir), "--port", str(port)]
    if browser:
        cmd.append("--browser")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if sys.platform == "win32":
        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY: the new window may come to the front
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=flags, close_fds=True)


if __name__ == "__main__":
    sys.exit(main())
