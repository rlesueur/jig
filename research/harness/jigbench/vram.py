"""Peak GPU memory of the model-server processes serving a trial, sampled in the background.

nvidia-smi cannot attribute memory to processes under Windows WDDM (it reports "[N/A]"), so this reads the
Windows performance counter `\\GPU Process Memory(*)\\Dedicated Usage` through PDH, per process ID. A trial's
figure is the peak, over samples taken while it ran, of the summed dedicated memory of the distinct server
processes behind its endpoints (one process counted once when the agent and Sentinel share it).
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from typing import Any
from urllib.parse import urlparse

import psutil

COUNTER = r"\GPU Process Memory(*)\Dedicated Usage"
_PDH_FMT_LARGE = 0x00000400
_PDH_MORE_DATA = 0x800007D2


class VramError(RuntimeError):
    pass


class _Value(ctypes.Structure):
    _fields_ = [("CStatus", wintypes.DWORD), ("largeValue", ctypes.c_longlong)]


class _Item(ctypes.Structure):
    _fields_ = [("szName", wintypes.LPWSTR), ("FmtValue", _Value)]


class DedicatedCounter:
    """An open PDH query on the per-process dedicated GPU memory counter."""

    def __init__(self) -> None:
        self._pdh = ctypes.WinDLL("pdh")
        self._query = wintypes.HANDLE()
        self._counter = wintypes.HANDLE()
        if rc := self._pdh.PdhOpenQueryW(None, None, ctypes.byref(self._query)):
            raise VramError(f"PdhOpenQuery failed: 0x{rc & 0xFFFFFFFF:08X}")
        if rc := self._pdh.PdhAddEnglishCounterW(self._query, COUNTER, None, ctypes.byref(self._counter)):
            self.close()
            raise VramError(f"PdhAddEnglishCounter({COUNTER}) failed: 0x{rc & 0xFFFFFFFF:08X}")

    def by_pid(self) -> dict[int, int]:
        """Dedicated GPU memory in MiB per process ID (summed over that process's adapters)."""
        if rc := self._pdh.PdhCollectQueryData(self._query):
            raise VramError(f"PdhCollectQueryData failed: 0x{rc & 0xFFFFFFFF:08X}")
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        rc = self._pdh.PdhGetFormattedCounterArrayW(self._counter, _PDH_FMT_LARGE, ctypes.byref(size),
                                                    ctypes.byref(count), None)
        if rc & 0xFFFFFFFF != _PDH_MORE_DATA:
            raise VramError(f"PdhGetFormattedCounterArray (size) failed: 0x{rc & 0xFFFFFFFF:08X}")
        buf = ctypes.create_string_buffer(size.value)
        rc = self._pdh.PdhGetFormattedCounterArrayW(self._counter, _PDH_FMT_LARGE, ctypes.byref(size),
                                                    ctypes.byref(count), buf)
        if rc:
            raise VramError(f"PdhGetFormattedCounterArray failed: 0x{rc & 0xFFFFFFFF:08X}")
        items = ctypes.cast(buf, ctypes.POINTER(_Item * count.value)).contents
        out: dict[int, int] = {}
        for item in items:
            name = item.szName or ""
            if item.FmtValue.CStatus or not name.startswith("pid_"):
                continue
            pid = int(name.split("_")[1])
            out[pid] = out.get(pid, 0) + item.FmtValue.largeValue
        return {pid: round(b / 2**20) for pid, b in out.items()}

    def close(self) -> None:
        if self._query:
            self._pdh.PdhCloseQuery(self._query)
            self._query = wintypes.HANDLE()


def listening_pid(base_url: str) -> int:
    """The process listening on a loopback endpoint's port (for servers the harness did not start)."""
    url = urlparse(base_url)
    if url.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise VramError(f"{base_url} is not on this machine; its GPU memory cannot be measured")
    for conn in psutil.net_connections(kind="tcp"):
        if conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == url.port and conn.pid:
            return conn.pid
    raise VramError(f"no process is listening on {base_url}")


class PeakSampler:
    """Samples the watched processes every `interval_s` seconds in a thread; `take()` returns the peak since
    the last `watch()` and is what a trial records."""

    def __init__(self, interval_s: float = 2.0) -> None:
        self.interval_s = interval_s
        self._counter = DedicatedCounter()
        self._lock = threading.Lock()
        self._pids: dict[str, int] = {}
        self._peak: dict[str, Any] = {}
        self._error: Exception | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="vram-sampler", daemon=True)

    def start(self) -> None:
        self._counter.by_pid()
        self._thread.start()

    def watch(self, pids: dict[str, int]) -> None:
        """Start a new peak over these processes ({endpoint name: pid}); takes one sample at once."""
        with self._lock:
            self._pids = dict(pids)
            self._peak = {"total_mib": 0, "by_endpoint": {n: 0 for n in pids}, "samples": 0}
        self._sample()

    def take(self) -> dict[str, Any]:
        self._sample()
        with self._lock:
            if self._error:
                raise VramError(f"VRAM sampling failed: {self._error}") from self._error
            return {**self._peak, "by_endpoint": dict(self._peak["by_endpoint"]),
                    "pids": dict(self._pids), "interval_s": self.interval_s, "counter": COUNTER}

    def _sample(self) -> None:
        try:
            usage = self._counter.by_pid()
        except Exception as exc:  # recorded and raised by take()
            with self._lock:
                self._error = exc
            return
        with self._lock:
            if not self._pids:
                return
            per = {n: usage.get(pid, 0) for n, pid in self._pids.items()}
            total = sum(usage.get(pid, 0) for pid in set(self._pids.values()))
            self._peak["samples"] += 1
            self._peak["total_mib"] = max(self._peak["total_mib"], total)
            for n, v in per.items():
                self._peak["by_endpoint"][n] = max(self._peak["by_endpoint"][n], v)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            self._sample()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=10)
        self._counter.close()
