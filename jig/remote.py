"""Use Jig from your other devices, through Tailscale.

Jig keeps listening on 127.0.0.1 only. Remote access goes through ``tailscale serve``: tailscaled
terminates HTTPS with a real certificate for ``<machine>.<tailnet>.ts.net`` and forwards the request,
only from devices on your tailnet, to ``http://127.0.0.1:<port>``. Jig never uses ``tailscale funnel``
(public internet exposure): it refuses to start if a funnel points at its port, and refuses every request
that tailscaled marks as funnelled.

``jig remote enable`` (or ``POST /remote/enable``) runs ``tailscale serve --bg --https=443
http://127.0.0.1:<port>`` and records the tailnet name, origin and enabling login in
``<data_dir>/remote.json``. ``--bg`` makes tailscaled keep the entry across restarts; Jig also checks it
at start-up and puts it back if it has gone (audited ``remote.restored``).

Which requests count as "through Tailscale". A request whose Host is the recorded tailnet name is only
accepted if:

1. its TCP connection comes from 127.0.0.1 *and is owned by the tailscaled process*: on Windows the
   connection's owning pid (GetExtendedTcpTable) must be the pid of the running Tailscale service; on
   Linux the socket's owner uid must be tailscaled's and differ from Jig's (UNTESTED). Any other local
   process could forge the Host and ``Tailscale-User-*`` headers, so those headers are ignored unless
   this check passes;
2. tailscaled named a tailnet user (``Tailscale-User-Login``; tailscaled deletes any such header the client
   sent), and that login is on the allow-list (``[remote] allowed_logins``, by default only the login that
   turned remote access on);
3. it carries a paired device session (the master token and browser sign-in are refused over the tailnet).

In container mode Jig cannot see which process owns a connection, so ``[remote] hostname`` is set
explicitly, the identity headers are ignored, and a paired device session is what grants access.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .config import Config
from .db import now_iso
from .errors import JigError

log = logging.getLogger(__name__)

STATE_FILENAME = "remote.json"
WINDOWS_SERVICE = "Tailscale"
HTTPS_PORT = 443
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
TAILSCALE_HEADERS = ("tailscale-user-login", "tailscale-user-name", "tailscale-user-profile-pic",
                     "tailscale-headers-info", "tailscale-app-capabilities")

CONTAINER_GUIDANCE = (
    "Jig runs in a container here, so it can't change the host's Tailscale settings. On the host, run "
    "'tailscale serve --bg --https=443 http://127.0.0.1:8766' (the published port), then set [remote] hostname "
    "(or JIG_REMOTE_HOSTNAME) in the container's config to the host's tailnet name, for example "
    "machine.tailnet.ts.net, and restart Jig. Or use a Tailscale sidecar container; see docs/container.md.")


class RemoteError(JigError):
    """Remote access could not be turned on or off, or Tailscale is not ready. Says what to do."""


# Tailscale CLI -------------------------------------------------------------------------------------

def tailscale_cli() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    if sys.platform == "win32":
        default = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale" / "tailscale.exe"
        return str(default) if default.is_file() else None
    if sys.platform == "darwin":
        app = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
        return str(app) if app.is_file() else None
    return None


def _run(cli: str, args: list[str], *, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run([cli, *args], capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as exc:
        raise RemoteError(f"'tailscale {' '.join(args)}' did not finish within {timeout:.0f}s. If it is waiting for "
                          "you to approve something in a browser, do that and try again.") from exc
    except OSError as exc:
        raise RemoteError(f"could not run {cli}: {exc}") from exc


def install_steps() -> list[str]:
    if sys.platform == "win32":
        install = ("Install Tailscale for Windows from https://tailscale.com/download/windows "
                   "(or run 'winget install Tailscale.Tailscale').")
    elif sys.platform == "darwin":
        install = "Install Tailscale from https://tailscale.com/download/mac."
    else:
        install = "Install Tailscale: https://tailscale.com/download/linux."
    return [install, *sign_in_steps()]


def sign_in_steps() -> list[str]:
    return ["Sign in on this computer: click the Tailscale icon and choose Log in (or run 'tailscale up'), and "
            "finish signing in in the browser.",
            "In the Tailscale admin console, DNS page (https://login.tailscale.com/admin/dns), make sure MagicDNS "
            "and HTTPS Certificates are both turned on.",
            "Install Tailscale on the devices you want to use Jig from, and sign in with the same account.",
            "Then turn on 'Use Jig from your other devices' in Settings, or run 'jig remote enable'."]


@dataclass
class TailscaleInfo:
    installed: bool
    cli: str | None = None
    version: str | None = None
    backend_state: str | None = None
    signed_in: bool = False
    dns_name: str | None = None
    login: str | None = None
    tailnet: str | None = None
    magic_dns: bool | None = None
    https_certificates: bool | None = None
    error: str | None = None
    steps: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.steps

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "ready": self.ready}


def tailscale_info() -> TailscaleInfo:
    """Whether Tailscale is installed and signed in, and exactly what the user still has to do if not.
    Reads only; never installs, starts or signs in anything."""
    cli = tailscale_cli()
    if cli is None:
        return TailscaleInfo(installed=False, error="Tailscale is not installed on this computer",
                             steps=install_steps())
    info = TailscaleInfo(installed=True, cli=cli)
    version = _run(cli, ["version"], timeout=15)
    info.version = version.stdout.strip().splitlines()[0] if version.returncode == 0 and version.stdout.strip() else None
    status = _run(cli, ["status", "--json"], timeout=15)
    try:
        data = json.loads(status.stdout)
    except ValueError:
        info.error = (status.stderr or status.stdout).strip() or f"'tailscale status' failed (exit {status.returncode})"
        info.steps = ["Start Tailscale: open the Tailscale app (or start the Tailscale service), then check again.",
                      *sign_in_steps()]
        return info
    info.backend_state = data.get("BackendState")
    me = data.get("Self") or {}
    info.dns_name = (me.get("DNSName") or "").rstrip(".").lower() or None
    user = (data.get("User") or {}).get(str(me.get("UserID")), {})
    info.login = (user.get("LoginName") or "").lower() or None
    tailnet = data.get("CurrentTailnet") or {}
    info.tailnet = tailnet.get("Name")
    info.magic_dns = tailnet.get("MagicDNSEnabled")
    info.https_certificates = bool(data.get("CertDomains"))
    info.signed_in = info.backend_state == "Running"
    if info.backend_state == "NeedsLogin" or info.backend_state == "NoState":
        info.steps = sign_in_steps()
    elif info.backend_state == "NeedsMachineAuth":
        info.steps = ["An admin of your tailnet must approve this computer in the admin console "
                      "(https://login.tailscale.com/admin/machines).", *sign_in_steps()[2:]]
    elif info.backend_state != "Running":
        info.steps = [f"Tailscale is {info.backend_state or 'not connected'}: connect it from the Tailscale icon "
                      "(or run 'tailscale up')."]
    elif not info.dns_name or not info.magic_dns:
        info.steps = ["Turn on MagicDNS in the Tailscale admin console (https://login.tailscale.com/admin/dns)."]
    elif not info.https_certificates:
        info.steps = ["Turn on HTTPS Certificates in the Tailscale admin console "
                      "(https://login.tailscale.com/admin/dns), under 'HTTPS Certificates'."]
    elif not info.login:
        info.steps = ["This computer is signed in as a tagged device, which has no user identity. Sign in as "
                      "yourself ('tailscale up --force-reauth') so Jig can restrict access to your login."]
    return info


def serve_config(cli: str) -> dict[str, Any]:
    r = _run(cli, ["serve", "status", "--json"], timeout=15)
    text = r.stdout.strip()
    if r.returncode != 0:
        raise RemoteError(f"'tailscale serve status --json' failed (exit {r.returncode}): {(r.stderr or text).strip()}")
    if not text or text in ("{}", "null"):
        return {}
    try:
        return json.loads(text)
    except ValueError as exc:
        raise RemoteError(f"could not read 'tailscale serve status --json': {text[:200]}") from exc


def _configs(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """The persistent serve config plus any foreground ('tailscale serve' without --bg) sessions."""
    return [cfg, *[c for c in (cfg.get("Foreground") or {}).values() if isinstance(c, dict)]]


def _targets_port(proxy: str | None, port: int) -> bool:
    if not proxy:
        return False
    parts = urlsplit(proxy if "://" in proxy else f"http://{proxy}")
    return parts.port == port and (parts.hostname or "127.0.0.1") in LOCAL_HOSTS


def funnels_to_port(cfg: dict[str, Any], port: int) -> list[str]:
    """Every tailnet address where a Tailscale Funnel (public internet) leads to Jig's port."""
    found = []
    for c in _configs(cfg):
        allow = c.get("AllowFunnel") or {}
        for hostport, web in (c.get("Web") or {}).items():
            if allow.get(hostport) and any(_targets_port(h.get("Proxy"), port)
                                           for h in (web.get("Handlers") or {}).values()):
                found.append(hostport)
        for tcp_port, handler in (c.get("TCP") or {}).items():
            if handler.get("TCPForward") and _targets_port(handler["TCPForward"], port) and any(
                    k.endswith(f":{tcp_port}") and v for k, v in allow.items()):
                found.append(f"tcp:{tcp_port}")
    return found


def serve_entry(cfg: dict[str, Any], hostname: str, port: int) -> dict[str, Any]:
    key = f"{hostname}:{HTTPS_PORT}"
    handlers = ((cfg.get("Web") or {}).get(key) or {}).get("Handlers") or {}
    root = handlers.get("/") or {}
    return {"present": bool(handlers), "proxy": root.get("Proxy"), "paths": sorted(handlers),
            "points_at_jig": set(handlers) == {"/"} and _targets_port(root.get("Proxy"), port),
            "funnel": bool((cfg.get("AllowFunnel") or {}).get(key))}


def funnel_refusal(where: list[str], port: int) -> str:
    return (f"Tailscale Funnel exposes Jig's port {port} to the public internet ({', '.join(where)}). Jig never runs "
            "behind a funnel. Turn it off with 'tailscale funnel --https=443 off' (or 'tailscale funnel reset'), "
            "then start Jig again. To use Jig from your own devices, use 'jig remote enable', which uses "
            "'tailscale serve' (your tailnet only).")


# Proving that a connection comes from tailscaled ---------------------------------------------------

_pid_cache: tuple[float, int | None, str] = (0.0, None, "")
_pid_lock = threading.Lock()


def _windows_tailscaled_pid() -> tuple[int | None, str]:
    import ctypes
    from ctypes import wintypes

    class SERVICE_STATUS_PROCESS(ctypes.Structure):
        _fields_ = [(n, wintypes.DWORD) for n in ("type", "state", "controls", "exit", "specific_exit",
                                                   "checkpoint", "wait_hint", "pid", "flags")]

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    adv.OpenSCManagerW.restype = wintypes.HANDLE
    adv.OpenSCManagerW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    adv.OpenServiceW.restype = wintypes.HANDLE
    adv.OpenServiceW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD]
    adv.QueryServiceStatusEx.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                         ctypes.POINTER(wintypes.DWORD)]
    adv.CloseServiceHandle.argtypes = [wintypes.HANDLE]
    scm = adv.OpenSCManagerW(None, None, 0x0001)  # SC_MANAGER_CONNECT
    if not scm:
        return None, f"the service manager could not be opened (Windows error {ctypes.get_last_error()})"
    try:
        svc = adv.OpenServiceW(scm, WINDOWS_SERVICE, 0x0004)  # SERVICE_QUERY_STATUS
        if not svc:
            err = ctypes.get_last_error()
            return None, ("the Tailscale service is not installed" if err == 1060
                          else f"the Tailscale service could not be opened (Windows error {err})")
        try:
            ssp, needed = SERVICE_STATUS_PROCESS(), wintypes.DWORD()
            if not adv.QueryServiceStatusEx(svc, 0, ctypes.byref(ssp), ctypes.sizeof(ssp), ctypes.byref(needed)):
                return None, f"the Tailscale service status could not be read (Windows error {ctypes.get_last_error()})"
            if ssp.state != 4 or not ssp.pid:  # SERVICE_RUNNING
                return None, "the Tailscale service is not running"
            return int(ssp.pid), ""
        finally:
            adv.CloseServiceHandle(svc)
    finally:
        adv.CloseServiceHandle(scm)


def tailscaled_pid() -> tuple[int | None, str]:
    """The pid of the running tailscaled (Windows: the Tailscale service), cached for 5 seconds."""
    global _pid_cache
    with _pid_lock:
        at, pid, why = _pid_cache
        if time.monotonic() - at < 5.0:
            return pid, why
        if sys.platform == "win32":
            pid, why = _windows_tailscaled_pid()
        else:
            pid, why = None, f"finding tailscaled's pid is only implemented on Windows, not {sys.platform}"
        _pid_cache = (time.monotonic(), pid, why)
        return pid, why


def tcp_owner_pid(local: tuple[str, int], remote: tuple[str, int]) -> int | None:
    """Windows: the pid owning the IPv4 TCP connection from ``local`` to ``remote`` (GetExtendedTcpTable)."""
    import ctypes
    from ctypes import wintypes

    class ROW(ctypes.Structure):  # MIB_TCPROW_OWNER_PID
        _fields_ = [("state", wintypes.DWORD), ("local_addr", wintypes.DWORD), ("local_port", wintypes.DWORD),
                    ("remote_addr", wintypes.DWORD), ("remote_port", wintypes.DWORD), ("pid", wintypes.DWORD)]

    iph = ctypes.WinDLL("iphlpapi")
    iph.GetExtendedTcpTable.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
                                        wintypes.ULONG, ctypes.c_int, wintypes.ULONG]
    size = wintypes.DWORD(0)
    for _ in range(5):
        buf = ctypes.create_string_buffer(size.value or 1)
        ret = iph.GetExtendedTcpTable(buf, ctypes.byref(size), False, socket.AF_INET, 5, 0)  # TCP_TABLE_OWNER_PID_ALL
        if ret == 0:
            break
        if ret != 122:  # ERROR_INSUFFICIENT_BUFFER
            raise OSError(f"GetExtendedTcpTable failed (error {ret})")
    else:
        raise OSError("GetExtendedTcpTable kept growing")
    count = wintypes.DWORD.from_buffer(buf).value
    rows = (ROW * count).from_buffer(buf, ctypes.sizeof(wintypes.DWORD))

    def addr(a: int, p: int) -> tuple[str, int]:
        return socket.inet_ntoa(struct.pack("<I", a)), socket.ntohs(p & 0xFFFF)

    for r in rows:
        if addr(r.local_addr, r.local_port) == local and addr(r.remote_addr, r.remote_port) == remote:
            return int(r.pid)
    return None


def _linux_socket_uid(local: tuple[str, int], remote: tuple[str, int]) -> int | None:
    def enc(ip: str, port: int) -> str:
        return f"{struct.unpack('<I', socket.inet_aton(ip))[0]:08X}:{port:04X}"

    want = (enc(*local), enc(*remote))
    for line in Path("/proc/net/tcp").read_text().splitlines()[1:]:
        f = line.split()
        if (f[1], f[2]) == want:
            return int(f[7])
    return None


def _linux_tailscaled_uids() -> set[int]:
    uids = set()
    for proc in Path("/proc").iterdir():
        try:
            if proc.name.isdigit() and (proc / "comm").read_text().strip() == "tailscaled":
                for line in (proc / "status").read_text().splitlines():
                    if line.startswith("Uid:"):
                        uids.add(int(line.split()[1]))
        except OSError:
            continue
    return uids


def verify_tailscale_peer(client: tuple[str, int] | None, server: tuple[str, int] | None) -> tuple[bool, str]:
    """Whether this TCP connection was made by the local tailscaled (that is, through ``tailscale serve``).
    Returns (False, why) for anything else, including every other local process."""
    if not client or not server:
        return False, "the connection's addresses are unknown"
    if client[0] != "127.0.0.1":
        return False, f"it came from {client[0]}, not from 127.0.0.1"
    if sys.platform == "win32":
        pid, why = tailscaled_pid()
        if pid is None:
            return False, why
        owner = tcp_owner_pid((client[0], int(client[1])), (server[0], int(server[1])))
        if owner is None:
            return False, "the connection's owner could not be found"
        if owner != pid:
            return False, f"the connection belongs to process {owner}, not to tailscaled (pid {pid})"
        return True, ""
    if sys.platform.startswith("linux"):  # UNTESTED: no Linux machine with Tailscale has run this yet
        uid = _linux_socket_uid((client[0], int(client[1])), (server[0], int(server[1])))
        ts_uids = _linux_tailscaled_uids()
        if uid is None or not ts_uids:
            return False, "tailscaled or the connection's owner could not be found"
        if uid not in ts_uids or uid == os.getuid():
            return False, ("the connection is not owned by tailscaled's user" if uid not in ts_uids else
                           "tailscaled runs as the same user as Jig, so its connections can't be told apart")
        return True, ""
    return False, f"checking that a connection comes from tailscaled is not implemented on {sys.platform}"


# Remote access state -------------------------------------------------------------------------------

@dataclass
class RequestSource:
    kind: str  # local | tailnet
    origin: str  # the only Origin accepted for cookie-authenticated state changes and WebSockets
    secure: bool  # set the Secure flag on cookies
    login: str | None = None  # the verified Tailscale login (tailnet requests on the host only)


class Refused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


class RemoteAccess:
    """The persisted remote-access setting and the per-request classification the middleware uses."""

    def __init__(self, config: Config):
        self.config = config
        self.path = config.data_dir / STATE_FILENAME
        self._cache: tuple[int, dict[str, Any]] | None = None

    @property
    def container(self) -> bool:
        return self.config.deployment == "container"

    def state(self) -> dict[str, Any]:
        try:
            mtime = self.path.stat().st_mtime_ns
        except FileNotFoundError:
            return {"enabled": False}
        if self._cache and self._cache[0] == mtime:
            return self._cache[1]
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise RemoteError(f"{self.path} is not valid JSON ({exc}); turn remote access off and on again") from exc
        self._cache = (mtime, data)
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def hostname(self) -> str | None:
        if self.container:
            return self.config.remote.hostname or None
        st = self.state()
        return st.get("hostname") if st.get("enabled") else None

    def origin(self) -> str | None:
        host = self.hostname()
        return f"https://{host}" if host else None

    def allowed_logins(self) -> list[str]:
        if self.config.remote.allowed_logins:
            return list(self.config.remote.allowed_logins)
        owner = self.state().get("owner_login")
        return [owner] if owner else []

    def classify(self, scope: dict[str, Any], headers: dict[str, str]) -> RequestSource:
        """Decide whether a request is local or came through tailscale serve. Raises Refused otherwise."""
        if headers.get("tailscale-funnel-request"):
            raise Refused(403, "Refused: this request came through Tailscale Funnel, from the public internet. Jig "
                               "never accepts that. Turn the funnel off with 'tailscale funnel --https=443 off'.")
        host = headers.get("host", "").lower()
        name = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
        remote_host = self.hostname()
        if remote_host and name == remote_host:
            if self.container:
                return RequestSource("tailnet", origin=f"https://{remote_host}", secure=True)
            ok, why = verify_tailscale_peer(scope.get("client"), scope.get("server"))
            if not ok:
                raise Refused(403, f"Refused: this request names Jig's tailnet address ({remote_host}) but did not "
                                   f"come through Tailscale on this computer: {why}.")
            login = (headers.get("tailscale-user-login") or "").strip().lower()
            if not login:
                raise Refused(403, "Refused: Tailscale did not identify a tailnet user for this request. Jig only "
                                   "accepts devices signed in as a person (not tagged devices).")
            allowed = self.allowed_logins()
            if login not in allowed:
                raise Refused(403, f"Refused: the Tailscale user {login} is not allowed to use this Jig. Allowed: "
                                   f"{', '.join(allowed) or 'nobody'}. Add them to [remote] allowed_logins on the host.")
            return RequestSource("tailnet", origin=f"https://{remote_host}", secure=True, login=login)
        if name.endswith(".ts.net"):
            raise Refused(403, "Refused: remote access is turned off on this Jig, or this is not its tailnet "
                               "address. Turn it on from Settings on the host, or run 'jig remote enable'.")
        scheme = "https" if scope.get("scheme") in ("https", "wss") else "http"
        return RequestSource("local", origin=f"{scheme}://{host}", secure=scheme == "https")

    # Turning it on and off -------------------------------------------------------------------------
    def status(self, port: int) -> dict[str, Any]:
        st = self.state()
        out: dict[str, Any] = {"deployment": self.config.deployment, "enabled": bool(self.hostname()),
                               "url": self.origin(), "hostname": self.hostname(),
                               "allowed_logins": self.allowed_logins(), "port": port, "problems": [],
                               "enabled_at": st.get("enabled_at"), "owner_login": st.get("owner_login")}
        if self.container:
            out["applicable"] = False
            out["reason"] = CONTAINER_GUIDANCE
            out["identity_checked"] = False
            return out
        out["applicable"] = True
        out["identity_checked"] = True
        info = tailscale_info()
        out["tailscale"] = info.as_dict()
        out["steps"] = info.steps
        if info.installed and info.backend_state is not None:
            try:
                cfg = serve_config(info.cli)  # type: ignore[arg-type]
            except RemoteError as exc:
                out["problems"].append(str(exc))
            else:
                if funnel := funnels_to_port(cfg, port):
                    out["problems"].append(funnel_refusal(funnel, port))
                if info.dns_name:
                    out["serve"] = serve_entry(cfg, info.dns_name, port)
                    if out["enabled"] and not out["serve"]["points_at_jig"]:
                        out["problems"].append("remote access is on, but Tailscale is not serving Jig at the moment; "
                                               "turn it off and on again")
        if out["enabled"] and info.dns_name and info.dns_name != st.get("hostname"):
            out["problems"].append(f"this computer's tailnet name is now {info.dns_name}, but remote access was "
                                   f"turned on as {st.get('hostname')}; turn it off and on again")
        return out

    def enable(self, port: int) -> dict[str, Any]:
        if self.container:
            raise RemoteError(CONTAINER_GUIDANCE)
        info = tailscale_info()
        if not info.ready:
            raise RemoteError("Remote access can't be turned on yet. " + (info.error + ". " if info.error else "") +
                              "Steps:\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(info.steps, 1)))
        cli, host = info.cli, info.dns_name
        assert cli and host and info.login
        cfg = serve_config(cli)
        if funnel := funnels_to_port(cfg, port):
            raise RemoteError(funnel_refusal(funnel, port))
        entry = serve_entry(cfg, host, port)
        if entry["present"] and not entry["points_at_jig"]:
            raise RemoteError(f"Tailscale already serves something else at https://{host}/ (paths {entry['paths']}, "
                              f"proxy {entry['proxy']}). Jig won't change your other Tailscale settings. If you no "
                              f"longer need it, remove it with 'tailscale serve --https=443 off', then try again.")
        if not entry["present"]:
            r = _run(cli, ["serve", "--bg", f"--https={HTTPS_PORT}", f"http://127.0.0.1:{port}"], timeout=60)
            if r.returncode != 0:
                raise RemoteError(f"'tailscale serve' failed (exit {r.returncode}): {(r.stderr or r.stdout).strip()}")
            after = serve_entry(serve_config(cli), host, port)
            if not after["points_at_jig"]:
                raise RemoteError(f"'tailscale serve' finished, but Tailscale does not show Jig at https://{host}/ "
                                  f"({after}). Nothing else was changed.")
        self._save({"enabled": True, "hostname": host, "origin": f"https://{host}", "port": port,
                    "owner_login": info.login, "tailnet": info.tailnet, "tailscale_version": info.version,
                    "enabled_at": now_iso(), "created_serve_entry": not entry["present"]})
        return self.status(port)

    def disable(self, port: int) -> dict[str, Any]:
        if self.container:
            raise RemoteError(CONTAINER_GUIDANCE)
        st = self.state()
        removed = False
        notes = []
        host, served_port = st.get("hostname"), st.get("port") or port
        cli = tailscale_cli()
        if host and cli:
            entry = serve_entry(serve_config(cli), host, served_port)
            if entry["points_at_jig"]:
                r = _run(cli, ["serve", f"--https={HTTPS_PORT}", "off"], timeout=60)
                if r.returncode != 0:
                    raise RemoteError(f"'tailscale serve --https=443 off' failed (exit {r.returncode}): "
                                      f"{(r.stderr or r.stdout).strip()}. Remote access is still on.")
                if serve_entry(serve_config(cli), host, served_port)["present"]:
                    raise RemoteError(f"Tailscale still shows an entry at https://{host}/ after turning it off.")
                removed = True
            elif entry["present"]:
                notes.append(f"Tailscale now serves something else at https://{host}/, so Jig left it alone.")
        elif host:
            notes.append("Tailscale is not installed any more, so there was no serve entry to remove.")
        self._save({**st, "enabled": False, "disabled_at": now_iso()})
        return {**self.status(port), "removed_serve_entry": removed, "notes": notes}

    def startup_check(self, port: int) -> list[tuple[str, str, dict[str, Any]]]:
        """At start-up: refuse to run behind a funnel; put Jig's serve entry back if it is on but gone.
        Returns audit records (kind, summary, data) for the runtime to write."""
        if self.container:
            if self.config.remote.hostname:
                log.warning("remote access: accepting https://%s through tailscale serve on the host. In container "
                            "mode Jig cannot verify that requests come from tailscaled, so Tailscale identity "
                            "headers are ignored and a paired device session is required.", self.config.remote.hostname)
            return []
        cli = tailscale_cli()
        st = self.state()
        if cli is None:
            if st.get("enabled"):
                log.error("remote access is on, but Tailscale is not installed any more; Jig is reachable only on "
                          "this computer. Reinstall Tailscale, or turn remote access off.")
                return [("remote.unavailable", "remote access is on but Tailscale is not installed", {})]
            return []
        try:
            cfg = serve_config(cli)
        except RemoteError as exc:
            log.error("could not check Tailscale for a funnel to Jig's port: %s", exc)
            return [("remote.check_failed", f"could not check Tailscale serve/funnel: {exc}", {})]
        if funnel := funnels_to_port(cfg, port):
            raise RemoteError(funnel_refusal(funnel, port))
        if not st.get("enabled"):
            return []
        entry = serve_entry(cfg, st["hostname"], port)
        if entry["points_at_jig"]:
            return []
        if entry["present"]:
            log.error("remote access is on, but Tailscale now serves something else at https://%s/; not changing it",
                      st["hostname"])
            return [("remote.restore_failed", "Tailscale serves something else at Jig's address", entry)]
        r = _run(cli, ["serve", "--bg", f"--https={HTTPS_PORT}", f"http://127.0.0.1:{port}"], timeout=60)
        if r.returncode != 0:
            log.error("could not put Jig's tailscale serve entry back: %s", (r.stderr or r.stdout).strip())
            return [("remote.restore_failed", "could not restore the tailscale serve entry",
                     {"exit_code": r.returncode, "output": (r.stderr or r.stdout).strip()[:500]})]
        log.info("remote access: restored the tailscale serve entry for https://%s", st["hostname"])
        return [("remote.restored", f"restored tailscale serve for https://{st['hostname']}", {"port": port})]
