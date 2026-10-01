"""One Jig per data directory, and a way to ask a running Jig to stop gracefully.

* ``<data_dir>/jig.lock`` is held with an OS lock (``msvcrt.locking`` on Windows, ``flock`` elsewhere)
  for as long as the runtime is open. The OS drops it when the process exits, even after a crash, so a
  stale file never blocks a restart. ``<data_dir>/instance.json`` says who holds it.
* ``jig stop`` asks the holder to shut down gracefully: on Windows by setting a named event
  (``Local\\Jig-stop-<hash of the data directory>``), elsewhere with SIGTERM.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import IO, Any

from .db import now_iso
from .errors import JigError

LOCK_FILENAME = "jig.lock"
INFO_FILENAME = "instance.json"
EXIT_INSTANCE_LOCKED = 75  # EX_TEMPFAIL: another Jig already uses this data directory


class InstanceLocked(JigError):
    """Another Jig process already uses this data directory."""


def _try_lock(fh: IO[bytes]) -> bool:
    try:
        if sys.platform == "win32":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(fh: IO[bytes]) -> None:
    if sys.platform == "win32":
        import msvcrt

        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def read_info(data_dir: Path) -> dict[str, Any] | None:
    try:
        return json.loads((Path(data_dir) / INFO_FILENAME).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


class InstanceLock:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / LOCK_FILENAME
        self.info_path = self.data_dir / INFO_FILENAME
        self._fh: IO[bytes] | None = None

    def acquire(self, **info: Any) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+b")  # noqa: SIM115 - held open for the lifetime of the runtime
        if not _try_lock(fh):
            fh.close()
            holder = read_info(self.data_dir) or {}
            who = (f"pid {holder.get('pid')}, started {holder.get('started_at')} ({holder.get('start_reason')})"
                   if holder else "unknown process")
            raise InstanceLocked(f"another Jig ({who}) is already using the data directory {self.data_dir}. "
                                 "Two runtimes must never share one; stop it first with 'jig stop'.")
        self._fh = fh
        self.info_path.write_text(json.dumps({"pid": os.getpid(), "started_at": now_iso(), **info}, indent=2),
                                  encoding="utf-8")

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        info = read_info(self.data_dir)
        if info and info.get("pid") == os.getpid():
            self.info_path.unlink(missing_ok=True)
        _unlock(fh)
        fh.close()

    @property
    def held(self) -> bool:
        return self._fh is not None


def running_instance(data_dir: Path) -> dict[str, Any] | None:
    """The live holder of the lock for ``data_dir`` (from instance.json), or None if nobody holds it."""
    path = Path(data_dir) / LOCK_FILENAME
    if not path.exists():
        return None
    with open(path, "a+b") as fh:
        if _try_lock(fh):
            _unlock(fh)
            return None
    return read_info(data_dir) or {"pid": None}


def stop_event_name(data_dir: Path) -> str:
    digest = hashlib.sha256(str(Path(data_dir).resolve()).lower().encode()).hexdigest()[:16]
    return f"Local\\Jig-stop-{digest}"


def request_stop(data_dir: Path) -> dict[str, Any]:
    """Ask the Jig using ``data_dir`` to shut down gracefully. Raises if none is running."""
    info = running_instance(data_dir)
    if info is None:
        raise JigError(f"no Jig is running for the data directory {data_dir}")
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenEventW.restype = wintypes.HANDLE
        kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        handle = kernel32.OpenEventW(0x0002, False, stop_event_name(data_dir))  # EVENT_MODIFY_STATE
        if not handle:
            raise JigError(f"Jig (pid {info.get('pid')}) holds {data_dir} but has no stop event (Windows error "
                           f"{ctypes.get_last_error()}); it may still be starting, or be an older version")
        try:
            if not kernel32.SetEvent(handle):
                raise JigError(f"SetEvent failed (Windows error {ctypes.get_last_error()})")
        finally:
            kernel32.CloseHandle(handle)
    else:
        import signal

        if not info.get("pid"):
            raise JigError(f"a Jig holds {data_dir} but {INFO_FILENAME} does not name its pid")
        os.kill(int(info["pid"]), signal.SIGTERM)
    return info
