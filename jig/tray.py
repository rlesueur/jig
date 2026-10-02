"""Jig's Windows tray icon: ``pythonw -m jig.tray --config ... --data-dir ... --port ...``.

The desktop install runs Jig through this instead of a console window. It starts ``jig serve`` (with no
console) for its data folder, shows whether Jig is running, and has Open Jig (Jig's own window, see
``jig.desktop``), Open in browser, Turn Jig off or on, and Quit. One tray runs per data folder: starting
another one (from the Start menu, the ``jig://start`` link on the "Jig is off" page, or autostart) asks the
running tray to start Jig, or open it, instead.

While Jig runs, a Windows notification says when a job is waiting for the person's OK. It says only that
(not what the job wants to do), so nothing private shows on the lock screen; clicking it opens Jig.

It only ever starts and stops the Jig for its own data folder, and turns it off the same way the web UI
does (``POST /power/stop``), so Jig finishes and saves what it was doing.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import logging
import os
import subprocess
import sys
import threading
import time
import webbrowser
from ctypes import wintypes
from pathlib import Path
from typing import Any

import httpx

from .config import load_config
from .instance import running_instance

log = logging.getLogger("jig.tray")

ICON = Path(__file__).parent / "web" / "favicon.ico"
POLL_S = 3.0

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_NULL = 0x0000
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B
WM_APP = 0x8000
WM_TRAY = WM_APP + 1  # mouse events on the icon
WM_STATUS = WM_APP + 2  # the status thread has news
WM_START = WM_APP + 3  # another tray instance asks: start Jig
WM_OPEN = WM_APP + 4  # another tray instance asks: start Jig and open it
NIN_BALLOONUSERCLICK = 0x0405  # the person clicked a notification
ASFW_ANY = -1

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_INFO, NIIF_WARNING = 0x1, 0x2
MF_STRING, MF_GRAYED, MF_SEPARATOR = 0x0, 0x1, 0x800
TPM_RIGHTBUTTON, TPM_BOTTOMALIGN, TPM_RETURNCMD = 0x2, 0x20, 0x100
IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x10, 0x40
ERROR_ALREADY_EXISTS = 183
CREATE_NO_WINDOW = 0x08000000

CMD_OPEN, CMD_ON, CMD_OFF, CMD_QUIT, CMD_BROWSER = 1, 2, 3, 4, 5

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD), ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HICON)]


def _win32() -> tuple[Any, Any, Any]:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    user32.RegisterClassExW.restype = wintypes.ATOM
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                       wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                  wintypes.UINT]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.CreatePopupMenu.restype = wintypes.HMENU
    user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
    user32.SetMenuDefaultItem.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.UINT]
    user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                      wintypes.HWND, wintypes.LPVOID]
    user32.DestroyMenu.argtypes = [wintypes.HMENU]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = LRESULT
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterWindowMessageW.restype = wintypes.UINT
    user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    return user32, shell32, kernel32


def instance_name(data_dir: Path) -> str:
    """The mutex and window-class name of the tray for this data folder."""
    key = os.path.normcase(str(data_dir.resolve()))
    return "JigTray-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


class Tray:
    def __init__(self, config_path: str | None, data_dir: Path, port: int, host: str) -> None:
        self.config_path = config_path
        self.data_dir = data_dir
        self.port = port
        self.base = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::', '') else host}:{port}"
        self.child: subprocess.Popen[bytes] | None = None
        self.stopping = False  # we asked Jig to turn off
        self.quitting = False
        self.open_when_up = False
        self.waiting_approvals: set[str] = set()  # pending approvals already notified
        self.state = "starting"  # starting | running | setup | stopping | off | problem
        self.problem = ""
        self.lock = threading.Lock()
        self.hwnd = None
        self.user32, self.shell32, self.kernel32 = _win32()
        self._wndproc = WNDPROC(self._on_message)  # keep a reference: Windows calls it
        self._taskbar_created = self.user32.RegisterWindowMessageW("TaskbarCreated")

    # ----- Jig itself -----

    def _token(self) -> str:
        return (self.data_dir / "api-token").read_text(encoding="utf-8").strip()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token()}"}

    def _answering(self) -> dict[str, Any] | None:
        """Jig's /status if this data folder's Jig answers, else None."""
        try:
            if httpx.get(f"{self.base}/health", timeout=2).status_code != 200:
                return None
            r = httpx.get(f"{self.base}/status", headers=self._headers(), timeout=5)
        except (httpx.HTTPError, OSError):
            return None
        return r.json() if r.status_code == 200 else None

    def start_jig(self) -> None:
        if self.child is not None and self.child.poll() is None:
            return
        if running_instance(self.data_dir):
            return  # already running (started some other way); the status thread picks it up
        cmd = [sys.executable, "-m", "jig.cli"]
        if self.config_path:
            cmd += ["--config", self.config_path]
        cmd += ["serve", "--data-dir", str(self.data_dir), "--port", str(self.port), "--no-browser", "--log-file",
                "--start-reason", self.start_reason]
        log.info("starting Jig: %s", cmd)
        self.stopping = False
        self.problem = ""
        self._set_state("starting")
        self.child = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
        self.start_reason = "manual"  # a later Turn Jig on is the user's

    def stop_jig(self) -> bool:
        """Turn Jig off the way the web UI does. Returns False (and says why) if Jig didn't accept."""
        try:
            r = httpx.post(f"{self.base}/power/stop", headers=self._headers(),
                           json={"scope": "jig", "confirm": True}, timeout=15)
        except (httpx.HTTPError, OSError) as exc:
            self._notify("Jig didn't turn off", f"Jig didn't answer: {exc}", warning=True)
            return False
        if r.status_code != 202:
            self._notify("Jig didn't turn off", f"Jig said no (error {r.status_code}): {r.text[:200]}", warning=True)
            return False
        self.stopping = True
        self._set_state("stopping")
        return True

    def open_jig(self) -> None:
        """Open Jig's window (it signs itself in), or bring it to the front. Waits for Jig if it's starting."""
        status = self._answering()
        if status is None:
            self.open_when_up = True
            if self.state in ("off", "problem"):
                self.start_jig()
            return
        from .desktop import open_window

        open_window(self.config_path, self.data_dir, self.port)

    def open_in_browser(self) -> None:
        """Open the web UI in the default browser, signed in, as 'jig ui --browser' does."""
        if self._answering() is None:
            self._notify("Jig isn't running", "Turn Jig on first, then open it in your browser.", warning=True)
            return
        try:
            r = httpx.post(f"{self.base}/auth/login-code", headers=self._headers(), timeout=10)
            r.raise_for_status()
        except (httpx.HTTPError, OSError) as exc:
            self._notify("Jig couldn't open", f"Jig didn't make a sign-in link: {exc}", warning=True)
            return
        webbrowser.open(f"{self.base}/#code={r.json()['code']}")

    def _check_approvals(self) -> None:
        """Notify once for each job that starts waiting for the person's OK."""
        try:
            r = httpx.get(f"{self.base}/approvals", params={"status": "pending"}, headers=self._headers(), timeout=5)
        except (httpx.HTTPError, OSError):
            return
        if r.status_code != 200:
            return
        pending = {a["id"] for a in r.json()}
        new = pending - self.waiting_approvals
        self.waiting_approvals = pending
        if new:
            many = len(pending) > 1
            self._notify("Jig needs your OK", (f"{len(pending)} jobs are" if many else "A job is") +
                         " waiting for your OK before carrying on. Click here to open Jig.")

    # ----- status -----

    def _watch(self) -> None:
        while not self.quitting:
            status = self._answering()
            exited = self.child is not None and self.child.poll() is not None
            with self.lock:
                if status is not None:
                    new = "setup" if status.get("status") == "setup" else "running"
                elif self.stopping and (self.child is None or exited):
                    new = "off"
                elif exited:
                    code = self.child.returncode
                    if self.stopping or code == 0:
                        new = "off"
                    else:
                        new = "problem"
                        self.problem = (f"Jig stopped with an error (exit code {code}). Its log is "
                                        f"{self.data_dir / 'logs' / 'jig.log'}.")
                    self.child = None
                elif self.state == "starting" or (self.child is not None and not exited):
                    new = "stopping" if self.stopping else "starting"
                else:
                    new = "off"
            if new != self.state:
                previous, self.state = self.state, new
                self.user32.PostMessageW(self.hwnd, WM_STATUS, 0, 0)
                if new == "problem":
                    self._notify("Jig stopped", self.problem, warning=True)
                elif new == "setup" and previous == "starting":
                    self._notify("Jig needs a model", "Open Jig to choose one. It stays off until a model passes "
                                 "its checks.")
            if new in ("running", "setup") and self.open_when_up:
                self.open_when_up = False
                self.open_jig()
            if new == "running":
                self._check_approvals()
            if new == "off" and self.quitting:
                break
            time.sleep(POLL_S)

    def _set_state(self, state: str) -> None:
        with self.lock:
            self.state = state
        if self.hwnd:
            self.user32.PostMessageW(self.hwnd, WM_STATUS, 0, 0)

    def status_text(self) -> str:
        return {
            "starting": "Jig is starting\u2026",
            "running": "Jig is running",
            "setup": "Jig needs a model: open it to set one up",
            "stopping": "Jig is turning off\u2026",
            "off": "Jig is off",
            "problem": "Jig stopped with a problem",
        }[self.state]

    # ----- the icon -----

    def _nid(self) -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        return nid

    def _add_icon(self) -> None:
        nid = self._nid()
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self.icon
        nid.szTip = self.status_text()
        self.shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))

    def _update_tip(self) -> None:
        nid = self._nid()
        nid.uFlags = NIF_TIP
        nid.szTip = self.status_text()
        self.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def _notify(self, title: str, text: str, warning: bool = False) -> None:
        log.info("%s: %s", title, text)
        if not self.hwnd:
            return
        nid = self._nid()
        nid.uFlags = NIF_INFO
        nid.szInfoTitle = title[:63]
        nid.szInfo = text[:255]
        nid.dwInfoFlags = NIIF_WARNING if warning else NIIF_INFO
        self.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def _menu(self) -> None:
        u = self.user32
        menu = u.CreatePopupMenu()
        running = self.state in ("running", "setup")
        u.AppendMenuW(menu, MF_STRING | MF_GRAYED, 0, self.status_text())
        u.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        u.AppendMenuW(menu, MF_STRING, CMD_OPEN, "Open Jig")
        u.AppendMenuW(menu, MF_STRING | (0 if running else MF_GRAYED), CMD_BROWSER, "Open in browser")
        if running:
            u.AppendMenuW(menu, MF_STRING, CMD_OFF, "Turn Jig off")
        else:
            u.AppendMenuW(menu, MF_STRING | (MF_GRAYED if self.state in ("starting", "stopping") else 0), CMD_ON,
                          "Turn Jig on")
        u.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        u.AppendMenuW(menu, MF_STRING, CMD_QUIT, "Quit (turns Jig off)" if running else "Quit")
        u.SetMenuDefaultItem(menu, CMD_OPEN, 0)
        pt = wintypes.POINT()
        u.GetCursorPos(ctypes.byref(pt))
        u.SetForegroundWindow(self.hwnd)
        cmd = u.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_BOTTOMALIGN | TPM_RETURNCMD, pt.x, pt.y, 0, self.hwnd,
                               None)
        u.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        u.DestroyMenu(menu)
        self._command(cmd)

    def _command(self, cmd: int) -> None:
        if cmd == CMD_OPEN:
            threading.Thread(target=self.open_jig, daemon=True).start()
        elif cmd == CMD_BROWSER:
            threading.Thread(target=self.open_in_browser, daemon=True).start()
        elif cmd == CMD_ON:
            self.start_jig()
        elif cmd == CMD_OFF:
            threading.Thread(target=self.stop_jig, daemon=True).start()
        elif cmd == CMD_QUIT:
            threading.Thread(target=self.quit, daemon=True).start()

    def quit(self) -> None:
        if self.state in ("running", "setup") and not self.stop_jig():
            return
        self.quitting = True
        deadline = time.monotonic() + 120
        while self.child is not None and self.child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.5)
        self.user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

    def _on_message(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        if msg == WM_TRAY:
            event = lparam & 0xFFFF
            if event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                self._menu()
            elif event in (WM_LBUTTONDBLCLK, NIN_BALLOONUSERCLICK):
                threading.Thread(target=self.open_jig, daemon=True).start()
            return 0
        if msg == WM_STATUS:
            self._update_tip()
            return 0
        if msg == WM_COMMAND:
            self._command(wparam & 0xFFFF)
            return 0
        if msg == WM_START:
            if self.state in ("off", "problem"):
                self.start_jig()
            return 0
        if msg == WM_OPEN:
            threading.Thread(target=self.open_jig, daemon=True).start()
            return 0
        if msg == self._taskbar_created and msg:
            self._add_icon()  # Explorer restarted
            return 0
        if msg == WM_CLOSE:
            self.user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            nid = self._nid()
            self.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            self.user32.PostQuitMessage(0)
            return 0
        return self.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def run(self, class_name: str, *, start: bool, open_ui: bool, start_reason: str) -> int:
        u = self.user32
        hinst = self.kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.lpszClassName = class_name
        if not u.RegisterClassExW(ctypes.byref(wc)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.hwnd = u.CreateWindowExW(0, class_name, "Jig", 0, 0, 0, 0, 0, None, None, hinst, None)
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self.icon = u.LoadImageW(None, str(ICON), IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
        self.start_reason = start_reason
        if running_instance(self.data_dir):
            self.state = "running"
        elif start:
            self.start_jig()
        else:
            self.state = "off"
        self._add_icon()
        log.info("tray ready for %s at %s (%s)", self.data_dir, self.base, self.state)
        self.open_when_up = open_ui
        threading.Thread(target=self._watch, daemon=True, name="jig-tray-status").start()
        msg = wintypes.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))
        return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m jig.tray", description="Jig's Windows tray icon.")
    p.add_argument("--config", help="Jig's config file (default: as 'jig' finds it)")
    p.add_argument("--data-dir", help="data folder (default: from the config)")
    p.add_argument("--port", type=int, help="port (default: from the config)")
    p.add_argument("--open", action="store_true", help="open Jig's window once it's running")
    p.add_argument("--no-start", action="store_true", help="show the icon without starting Jig")
    p.add_argument("--start-reason", choices=["manual", "autostart"], default="manual")
    p.add_argument("--quit", action="store_true",
                   help="ask the running tray to turn Jig off and quit (for the uninstaller)")
    p.add_argument("link", nargs="?", help="a jig:// link: jig://start, or jig://open to open Jig too")
    args = p.parse_args(argv)
    if args.link and args.link.lower().startswith("jig://open"):
        args.open = True
    if sys.platform != "win32":
        print("The tray icon is for Windows. Elsewhere, run: jig serve", file=sys.stderr)
        return 2

    config = load_config(args.config, **({"data_dir": args.data_dir} if args.data_dir else {}))
    data_dir = config.data_dir
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=data_dir / "logs" / "tray.log", level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # it polls every few seconds
    name = instance_name(data_dir)
    user32, _, kernel32 = _win32()
    if args.quit:
        hwnd = user32.FindWindowW(name, None)
        if not hwnd:
            return 0
        user32.PostMessageW(hwnd, WM_COMMAND, CMD_QUIT, 0)
        deadline = time.monotonic() + 150
        while user32.FindWindowW(name, None) and time.monotonic() < deadline:
            time.sleep(0.5)
        return 0 if not user32.FindWindowW(name, None) else 1
    ctypes.set_last_error(0)
    mutex = kernel32.CreateMutexW(None, False, f"Local\\{name}")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        hwnd = user32.FindWindowW(name, None)
        if hwnd:
            user32.AllowSetForegroundWindow(ASFW_ANY)  # so Jig's window may come to the front for the person
            user32.PostMessageW(hwnd, WM_OPEN if args.open else WM_START, 0, 0)
            log.info("a tray already runs for %s; asked it to %s", data_dir, "open Jig" if args.open else "start Jig")
            return 0
        log.error("a tray already runs for %s but its window wasn't found", data_dir)
        return 1
    from .desktop import APP_ID

    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)  # notifications come from "Jig"
    tray = Tray(args.config, data_dir, args.port or config.server.port, config.server.host)
    try:
        return tray.run(name, start=not args.no_start, open_ui=args.open, start_reason=args.start_reason)
    finally:
        kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    sys.exit(main())
