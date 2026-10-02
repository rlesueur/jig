"""Running the server for always-on use: graceful stop signals and rotating file logs.

Graceful shutdown means uvicorn's lifespan shutdown runs, so ``Jig.stop()`` stops the heartbeat,
checkpoints and re-queues running tasks, and releases the data-directory lock. It is triggered by:

* Ctrl+C, or SIGTERM (what systemd and launchd send);
* ``jig stop``, which sets a named Windows event (see ``jig.instance``);
* Windows logoff or shutdown. A process without a console (``pythonw.exe``, as Task Scheduler runs
  Jig) gets no console control events, so Jig creates a hidden top-level window and handles
  ``WM_ENDSESSION``, holding the session open for up to ``ENDSESSION_WAIT_S`` while it shuts down.

A hard kill (``schtasks /End``, Task Manager, power loss) skips all of this; the next start then
re-queues interrupted tasks from their checkpoints instead.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .instance import stop_event_name
from .logs import log_uncaught_exceptions, protect_logging

log = logging.getLogger(__name__)

LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 5
ENDSESSION_WAIT_S = 20.0
WINDOW_CLASS = "JigLifecycleWindow"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class _LogWriter:
    """Stands in for stdout/stderr when there is no console, so prints and tracebacks reach the log."""

    def __init__(self, logger: logging.Logger, level: int):
        self.logger, self.level, self._buf = logger, level, ""

    def write(self, text: str) -> int:
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self.logger.log(self.level, line)
        return len(text)

    def flush(self) -> None:
        if self._buf.strip():
            self.logger.log(self.level, self._buf)
        self._buf = ""

    def isatty(self) -> bool:
        return False


def configure_file_logging(log_path: Path, *, capture_std: bool = True) -> logging.Handler:
    """Log to a rotating file (5 MB x 5). With ``capture_std``, stdout and stderr go there too."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(log_path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    protect_logging()
    if capture_std:
        sys.stdout = _LogWriter(logging.getLogger("jig.stdout"), logging.INFO)  # type: ignore[assignment]
        sys.stderr = _LogWriter(logging.getLogger("jig.stderr"), logging.ERROR)  # type: ignore[assignment]
        log_uncaught_exceptions()
    return handler


class StopSignals:
    """Watches the named stop event (when ``data_dir`` is given) and Windows session-end messages, and
    calls ``request_exit`` for each. ``close()`` marks the shutdown as finished, which releases a
    session-end message that is waiting for it."""

    def __init__(self, request_exit: Callable[[str], None], data_dir: Path | None):
        self.request_exit = request_exit
        self.data_dir = data_dir
        self.stopped = threading.Event()
        self._event_handle: Any = None
        self._hwnd: Any = None

    def start(self) -> None:
        if sys.platform != "win32":
            return
        ready = threading.Event()
        threading.Thread(target=self._window_loop, args=(ready,), name="jig-session-end", daemon=True).start()
        if not ready.wait(10):
            raise OSError("the session-end window did not start")
        if self.data_dir is not None:
            self._watch_stop_event(self.data_dir)

    def _watch_stop_event(self, data_dir: Path) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateEventW.restype = wintypes.HANDLE
        kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        handle = kernel32.CreateEventW(None, True, False, stop_event_name(data_dir))
        if not handle:
            raise OSError(f"CreateEventW failed (Windows error {ctypes.get_last_error()})")
        self._event_handle = handle

        def wait_event() -> None:
            while not self.stopped.is_set():
                if kernel32.WaitForSingleObject(handle, 500) == 0:  # WAIT_OBJECT_0
                    self.request_exit("'jig stop'")
                    return

        threading.Thread(target=wait_event, name="jig-stop-event", daemon=True).start()

    def _window_loop(self, ready: threading.Event) -> None:
        import ctypes
        import os
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                        ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

        user32.DefWindowProcW.restype = LRESULT
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                           wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        WM_QUERYENDSESSION, WM_ENDSESSION, WM_CLOSE = 0x0011, 0x0016, 0x0010

        def proc(hwnd: Any, msg: int, wparam: int, lparam: int) -> int:
            if msg == WM_QUERYENDSESSION:
                return 1  # never veto a logoff or shutdown
            if msg == WM_ENDSESSION:
                if wparam:
                    kind = "logoff" if lparam & 0x80000000 else "shutdown or restart"
                    self.request_exit(f"Windows session ending ({kind})")
                    # Windows ends the process as soon as this returns, so wait for the lifespan shutdown.
                    if not self.stopped.wait(ENDSESSION_WAIT_S):
                        log.error("graceful shutdown did not finish within %.0fs of the session ending",
                                  ENDSESSION_WAIT_S)
                return 0
            if msg == WM_CLOSE:
                self.request_exit("WM_CLOSE")
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._proc = WNDPROC(proc)  # keep a reference for the lifetime of the window
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW(lpfnWndProc=self._proc, hInstance=hinst, lpszClassName=WINDOW_CLASS)
        user32.RegisterClassW(ctypes.byref(wc))
        # A hidden top-level window (not message-only), because only those receive the session-end broadcast.
        self._hwnd = user32.CreateWindowExW(0, WINDOW_CLASS, f"Jig {os.getpid()}", 0, 0, 0, 0, 0, None, None,
                                            hinst, None)
        ready.set()
        if not self._hwnd:
            log.error("could not create the session-end window (Windows error %d); logoff will not be graceful",
                      ctypes.get_last_error())
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def close(self) -> None:
        self.stopped.set()
        if sys.platform == "win32" and self._event_handle:
            import ctypes

            ctypes.WinDLL("kernel32").CloseHandle(self._event_handle)
            self._event_handle = None


def run_server(app: Any, *, host: str, port: int, data_dir: Path, log_to_file: bool, access_log: bool = True) -> int:
    """Run uvicorn with Jig's stop signals. Returns 0 after a clean stop. If start-up fails, uvicorn
    exits the process with code 3; the 1 below covers any start-up failure that returns instead."""
    import uvicorn

    # Open event streams (SSE, WebSockets) would otherwise hold a graceful shutdown open indefinitely.
    # proxy_headers=False: X-Forwarded-For/-Proto must never replace the real TCP peer, because remote access
    # checks that the connection itself belongs to tailscaled (jig.remote).
    # Without access_log (a person at a terminal), uvicorn logs through Jig's handlers: warnings on screen,
    # everything in the log file.
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info", lifespan="on",
                                           timeout_graceful_shutdown=10, proxy_headers=False, access_log=access_log,
                                           log_config=None if log_to_file or not access_log
                                           else uvicorn.config.LOGGING_CONFIG))
    # uvicorn.Config has just set up its loggers; no log line may carry a query string or an error's text.
    protect_logging()
    log_uncaught_exceptions()

    def request_exit(why: str) -> None:
        if not server.should_exit:
            log.info("graceful shutdown requested: %s", why)
        server.should_exit = True

    # POST /power/stop uses this to shut the server down after its response has been sent.
    app.state.request_exit = request_exit
    signals = StopSignals(request_exit, data_dir)
    signals.start()
    try:
        server.run()
    finally:
        signals.close()
    if not server.started:
        log.error("Jig did not start; see the errors above")
        return 1
    return 0
