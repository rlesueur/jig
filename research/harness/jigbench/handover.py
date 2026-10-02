"""Safe GPU handover for the research harness (not in jig/ core).

For some runs a test model needs the whole GPU to itself, which means briefly stopping the main model
server on port 8080. This module does that *safely* and *reversibly*, and never interrupts anything that is
actually in use:

1. Preconditions (re-checked immediately before stopping):
   - inside the overnight window, or the user idle for >= 30 min;
   - the 8080 server has had no active request for >= 10 continuous minutes (polled from llama-server's
     /slots, or /metrics as a fallback);
   - Jig (127.0.0.1:8766) reports no running tasks/steps, no in-flight chat, nothing scheduled during the
     handover window (if Jig is not running, this passes, and we record it);
   - no pytest / demo-recording / playwright processes are running.
2. Pause Jig first (its real /agent/pause), so no new work starts; record that we paused it.
3. Record the exact command line, cwd and relevant environment of the live 8080 process to
   research/logs/handover-<ts>.json, then stop it gracefully (polite stop, wait, force only on timeout) and
   confirm VRAM was released.
4. The caller runs its test model(s) within the VRAM budget, still re-checking user activity.
5. ALWAYS restore: stop our server, restart the original from the recorded command line/cwd/env (logs to
   research/logs/), wait until it is healthy (/v1/models serves the same alias), then unpause Jig only if we
   paused it. Restoration is guaranteed by try/finally here AND by an independent watchdog
   (``jigbench restore-watchdog``) that restores from the latest unrestored handover file if no harness is
   alive.
6. Ownership: if Jig *launched and supervises* the 8080 server (its [model.launch] supervisor would restart
   it), we never kill it behind Jig's back. If Jig's power API (POST /power/stop scope 'jig_and_model' /
   ``jig model stop|start``) is available we use it; otherwise the handover is refused and logged.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import httpx
import psutil

from .compute_policy import LONDON, idle_seconds, in_window
from .paths import LOGS, REPO
from .servers import free_vram_mib

log = logging.getLogger("jigbench.handover")

MAIN_PORT = 8080
JIG_URL = "http://127.0.0.1:8766"
HANDOVER_LOG = LOGS / "handover.log"
# Environment variables worth restoring with the server (GPU/model/runtime relevant only; never secrets by
# default beyond these well-known prefixes).
SAFE_ENV_PREFIXES = ("CUDA", "GGML", "LLAMA", "HSA", "HIP", "HF_", "OMP", "KMP", "PATH", "TEMP", "TMP")
HANDOVER_IDLE_REQUIRED_S = 600.0      # 10 continuous minutes with no active request on 8080
HANDOVER_WINDOW_S = 1800.0            # "expected handover window" for the no-schedule-due check


class HandoverError(RuntimeError):
    pass


class HandoverBlocked(HandoverError):
    """Preconditions not met; nothing was stopped."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now()).isoformat(timespec="seconds")


def audit(event: str, **fields: Any) -> None:
    """Append one structured line to research/logs/handover.log and the Python log."""
    LOGS.mkdir(parents=True, exist_ok=True)
    rec = {"ts": _iso(), "event": event, **fields}
    with HANDOVER_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")
    log.info("handover: %s %s", event, {k: v for k, v in fields.items() if k != "process"})


# --------------------------------------------------------------------------- process capture / control
def find_listener_pid(port: int) -> int | None:
    for c in psutil.net_connections(kind="inet"):
        if c.laddr and c.laddr.port == port and c.status == psutil.CONN_LISTEN and c.pid:
            return c.pid
    return None


def capture_process(pid: int) -> dict[str, Any]:
    """Everything needed to restart the process identically: command line, cwd and relevant environment."""
    p = psutil.Process(pid)
    with p.oneshot():
        rec: dict[str, Any] = {"pid": pid, "name": p.name(), "exe": p.exe(), "cmdline": p.cmdline(),
                               "cwd": p.cwd(), "ppid": p.ppid(), "create_time": p.create_time()}
    try:
        env = p.environ()
        rec["environ"] = {k: v for k, v in env.items()
                          if any(k.upper().startswith(pref) for pref in SAFE_ENV_PREFIXES)}
    except (psutil.AccessDenied, OSError) as exc:
        rec["environ"] = None
        rec["environ_error"] = str(exc)
    return rec


def stop_process_gracefully(pid: int, *, grace_s: float = 60.0, force_s: float = 30.0) -> str:
    """Polite stop, wait, force only if it does not exit in time. Returns how it ended."""
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return "already gone"
    # Polite: taskkill without /F asks the process to close; llama-server has no window, so this may not
    # suffice, but we always try it first before forcing.
    subprocess.run(["taskkill", "/PID", str(pid), "/T"], capture_output=True, text=True)
    try:
        proc.wait(timeout=grace_s)
        return "stopped politely"
    except psutil.TimeoutExpired:
        pass
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
    try:
        proc.wait(timeout=force_s)
        return "forced"
    except psutil.TimeoutExpired as exc:
        raise HandoverError(f"process {pid} did not exit after force kill") from exc


def restart_from_record(rec: dict[str, Any], *, log_dir: Path | None = None) -> subprocess.Popen[bytes]:
    """Restart the original server from a captured record. Not tied to the harness lifetime: it must
    outlive this process, so it is a plain Popen with no kill-on-exit job."""
    log_dir = log_dir or (LOGS / "servers")
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = (log_dir / f"main-restore-{ts}.log").open("ab")
    env = os.environ.copy()
    env.update(rec.get("environ") or {})
    out.write(f"\n=== {_iso()} restore: {' '.join(rec['cmdline'])}\n".encode())
    out.flush()
    return subprocess.Popen(rec["cmdline"], cwd=rec.get("cwd") or None, env=env, stdout=out,
                            stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)


# --------------------------------------------------------------------------- 8080 idleness
def server_slots_idle(port: int = MAIN_PORT, *, timeout: float = 3.0) -> tuple[bool | None, str]:
    """(idle, detail). idle is None when it cannot be determined (then we must not stop)."""
    base = f"http://127.0.0.1:{port}"
    try:
        r = httpx.get(base + "/slots", timeout=timeout)
    except httpx.HTTPError as exc:
        # /slots disabled or server down; try /metrics as a fallback.
        return _metrics_idle(port, timeout=timeout) if _reachable(port, timeout) else (None, f"8080 unreachable: {exc}")
    if r.status_code != 200:
        return _metrics_idle(port, timeout=timeout)
    try:
        slots = r.json()
    except ValueError:
        return None, "/slots did not return JSON"
    if not isinstance(slots, list):
        return None, f"/slots unexpected shape: {type(slots).__name__}"
    busy = [s.get("id") for s in slots if s.get("is_processing") is True or s.get("state") == 1]
    return (not busy), f"{len(slots)} slots, busy={busy}"


def _reachable(port: int, timeout: float) -> bool:
    try:
        httpx.get(f"http://127.0.0.1:{port}/health", timeout=timeout)
        return True
    except httpx.HTTPError:
        return False


def _metrics_idle(port: int, *, timeout: float) -> tuple[bool | None, str]:
    try:
        r = httpx.get(f"http://127.0.0.1:{port}/metrics", timeout=timeout)
    except httpx.HTTPError as exc:
        return None, f"neither /slots nor /metrics available ({exc})"
    if r.status_code != 200:
        return None, f"/metrics HTTP {r.status_code}; cannot confirm idle"
    processing = None
    for line in r.text.splitlines():
        if line.startswith("llamacpp:requests_processing"):
            processing = float(line.split()[-1])
    if processing is None:
        return None, "/metrics has no requests_processing gauge"
    return (processing == 0), f"requests_processing={processing}"


@dataclass
class IdleWatch:
    """Track continuous idleness of the 8080 server across polls."""

    port: int = MAIN_PORT
    required_idle_s: float = HANDOVER_IDLE_REQUIRED_S
    _idle_since: float | None = None

    def sample(self) -> tuple[bool, str]:
        idle, detail = server_slots_idle(self.port)
        now = time.monotonic()
        if idle is None:
            self._idle_since = None
            return False, f"cannot confirm 8080 idle ({detail})"
        if not idle:
            self._idle_since = None
            return False, f"8080 busy ({detail})"
        if self._idle_since is None:
            self._idle_since = now
        held = now - self._idle_since
        return held >= self.required_idle_s, f"8080 idle {held:.0f}s/{self.required_idle_s:.0f}s ({detail})"

    def wait_until_idle(self, *, deadline_s: float, poll_s: float = 10.0,
                        is_cancelled: Any = None) -> tuple[bool, str]:
        end = time.monotonic() + deadline_s
        last = "not polled"
        while time.monotonic() < end:
            if is_cancelled and is_cancelled():
                return False, "cancelled while waiting for 8080 to be idle"
            ok, last = self.sample()
            if ok:
                return True, last
            time.sleep(poll_s)
        return False, f"8080 not continuously idle before deadline ({last})"


# --------------------------------------------------------------------------- Jig
@dataclass
class JigClient:
    base_url: str = JIG_URL
    token: str | None = None

    def __post_init__(self) -> None:
        if self.token is None:
            self.token = self._discover_token()

    @staticmethod
    def _discover_token() -> str | None:
        if tok := os.environ.get("JIG_API_TOKEN"):
            return tok.strip()
        candidates = []
        if dd := os.environ.get("JIG_DATA_DIR"):
            candidates.append(Path(dd) / "api-token")
        candidates += [REPO / "data" / "api-token", REPO / "jig" / "data" / "api-token"]
        for path in candidates:
            if path.exists():
                return path.read_text(encoding="utf-8").strip()
        return None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def running(self) -> bool:
        try:
            return httpx.get(f"{self.base_url}/health", timeout=3).status_code == 200
        except httpx.HTTPError:
            return False

    def _get(self, path: str) -> Any:
        r = httpx.get(f"{self.base_url}{path}", headers=self._headers(), timeout=10)
        r.raise_for_status()
        return r.json()

    def status(self) -> dict[str, Any] | None:
        try:
            return self._get("/status")
        except httpx.HTTPError:
            return None

    def busy(self, *, window_s: float = HANDOVER_WINDOW_S) -> tuple[bool, str]:
        """True if Jig has work running, in flight, or scheduled within the handover window."""
        if not self.running():
            return False, "Jig not running (recorded)"
        if not self.token:
            return True, "Jig is running but no API token was found; refusing to assume it is idle"
        try:
            state = self._get("/state")
            tasks = self._get("/tasks?newest_first=true&limit=50")
            schedules = self._get("/schedules")
        except httpx.HTTPError as exc:
            return True, f"Jig API error, assuming busy: {exc}"
        active_states = {"running", "waiting_approval", "in_progress"}
        active = [t for t in _as_list(tasks) if str(t.get("status")) in active_states]
        if active:
            return True, f"Jig has active tasks: {[t.get('id') for t in active[:5]]}"
        avatar = (state or {}).get("state")
        if avatar in {"thinking", "working", "talking", "approval"}:
            return True, f"Jig avatar state is {avatar!r}"
        soon = _now() + timedelta(seconds=window_s)
        for s in _as_list(schedules):
            nxt = _parse_dt(s.get("next_run") or s.get("next_run_at"))
            if nxt and nxt <= soon and s.get("enabled", True):
                return True, f"a schedule is due within the handover window ({s.get('id')} at {nxt.isoformat()})"
        return False, "Jig running but idle (no active tasks, idle avatar, nothing due soon)"

    def has_queued_work(self) -> bool:
        if not (self.running() and self.token):
            return False
        try:
            tasks = self._get("/tasks?newest_first=true&limit=50")
        except httpx.HTTPError:
            return False
        return any(str(t.get("status")) in {"queued", "running", "waiting_approval", "in_progress"}
                   for t in _as_list(tasks))

    def pause(self) -> bool:
        r = httpx.post(f"{self.base_url}/agent/pause", headers=self._headers(), timeout=10)
        r.raise_for_status()
        return True

    def resume(self) -> bool:
        r = httpx.post(f"{self.base_url}/agent/resume", headers=self._headers(), timeout=10)
        r.raise_for_status()
        return True

    def power_api_available(self) -> bool:
        """Whether Jig exposes a power API to stop/start the model it supervises (POST /power/stop)."""
        for path in ("/power", "/power/status"):
            try:
                r = httpx.get(f"{self.base_url}{path}", headers=self._headers(), timeout=3)
            except httpx.HTTPError:
                continue
            if r.status_code != 404:
                return True
        return False

    def power_stop_model(self) -> None:
        r = httpx.post(f"{self.base_url}/power/stop", headers=self._headers(),
                       json={"scope": "jig_and_model"}, timeout=30)
        r.raise_for_status()

    def power_start_model(self) -> None:
        r = httpx.post(f"{self.base_url}/power/start", headers=self._headers(),
                       json={"scope": "jig_and_model"}, timeout=120)
        r.raise_for_status()


def _as_list(obj: Any) -> list[dict[str, Any]]:
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for key in ("tasks", "schedules", "items", "results"):
            if isinstance(obj.get(key), list):
                return obj[key]
    return []


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def jig_supervises(main_pid: int, jig: JigClient) -> tuple[bool, str]:
    """Does Jig own/supervise the 8080 process (so its supervisor would restart it)?"""
    try:
        p = psutil.Process(main_pid)
        chain = [p, *p.parents()]
    except psutil.Error:
        chain = []
    for anc in chain:
        try:
            cl = " ".join(anc.cmdline()).lower()
        except psutil.Error:
            continue
        if "jig" in cl and ("serve" in cl or "autostart" in cl or "jig.autostart.launch" in cl):
            return True, f"8080 is a descendant of a Jig process (pid {anc.pid})"
    st = jig.status() if jig.running() else None
    ms = (st or {}).get("model_server") or {}
    if ms.get("pid") == main_pid and ms.get("already_running") is False:
        return True, "Jig reports it launched the 8080 model server"
    return False, "no Jig supervisor detected for 8080 (looks hand-started)"


# --------------------------------------------------------------------------- blocking-process checks
BLOCKER_HINTS = ("pytest", "playwright", "run.ps1")


def blocking_processes() -> list[str]:
    """pytest / demo-recording / playwright processes that must not be interrupted."""
    found = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cl = " ".join(p.info.get("cmdline") or []).lower()
        except psutil.Error:
            continue
        if any(h in cl for h in ("pytest", "playwright")) or ("demos" in cl and "run.ps1" in cl):
            if p.info["pid"] != os.getpid():
                found.append(f"{p.info['name']} (pid {p.info['pid']})")
    return found


# --------------------------------------------------------------------------- handover files
def handover_files() -> list[Path]:
    return sorted(LOGS.glob("handover-*.json"))


def unrestored_handovers() -> list[Path]:
    out = []
    for path in handover_files():
        try:
            if not json.loads(path.read_text(encoding="utf-8")).get("restored", False):
                out.append(path)
        except (OSError, ValueError):
            continue
    return out


def write_handover_file(record: dict[str, Any], extra: dict[str, Any]) -> Path:
    LOGS.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = LOGS / f"handover-{ts}.json"
    data = {"created": _iso(), "restored": False, "main_port": MAIN_PORT, "harness_pid": os.getpid(),
            "process": record, **extra}
    path.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
    return path


def mark_restored(path: Path, *, ok: bool, detail: str) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(restored=True, restored_ok=ok, restored_at=_iso(), restore_detail=detail)
    path.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")


# --------------------------------------------------------------------------- preconditions
@dataclass
class Preconditions:
    ok: bool
    reasons: list[str]
    main_pid: int | None
    supervised: bool
    power_api: bool


def check_preconditions(jig: JigClient, *, idle_watch: IdleWatch | None = None,
                        require_window_or_idle: bool = True) -> Preconditions:
    reasons: list[str] = []
    # 1. window / idle
    if require_window_or_idle:
        idle = idle_seconds()
        if not (in_window(datetime.now(LONDON)) or idle >= 1800):
            reasons.append(f"not in the overnight window and user idle only {idle:.0f}s (<1800s)")
    # 2. 8080 idle for >= 10 continuous minutes
    watch = idle_watch or IdleWatch()
    ok_idle, detail = watch.sample()
    if not ok_idle:
        reasons.append(f"8080 not confirmed idle for 10 min: {detail}")
    # 3. Jig idle
    busy, why = jig.busy()
    if busy:
        reasons.append(f"Jig busy: {why}")
    # 4. no pytest/demo/playwright
    if blockers := blocking_processes():
        reasons.append(f"blocking processes running: {blockers}")
    # ownership
    main_pid = find_listener_pid(MAIN_PORT)
    supervised, sup_why = (False, "no process on 8080")
    power_api = False
    if main_pid:
        supervised, sup_why = jig_supervises(main_pid, jig)
        if supervised:
            power_api = jig.power_api_available()
            if not power_api:
                reasons.append(f"Jig supervises 8080 ({sup_why}) and no power API is available; "
                               "refusing to kill it behind Jig's supervisor")
    else:
        reasons.append("no process is listening on 8080")
    return Preconditions(ok=not reasons, reasons=reasons, main_pid=main_pid, supervised=supervised,
                         power_api=power_api)


# --------------------------------------------------------------------------- acquire / restore
@dataclass
class Handover:
    record: dict[str, Any]
    file: Path
    paused_jig: bool
    supervised: bool
    power_api: bool
    free_vram_before: int
    restored: bool = False


def acquire(jig: JigClient | None = None, *, idle_watch: IdleWatch | None = None,
            require_window_or_idle: bool = True, wait_idle_s: float = 0.0) -> Handover:
    """Run the preconditions, pause Jig, record and stop the 8080 server. Raises HandoverBlocked if any
    precondition fails (nothing is stopped)."""
    jig = jig or JigClient()
    watch = idle_watch or IdleWatch()
    if wait_idle_s:
        got, detail = watch.wait_until_idle(deadline_s=wait_idle_s)
        audit("wait_idle", ok=got, detail=detail)
    pre = check_preconditions(jig, idle_watch=watch, require_window_or_idle=require_window_or_idle)
    if not pre.ok:
        audit("blocked", reasons=pre.reasons)
        raise HandoverBlocked("; ".join(pre.reasons))
    assert pre.main_pid is not None
    record = capture_process(pre.main_pid)
    free_before = free_vram_mib()
    # 2. pause Jig first
    paused = False
    if jig.running() and jig.token:
        try:
            jig.pause()
            paused = True
            audit("jig_paused")
        except httpx.HTTPError as exc:
            audit("jig_pause_failed", error=str(exc))
            raise HandoverBlocked(f"could not pause Jig before handover: {exc}") from exc
    # 3. record then stop
    hfile = write_handover_file(record, {"supervised": pre.supervised, "power_api": pre.power_api,
                                         "paused_jig": paused, "free_vram_before_mib": free_before})
    audit("recorded", file=str(hfile), process={k: record.get(k) for k in ("pid", "exe", "cmdline", "cwd")})
    if pre.supervised and pre.power_api:
        jig.power_stop_model()
        audit("stopped_via_power_api")
    else:
        ended = stop_process_gracefully(pre.main_pid)
        audit("stopped_process", how=ended, pid=pre.main_pid)
    # confirm VRAM released
    released = _confirm_vram_released(free_before)
    audit("vram", **released)
    return Handover(record=record, file=hfile, paused_jig=paused, supervised=pre.supervised,
                    power_api=pre.power_api, free_vram_before=free_before)


def _confirm_vram_released(free_before: int, *, timeout_s: float = 30.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    free_after = free_before
    while time.monotonic() < deadline:
        free_after = free_vram_mib()
        if free_after - free_before > 256:  # anything meaningful came back
            return {"released": True, "free_before_mib": free_before, "free_after_mib": free_after}
        time.sleep(2)
    return {"released": False, "free_before_mib": free_before, "free_after_mib": free_after,
            "note": "VRAM did not visibly increase; the server may have held little, or another process took it"}


def restore(handover: Handover, jig: JigClient | None = None, *, health_timeout_s: float = 300.0) -> bool:
    """Restart the original 8080 server from the record, wait until healthy, then unpause Jig if we paused
    it. Loud on failure. Idempotent: safe to call again."""
    if handover.restored:
        return True
    jig = jig or JigClient()
    alias = _alias_before()
    try:
        if handover.supervised and handover.power_api:
            jig.power_start_model()
            audit("restored_via_power_api")
        else:
            restart_from_record(handover.record)
            audit("restart_launched")
        ok, detail = wait_main_healthy(alias=alias, timeout_s=health_timeout_s)
        if not ok:
            audit("restore_unhealthy", detail=detail)
            mark_restored(handover.file, ok=False, detail=detail)
            raise HandoverError(f"8080 did not become healthy after restore: {detail}")
        audit("restore_healthy", detail=detail)
        if handover.paused_jig and jig.running() and jig.token:
            jig.resume()
            audit("jig_resumed")
        mark_restored(handover.file, ok=True, detail=detail)
        handover.restored = True
        return True
    except HandoverError:
        raise
    except Exception as exc:  # restoration must fail loudly, never silently
        audit("restore_error", error=str(exc))
        mark_restored(handover.file, ok=False, detail=f"{type(exc).__name__}: {exc}")
        raise HandoverError(f"restore failed: {exc}") from exc


def _alias_before() -> str | None:
    try:
        r = httpx.get(f"http://127.0.0.1:{MAIN_PORT}/v1/models", timeout=3)
        if r.status_code == 200:
            data = r.json().get("data") or []
            return data[0]["id"] if data else None
    except httpx.HTTPError:
        pass
    return None


def wait_main_healthy(*, alias: str | None, timeout_s: float = 300.0) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout_s
    last = "no response"
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"http://127.0.0.1:{MAIN_PORT}/v1/models", timeout=5)
            if r.status_code == 200:
                ids = [m.get("id") for m in (r.json().get("data") or [])]
                if alias is None or alias in ids:
                    return True, f"/v1/models serves {ids}"
                last = f"served {ids}, expected alias {alias!r}"
        except httpx.HTTPError as exc:
            last = str(exc)
        time.sleep(3)
    return False, last


@contextmanager
def gpu_handover(jig: JigClient | None = None, *, require_window_or_idle: bool = True,
                 wait_idle_s: float = 0.0, idle_watch: IdleWatch | None = None) -> Iterator[Handover]:
    """Context manager: acquire the GPU (stop 8080), yield, and ALWAYS restore in finally."""
    jig = jig or JigClient()
    ho = acquire(jig, idle_watch=idle_watch, require_window_or_idle=require_window_or_idle, wait_idle_s=wait_idle_s)
    try:
        yield ho
    finally:
        restore(ho, jig)


# --------------------------------------------------------------------------- watchdog
def harness_alive(exclude_pid: int | None = None) -> bool:
    """Is any jigbench harness process (run/overnight) alive (other than this one)?"""
    me = exclude_pid or os.getpid()
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            cl = " ".join(p.info.get("cmdline") or []).lower()
        except psutil.Error:
            continue
        if p.info["pid"] == me:
            continue
        if "jigbench" in cl and ("overnight" in cl or " run " in f" {cl} "):
            return True
    return False


def restore_watchdog() -> int:
    """Independent restorer. If no harness is alive and there is an unrestored handover, restore from the
    latest one. Intended to run every few minutes from a per-user scheduled task."""
    pending = unrestored_handovers()
    if not pending:
        return 0
    if harness_alive():
        audit("watchdog_skip", reason="a harness is alive; leaving restoration to it", pending=len(pending))
        return 0
    latest = pending[-1]
    audit("watchdog_restore_start", file=str(latest), pending=len(pending))
    data = json.loads(latest.read_text(encoding="utf-8"))
    ho = Handover(record=data["process"], file=latest, paused_jig=bool(data.get("paused_jig")),
                  supervised=bool(data.get("supervised")), power_api=bool(data.get("power_api")),
                  free_vram_before=int(data.get("free_vram_before_mib") or 0))
    try:
        restore(ho)
        audit("watchdog_restore_ok", file=str(latest))
        return 0
    except HandoverError as exc:
        audit("watchdog_restore_failed", file=str(latest), error=str(exc))
        return 2
