"""Opt-in autostart: start Jig when the user logs in, only after they have explicitly agreed.

Nothing here runs unless the user asks for it (``jig autostart enable`` with a y/N prompt or ``--yes``,
or ``POST /autostart/enable`` with ``"confirm": true``). Each backend registers one entry for the
current user only and can show exactly what it will register before doing so:

* Windows: a per-user Task Scheduler task at logon (``windows.py``). Tested.
* macOS: a launchd LaunchAgent (``macos.py``). UNTESTED.
* Linux: a systemd user unit (``linux.py``). UNTESTED.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .base import AutostartBackend, AutostartError, LaunchSpec, Plan, Status, record_audit

__all__ = ["AutostartBackend", "AutostartError", "LaunchSpec", "Plan", "Status", "backend_for", "enable",
           "disable"]


def backend_for(spec: LaunchSpec, *, entry: str | None = None, platform: str | None = None) -> AutostartBackend:
    platform = platform or sys.platform
    if platform == "win32":
        from .windows import WindowsTaskScheduler

        return WindowsTaskScheduler(spec, entry=entry)
    if platform == "darwin":
        from .macos import LaunchdAgent

        return LaunchdAgent(spec, entry=entry)
    if platform.startswith("linux"):
        from .linux import SystemdUserUnit

        return SystemdUserUnit(spec, entry=entry)
    raise AutostartError(f"autostart is not supported on {platform}")


def enable(backend: AutostartBackend, *, confirmed: bool, via: str, start_now: bool = False) -> Plan:
    if not confirmed:
        raise AutostartError("autostart was not enabled: it needs explicit confirmation")
    plan = backend.enable(start_now=start_now)
    record_audit(backend.spec.data_dir, "autostart.enabled", f"autostart enabled ({plan.backend})", via=via,
                 entry=plan.entry, command_line=plan.command_line, trigger=plan.trigger, account=plan.account,
                 log_path=plan.log_path, started_now=start_now)
    return plan


def disable(backend: AutostartBackend, *, via: str) -> list[str]:
    removed = backend.disable()
    record_audit(Path(backend.spec.data_dir), "autostart.disabled",
                 f"autostart disabled; removed {len(removed)} item(s)", via=via, entry=backend.entry,
                 removed=removed)
    return removed
