"""Compute policy: heavy runs only overnight (01:00-07:00 Europe/London) or when the machine is idle.

* May start when (in the overnight window AND no input for `window_min_idle_s`, default 5 minutes) OR
  (no input for `idle_start_s`, default 30 minutes), AND no other process is using the GPU significantly.
* Must stop promptly when the user becomes active (input within the last `active_s`, default 60 s), when
  another process needs the GPU (measured in a quiet gap between trials, when we are not using it), or
  when the overnight window ends and the machine is not idle.
Stopping cancels the current trial (results are only written for finished trials), stops our model
servers and exits; the next scheduled start resumes from the results file.
"""

from __future__ import annotations

import ctypes
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from datetime import time as dtime
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")


class Paused(Exception):
    """Raised to stop a run politely; the run resumes later."""


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def idle_seconds() -> float:
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        raise OSError("GetLastInputInfo failed")
    now = ctypes.windll.kernel32.GetTickCount()
    return ((now - info.dwTime) & 0xFFFFFFFF) / 1000.0


def gpu_utilisation_samples(n: int = 5, interval_s: float = 1.0) -> list[int]:
    out = []
    for i in range(n):
        r = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=30, check=True)
        out.append(int(r.stdout.strip().splitlines()[0]))
        if i < n - 1:
            time.sleep(interval_s)
    return out


def in_window(now: datetime, start: dtime = dtime(1, 0), end: dtime = dtime(7, 0)) -> bool:
    t = now.astimezone(LONDON).time()
    return start <= t < end


@dataclass
class Policy:
    window_min_idle_s: float = 300
    idle_start_s: float = 1800
    active_s: float = 60
    gpu_busy_pct: float = 25

    def gpu_busy(self) -> tuple[bool, list[int]]:
        samples = gpu_utilisation_samples()
        return sum(samples) / len(samples) > self.gpu_busy_pct, samples

    def may_start(self, now: datetime | None = None) -> tuple[bool, str]:
        now = now or datetime.now(LONDON)
        idle = idle_seconds()
        window = in_window(now)
        if not ((window and idle >= self.window_min_idle_s) or idle >= self.idle_start_s):
            return False, (f"not allowed: window={window}, idle={idle:.0f}s (need {self.window_min_idle_s:.0f}s in "
                           f"the 01:00-07:00 window or {self.idle_start_s:.0f}s otherwise)")
        busy, samples = self.gpu_busy()
        if busy:
            return False, f"GPU busy with another process (utilisation samples {samples})"
        return True, f"allowed: window={window}, idle={idle:.0f}s, GPU samples {samples}"

    def must_stop(self, now: datetime | None = None) -> str | None:
        """Cheap check, safe to call every few seconds while a trial runs (does not sample the GPU)."""
        now = now or datetime.now(LONDON)
        idle = idle_seconds()
        if idle < self.active_s:
            return f"user active (last input {idle:.0f}s ago)"
        if not in_window(now) and idle < self.idle_start_s:
            return "outside the overnight window and the machine is not idle"
        return None

    def between_trials(self) -> str | None:
        """Full check in a quiet gap when we are not using the GPU."""
        if reason := self.must_stop():
            return reason
        busy, samples = self.gpu_busy()
        if busy:
            return f"another process is using the GPU (utilisation samples {samples})"
        return None


class NoPolicy:
    """Development pilots (approved to run now): no gating."""

    def may_start(self, now: datetime | None = None) -> tuple[bool, str]:
        return True, "pilot: no compute policy"

    def must_stop(self, now: datetime | None = None) -> str | None:
        return None

    def between_trials(self) -> str | None:
        return None
