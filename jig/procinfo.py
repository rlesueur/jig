"""Identify a process by its pid *and* start time, and handle one that is not our child.

Jig records the model server it launched (pid and start time) so that a later Jig can take it back
under supervision after "Turn Jig off" left it running. A pid alone could have been reused by an
unrelated process, so a process is only treated as the same one if its start time matches too.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

_STILL_ACTIVE = 259
_SYNCHRONIZE = 0x00100000
_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _kernel32():
    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.WaitForSingleObject.restype = wintypes.DWORD
    k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    return k


def process_start_time(pid: int) -> int | None:
    """An opaque start time for a *running* process, or None if there is no such process (or it cannot
    be read). Windows: the creation FILETIME. Linux: the start time in clock ticks since boot."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        k = _kernel32()
        h = k.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return None
        try:
            code = wintypes.DWORD()
            if not k.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != _STILL_ACTIVE:
                return None
            times = [wintypes.FILETIME() for _ in range(4)]
            if not k.GetProcessTimes(h, *(ctypes.byref(t) for t in times)):
                return None
            return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        finally:
            k.CloseHandle(h)
    if sys.platform.startswith("linux"):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
        except OSError:
            return None
        fields = stat.rsplit(")", 1)[1].split()
        return None if fields[0] == "Z" else int(fields[19])  # field 22 of the full line: starttime
    return None


class AdoptedProcess:
    """A ``subprocess.Popen``-like handle for a process this Jig did not start itself (one an earlier Jig
    launched and left running). Supports what the supervisor uses: pid, poll, wait, terminate, kill."""

    def __init__(self, pid: int):
        self.pid = pid
        self.returncode: int | None = None
        self._handle = None
        if sys.platform == "win32":
            self._handle = _kernel32().OpenProcess(_SYNCHRONIZE | _PROCESS_TERMINATE |
                                                   _PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not self._handle:
                import ctypes

                raise OSError(f"cannot open process {pid} (Windows error {ctypes.get_last_error()})")

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            k = _kernel32()
            if k.WaitForSingleObject(self._handle, 0) == 0:
                code = wintypes.DWORD()
                k.GetExitCodeProcess(self._handle, ctypes.byref(code))
                self.returncode = int(code.value)
        else:
            try:
                os.kill(self.pid, 0)
            except ProcessLookupError:
                self.returncode = -1  # not our child, so its real exit code cannot be collected
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(f"pid {self.pid}", timeout or 0)
            time.sleep(0.2)
        return self.returncode  # type: ignore[return-value]

    def terminate(self) -> None:
        if sys.platform == "win32":
            _kernel32().TerminateProcess(self._handle, 1)  # what Popen.terminate does on Windows
        else:
            os.kill(self.pid, signal.SIGTERM)

    def kill(self) -> None:
        if sys.platform == "win32":
            _kernel32().TerminateProcess(self._handle, 1)
        else:
            os.kill(self.pid, signal.SIGKILL)

    def __del__(self) -> None:
        if self._handle:
            _kernel32().CloseHandle(self._handle)
            self._handle = None
