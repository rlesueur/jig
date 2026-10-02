"""API access token, browser sessions and the authentication middleware.

* A random token is created on first run in ``<data_dir>/api-token``. Only the
  current user may read it: on Windows the file's ACL is reduced to a single
  entry for the current user (with ``icacls``) and then verified through the
  Win32 security API; elsewhere the file is created with mode 0600. If either
  step fails, Jig refuses to continue.
* Programs send ``Authorization: Bearer <token>`` on every HTTP request and on
  the WebSocket handshake.
* The browser UI never keeps the token. It exchanges either the token (pasted
  once) or a one-time login code (``jig ui``) for an HttpOnly, SameSite=Strict
  session cookie signed with the token, so rotating the token signs every
  browser out. Requests authenticated by cookie must come from Jig's own
  origin: unsafe methods and WebSocket handshakes with any other (or no)
  ``Origin`` header are refused.
* ``/health`` (``{"status": "ok"}`` only), the UI's static files and the
  session routes (``GET`` says only whether the caller is signed in; ``POST``
  checks the token or code itself) are the only routes that need no credentials.
* Other devices reach Jig only through ``tailscale serve`` (see ``jig.remote``). Every request is first
  classified as local or tailnet; a tailnet request must provably come from tailscaled, from an
  allowed Tailscale login, and carry a paired device session (``jig.devices``). The master token and
  browser sign-in are refused over the tailnet. Cookies get the Secure flag there, and the only Origin
  accepted is the recorded ``https://<machine>.<tailnet>.ts.net``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any

from .devices import DEVICE_COOKIE, DeviceStore
from .errors import JigError
from .remote import TAILSCALE_HEADERS, Refused, RemoteAccess, RemoteError, RequestSource

TOKEN_FILENAME = "api-token"
SESSION_COOKIE = "jig_session"
SESSION_TTL_S = 12 * 3600
LOGIN_CODE_TTL_S = 120

# GET /auth/session only reports whether the caller is signed in, so the UI can check without a 401.
# POST /auth/pair checks the pairing code itself.
PUBLIC_EXACT = {("GET", "/health"), ("GET", "/"), ("GET", "/auth/session"), ("POST", "/auth/session"),
                ("POST", "/auth/pair"), ("GET", "/favicon.ico")}
PUBLIC_PREFIXES = ("/web/", "/avatar/")
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class TokenFileError(JigError):
    """The token file could not be created, secured or read. Jig does not fall back to running without it."""


# Windows file security ---------------------------------------------------------------------------

def _win_current_user_sid() -> str:
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD)]
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):  # TOKEN_QUERY
        raise TokenFileError(f"OpenProcessToken failed (Windows error {ctypes.get_last_error()})")
    try:
        size = wintypes.DWORD()
        advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))  # TokenUser
        buf = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(token, 1, buf, size, ctypes.byref(size)):
            raise TokenFileError(f"GetTokenInformation failed (Windows error {ctypes.get_last_error()})")
        psid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]  # TOKEN_USER.User.Sid
        return _win_sid_string(psid)
    finally:
        kernel32.CloseHandle(token)


def _win_sid_string(psid: int) -> str:
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    out = wintypes.LPWSTR()
    if not advapi32.ConvertSidToStringSidW(psid, ctypes.byref(out)):
        raise TokenFileError(f"ConvertSidToStringSidW failed (Windows error {ctypes.get_last_error()})")
    try:
        return out.value
    finally:
        kernel32.LocalFree(out)


def _win_owner_only(path: Path) -> list[str]:
    """Return the problems with ``path``'s DACL; empty when only the current user has access."""
    import ctypes
    from ctypes import wintypes

    class ACL(ctypes.Structure):
        _fields_ = [("AclRevision", ctypes.c_ubyte), ("Sbz1", ctypes.c_ubyte), ("AclSize", ctypes.c_ushort),
                    ("AceCount", ctypes.c_ushort), ("Sbz2", ctypes.c_ushort)]

    class ACE(ctypes.Structure):  # ACCESS_ALLOWED_ACE / ACCESS_DENIED_ACE; the SID starts at SidStart
        _fields_ = [("AceType", ctypes.c_ubyte), ("AceFlags", ctypes.c_ubyte), ("AceSize", ctypes.c_ushort),
                    ("Mask", wintypes.DWORD), ("SidStart", wintypes.DWORD)]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.GetNamedSecurityInfoW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
                                               ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                               ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.GetSecurityDescriptorControl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.WORD),
                                                      ctypes.POINTER(wintypes.DWORD)]
    dacl, sd = ctypes.c_void_p(), ctypes.c_void_p()
    err = advapi32.GetNamedSecurityInfoW(str(path), 1, 0x4, None, None, ctypes.byref(dacl), None,  # SE_FILE_OBJECT, DACL
                                         ctypes.byref(sd))
    if err:
        raise TokenFileError(f"cannot read the security descriptor of {path} (Windows error {err})")
    try:
        if not dacl.value:
            return ["the file has no DACL, so everyone has access"]
        problems = []
        control, revision = wintypes.WORD(), wintypes.DWORD()
        if not advapi32.GetSecurityDescriptorControl(sd, ctypes.byref(control), ctypes.byref(revision)):
            raise TokenFileError(f"GetSecurityDescriptorControl failed (Windows error {ctypes.get_last_error()})")
        if not control.value & 0x1000:  # SE_DACL_PROTECTED
            problems.append("the DACL still inherits entries from the folder")
        me = _win_current_user_sid()
        count = ctypes.cast(dacl, ctypes.POINTER(ACL))[0].AceCount
        if count == 0:
            problems.append("the DACL is empty, so nobody (including you) can read it")
        for i in range(count):
            p = ctypes.c_void_p()
            if not advapi32.GetAce(dacl, i, ctypes.byref(p)):
                raise TokenFileError(f"GetAce failed (Windows error {ctypes.get_last_error()})")
            ace = ctypes.cast(p, ctypes.POINTER(ACE))[0]
            sid = _win_sid_string(p.value + ACE.SidStart.offset)
            if ace.AceType == 0 and sid != me:  # ACCESS_ALLOWED_ACE_TYPE for someone else
                problems.append(f"{sid} is granted access")
            elif ace.AceType not in (0, 1):  # anything other than allow / deny entries
                problems.append(f"unexpected ACE type {ace.AceType} for {sid}")
        return problems
    finally:
        kernel32.LocalFree(sd)


def _restrict(path: Path) -> None:
    """Make ``path`` readable and writable by the current user only, and verify it. Raises on failure."""
    if sys.platform == "win32":
        sid = _win_current_user_sid()
        proc = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:F"],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            raise TokenFileError(f"icacls could not restrict {path} (exit {proc.returncode}): "
                                 f"{(proc.stdout + proc.stderr).strip()}")
    else:
        os.chmod(path, 0o600)
    check_private(path)


def check_private(path: Path) -> None:
    if sys.platform == "win32":
        problems = _win_owner_only(path)
    else:
        mode = path.stat().st_mode & 0o777
        problems = [f"mode is {oct(mode)}; group and others must have no access"] if mode & 0o077 else []
    if problems:
        raise TokenFileError(f"the token file {path} is not private: {'; '.join(problems)}. "
                             "Run 'jig token rotate' to recreate it with safe permissions.")


# Token store -----------------------------------------------------------------------------------

class TokenStore:
    """The long-lived API token in ``<data_dir>/api-token``. Reloaded when the file changes, so
    ``jig token rotate`` takes effect on a running server without a restart."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / TOKEN_FILENAME
        self._cache: tuple[int, str] | None = None

    def _write_new(self, target: Path) -> str:
        token = secrets.token_urlsafe(32)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.{secrets.token_hex(4)}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        try:
            _restrict(tmp)  # secure the empty file before the secret is written into it
            tmp.write_text(token + "\n", encoding="ascii")
            os.replace(tmp, target)  # a rename keeps the restricted ACL
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        check_private(target)
        self._cache = None
        return token

    def ensure(self) -> str:
        """Return the token, creating it on first run. Fails loudly if it cannot be kept private."""
        if not self.path.exists():
            self._write_new(self.path)
        return self.get()

    def get(self) -> str:
        try:
            mtime = self.path.stat().st_mtime_ns
        except FileNotFoundError as exc:
            raise TokenFileError(f"the API token file {self.path} does not exist; start 'jig serve' or run "
                                 "'jig token show' to create it") from exc
        if self._cache and self._cache[0] == mtime:
            return self._cache[1]
        check_private(self.path)
        token = self.path.read_text(encoding="ascii").strip()
        if len(token) < 32:
            raise TokenFileError(f"the API token in {self.path} is malformed; run 'jig token rotate'")
        self._cache = (mtime, token)
        return token

    def rotate(self) -> str:
        return self._write_new(self.path)


# Sessions and login codes ----------------------------------------------------------------------

def _sign(token: str, message: str) -> str:
    return hmac.new(token.encode(), message.encode(), hashlib.sha256).hexdigest()


@dataclass
class Principal:
    via: str  # bearer | cookie | device
    device: dict[str, Any] | None = None


LOCAL_SOURCE = RequestSource("local", origin="", secure=False)


class Auth:
    def __init__(self, tokens: TokenStore, devices: DeviceStore | None = None):
        self.tokens = tokens
        self.devices = devices
        self._codes: dict[str, float] = {}

    # Session cookies are signed with the token, so rotating it invalidates every session.
    def new_session(self) -> tuple[str, int]:
        expires = int(time.time()) + SESSION_TTL_S
        return f"{expires}.{_sign(self.tokens.get(), f'session:{expires}')}", SESSION_TTL_S

    def session_valid(self, value: str) -> bool:
        expires, _, sig = value.partition(".")
        if not expires.isdigit() or int(expires) < time.time():
            return False
        return hmac.compare_digest(sig, _sign(self.tokens.get(), f"session:{expires}"))

    def token_valid(self, candidate: str) -> bool:
        return hmac.compare_digest(candidate.encode(), self.tokens.get().encode())

    def new_login_code(self) -> tuple[str, int]:
        now = time.time()
        self._codes = {c: exp for c, exp in self._codes.items() if exp > now}
        code = secrets.token_urlsafe(24)
        self._codes[code] = now + LOGIN_CODE_TTL_S
        return code, LOGIN_CODE_TTL_S

    def redeem_login_code(self, code: str) -> bool:
        """One use only, and only within its lifetime."""
        expires = self._codes.pop(code, None)
        return expires is not None and expires > time.time()

    def authenticate(self, headers: dict[str, str], source: RequestSource = LOCAL_SOURCE) -> Principal | None:
        """Over the tailnet only a paired device session counts; locally, the token, a browser session
        or a device session. Raises Refused for the master token over the tailnet."""
        authz = headers.get("authorization", "")
        scheme, _, value = authz.partition(" ")
        if scheme.lower() == "bearer" and value:
            if source.kind == "tailnet":
                raise Refused(403, "Refused: the master API token is not accepted over the tailnet. Pair this device "
                                   "instead: Settings > Use Jig from your other devices > Add a device, on the host.")
            if self.token_valid(value.strip()):
                return Principal("bearer")
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get("cookie", ""))
        except Exception:
            return None
        if source.kind == "local" and SESSION_COOKIE in cookie and self.session_valid(cookie[SESSION_COOKIE].value):
            return Principal("cookie")
        if self.devices is not None and DEVICE_COOKIE in cookie:
            device = self.devices.authenticate(cookie[DEVICE_COOKIE].value, tailscale_login=source.login)
            if device is not None:
                return Principal("device", device)
        return None


def is_public(method: str, path: str) -> bool:
    return (method, path) in PUBLIC_EXACT or (method in SAFE_METHODS and path.startswith(PUBLIC_PREFIXES))


def _cookie(name: str, value: str, max_age: int, secure: bool) -> str:
    return f"{name}={value}; HttpOnly; SameSite=Strict; Path=/; Max-Age={max_age}" + ("; Secure" if secure else "")


def session_cookie_header(value: str, max_age: int, *, secure: bool = False) -> str:
    return _cookie(SESSION_COOKIE, value, max_age, secure)


def device_cookie_header(value: str, max_age: int, *, secure: bool = False) -> str:
    return _cookie(DEVICE_COOKIE, value, max_age, secure)


def clear_cookie_headers(*, secure: bool = False) -> list[str]:
    return [_cookie(SESSION_COOKIE, "", 0, secure), _cookie(DEVICE_COOKIE, "", 0, secure)]


class AuthMiddleware:
    """Pure ASGI middleware, so it covers HTTP, the SSE stream and the WebSocket handshake alike."""

    def __init__(self, app: Any, auth: Auth, remote: RemoteAccess):
        self.app = app
        self.auth = auth
        self.remote = remote

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        method = scope.get("method", "GET") if scope["type"] == "http" else "WEBSOCKET"
        try:
            source = self.remote.classify(scope, headers)
        except Refused as exc:
            await self._reject(scope, receive, send, exc.status, exc.message)
            return
        except RemoteError as exc:
            await self._reject(scope, receive, send, 500, str(exc))
            return
        if source.kind == "local":
            # Only tailscaled sets these, so on a local connection they are forged; drop them for every route.
            for name in TAILSCALE_HEADERS:
                headers.pop(name, None)
            scope["headers"] = [(k, v) for k, v in scope.get("headers", [])
                                if k.decode("latin-1").lower() not in TAILSCALE_HEADERS]
        state = scope.setdefault("state", {})
        state["source"] = source
        if scope["type"] == "http" and is_public(method, scope["path"]):
            await self.app(scope, receive, send)
            return
        try:
            principal = self.auth.authenticate(headers, source)
        except TokenFileError as exc:
            await self._reject(scope, receive, send, 500, str(exc))
            return
        except Refused as exc:
            await self._reject(scope, receive, send, exc.status, exc.message)
            return
        if principal is None:
            await self._reject(scope, receive, send, 401, "authentication required: send 'Authorization: Bearer "
                               "<token>' (see 'jig token show') or sign in to the web UI" if source.kind == "local"
                               else "authentication required: pair this device first (Settings > Use Jig from your "
                               "other devices > Add a device, on the host)")
            return
        if principal.via != "bearer" and method not in SAFE_METHODS and headers.get("origin") != source.origin:
            await self._reject(scope, receive, send, 403, "cross-origin request refused")
            return
        state["auth_via"] = principal.via
        state["principal"] = principal
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(scope: dict[str, Any], receive: Any, send: Any, status: int, message: str) -> None:
        if scope["type"] == "websocket":
            await receive()  # websocket.connect
            await send({"type": "websocket.close", "code": 1008, "reason": message[:120]})
            return
        body = json.dumps({"error": message}).encode()
        headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
        if status == 401:
            headers.append((b"www-authenticate", b'Bearer realm="jig"'))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})
