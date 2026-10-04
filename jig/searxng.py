"""Optional local SearXNG, installed only when the person asks.

SearXNG is a separate program (AGPL-3.0-or-later). Jig does not ship its source. A pinned upstream
commit is downloaded into Jig's data folder, or into a sibling of the checkout when that data folder
sits inside the git repository, and run from its own virtual environment. It listens on 127.0.0.1
only. Jig starts it when a search needs it and stops it when Jig stops. It is not registered to start
at Windows sign-in. A SearXNG that is already running locally is used as it is: Jig does not install
a second copy and does not stop a process it did not start.

Search uses SearXNG's JSON results. If SearXNG is not installed or will not start, the tool says so.
It does not scrape DuckDuckGo or any other engine, and it does not invent results.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import posixpath
import shutil
import socket
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import Request
from pydantic import BaseModel

from .errors import JigError
from .logs import hide_query

log = logging.getLogger("jig.searxng")

# Docker tag 2026.10.2-19ffbcd30, commit 19ffbcd30 (2 Oct 2026). SearXNG publishes no GitHub
# release tags, so the pin is this commit and the SHA-256 of GitHub's source archive of it.
PINNED_VERSION = "2026.10.2-19ffbcd30"
PINNED_COMMIT = "19ffbcd30686e4008392e93de164200510dfb9d8"
PINNED_SHA256 = "ac050643014cf3db1b3ae171a4b21fc4b29c5d9f572255c1a903e51cc2094e59"
PINNED_URL = f"https://github.com/searxng/searxng/archive/{PINNED_COMMIT}.tar.gz"
PINNED_COMMIT_URL = f"https://api.github.com/repos/searxng/searxng/commits/{PINNED_COMMIT}"
LICENCE = "AGPL-3.0-or-later"
# The Windows installer ships embeddable Python, which has no venv module and no pip. Pip is fetched
# from this official bootstrap when that is the interpreter creating SearXNG's environment.
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

BIND = "127.0.0.1"
DEFAULT_PORT = 8090
READY_TIMEOUT_S = 90.0
SEARCH_TIMEOUT_S = 30.0
STOP_TIMEOUT_S = 8.0
# Room for the archive, the unpacked tree and the virtual environment.
INSTALL_FREE_BYTES = 400 * 1024 * 1024

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

ABSENT = (
    "Search isn't available. SearXNG isn't installed, and it isn't already running on this computer. "
    "I won't look this up by scraping DuckDuckGo or any other site, and I haven't made up any results. "
    "You can install search in Settings."
)
WILL_NOT_START = (
    "Search isn't available. SearXNG is installed, but it wouldn't start. "
    "I won't look this up by scraping DuckDuckGo or any other site, and I haven't made up any results."
)
TURNED_OFF = (
    "Search is turned off in Settings. I won't look this up by scraping DuckDuckGo or any other site, "
    "and I haven't made up any results."
)
NO_JSON = (
    "Search isn't available. SearXNG is running, but it isn't offering JSON results, so Jig can't read them. "
    "I won't look this up by scraping DuckDuckGo or any other site, and I haven't made up any results."
)


class SearxngError(JigError):
    """The install cannot continue. The message says why, in plain words, and nothing was left half-done
    unless a previous install was already in place."""


class SearxngUnavailable(JigError):
    """Search cannot answer. The message is what the person (and the model) should be told."""


def _pin_fetch_error(detail: str) -> SearxngError:
    return SearxngError(
        f"Couldn't fetch the pinned SearXNG release {PINNED_VERSION}: {detail} Nothing was installed."
    )


def install_dir(data_dir: Path) -> Path:
    """Where this data folder's SearXNG lives. Inside the data folder when that folder is outside the
    Jig checkout; otherwise a sibling of the checkout, so the virtual environment is never in git."""
    data = Path(data_dir).resolve()
    repo = Path(__file__).resolve().parents[1]
    try:
        data.relative_to(repo)
    except ValueError:
        return data / "searxng"
    digest = hashlib.sha256(os.path.normcase(str(data)).encode()).hexdigest()[:12]
    return repo.parent / "jig-searxng" / digest


def looks_like_searxng(payload: Any) -> bool:
    """Whether ``GET /config`` JSON is SearXNG's, not some other program on the port."""
    if not isinstance(payload, dict):
        return False
    brand = payload.get("brand")
    engines = payload.get("engines")
    if not isinstance(brand, dict) or not isinstance(engines, list):
        return False
    mark = " ".join(str(brand.get(key) or "") for key in ("GIT_URL", "DOCS_URL")).lower()
    return "searxng" in mark


def _port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.4)
        try:
            sock.connect((BIND, port))
        except OSError:
            return False
    return True


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind((BIND, 0))
        return int(sock.getsockname()[1])


def _process_image(pid: int) -> str | None:
    """The executable path of ``pid``, or None if it cannot be read."""
    if pid <= 0:
        return None
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            size = wintypes.DWORD(32768)
            buf = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return None
            return buf.value
        finally:
            kernel32.CloseHandle(handle)
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return None


def _keep_private(path: Path) -> None:
    """Best-effort: the settings file holds SearXNG's secret key, so only this user should read it."""
    if sys.platform == "win32":
        try:
            from .auth import _restrict

            _restrict(path)
            return
        except (OSError, JigError) as exc:
            log.warning("could not restrict SearXNG settings (%s)", type(exc).__name__)
            return
    os.chmod(path, 0o600)


def _python_for_venv() -> Path:
    """The Python that can create a virtual environment. ``jig.exe`` sits beside ``python.exe``."""
    exe = Path(sys.executable)
    name = exe.name.lower()
    if name.startswith("python"):
        return exe
    sibling = exe.with_name("python.exe" if sys.platform == "win32" else "python")
    if sibling.is_file():
        return sibling
    raise SearxngError(
        "Couldn't install SearXNG: Jig couldn't find Python to create its own environment. Nothing was installed."
    )


def _can_import_venv(python: Path) -> bool:
    """Whether ``python`` can run ``python -m venv``. The Windows embeddable build cannot."""
    flags = {"creationflags": _NO_WINDOW} if sys.platform == "win32" else {}
    try:
        proc = subprocess.run([str(python), "-s", "-c", "import venv"], capture_output=True, timeout=60, **flags)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _create_embedded_venv(python: Path, venv: Path) -> None:
    """A separate interpreter for Windows embeddable Python, which ships without ``venv`` or pip.

    The interpreter and its DLL are copied into ``venv``. A path file points that copy at the
    embeddable standard library and at this environment's own site-packages, not at Jig's packages.
    Nothing in Jig's install folder is changed. Windows will not load some other ``python3.dll``
    from ``PATH``, because the matching DLL sits beside the copied ``python.exe``.
    """
    base = python.resolve().parent
    zips = sorted(path for path in base.glob("python3*.zip") if path.is_file())
    if len(zips) != 1:
        raise SearxngError(
            "Couldn't install SearXNG: this Python has no venv module, and its standard library could not be found. "
            "Nothing was installed."
        )
    tag = zips[0].stem
    dll = base / f"{tag}.dll"
    if not dll.is_file() or not (base / "python.exe").is_file():
        raise SearxngError(
            "Couldn't install SearXNG: this Python has no venv module, and its interpreter files could not be found. "
            "Nothing was installed."
        )
    scripts = venv / "Scripts"
    site = venv / "Lib" / "site-packages"
    scripts.mkdir(parents=True)
    site.mkdir(parents=True)
    for name in ("python.exe", "pythonw.exe", dll.name, "python3.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        src = base / name
        if src.is_file():
            shutil.copy2(src, scripts / name)
    if not (scripts / "python.exe").is_file() or not (scripts / dll.name).is_file():
        raise SearxngError(
            "Couldn't install SearXNG: Jig couldn't copy Python into SearXNG's environment. Nothing was installed."
        )
    lines = [str(zips[0]), str(base), str(site), "import site"]
    (scripts / f"{tag}._pth").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _unpack_error(archive: Path, reason: str) -> SearxngError:
    return SearxngError(f"Couldn't unpack SearXNG: {archive.name} {reason} Nothing was installed.")


def _absolute_archive_path(name: str) -> bool:
    """True for a POSIX absolute path, a UNC path, or a Windows drive path. Tar names use ``/``."""
    text = name.replace("\\", "/")
    if text.startswith("/"):
        return True
    return len(text) >= 2 and text[1] == ":"


def _archive_parts(archive: Path, name: str) -> list[str]:
    """Lexical parts of an archive member name. Raises when the name is absolute or climbs out."""
    if _absolute_archive_path(name) or "\x00" in name:
        raise _unpack_error(archive, "has a file outside its folder.")
    parts: list[str] = []
    for part in name.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise _unpack_error(archive, "has a file outside its folder.")
        parts.append(part)
    if not parts:
        raise _unpack_error(archive, "has a file outside its folder.")
    return parts


def _link_target_parts(archive: Path, member: tarfile.TarInfo) -> list[str]:
    """Where a symlink or hardlink points, as parts inside the archive. Outside targets are refused.

    Symlink targets are relative to the link's own directory. Hardlink targets name another member.
    Absolute targets are refused even when the absolute path would land inside the folder.
    """
    link = member.linkname.replace("\\", "/").rstrip("/")
    if not link or _absolute_archive_path(link) or "\x00" in member.linkname:
        raise _unpack_error(archive, "contains a link to an absolute path.")
    if member.issym():
        base = posixpath.dirname(member.name.replace("\\", "/"))
        combined = posixpath.normpath(posixpath.join(base, link))
    else:
        combined = posixpath.normpath(link)
    if _absolute_archive_path(combined) or combined == ".." or combined.startswith("../"):
        raise _unpack_error(archive, "contains a link that points outside its folder.")
    return _archive_parts(archive, combined)


def _materialise_links(archive: Path, dest: Path, links: list[tarfile.TarInfo]) -> None:
    """Copy each in-folder link's target into place. Windows symlinks need privileges, so the
    unpacked tree holds real files and folders. A link is never left as a link."""
    pending = list(links)
    for _ in range(len(links) + 1):
        if not pending:
            return
        still: list[tarfile.TarInfo] = []
        for member in pending:
            link_path = dest.joinpath(*_archive_parts(archive, member.name))
            target = dest.joinpath(*_link_target_parts(archive, member))
            # Copying a folder onto a path inside itself would loop. That is refused with the
            # outside-link error: the link does not name a separate file in the archive.
            if target == link_path or target in link_path.parents:
                raise _unpack_error(archive, "contains a link that points outside its folder.")
            if link_path.exists():
                raise _unpack_error(archive, "contains a link that points outside its folder.")
            if not target.exists():
                still.append(member)
                continue
            link_path.parent.mkdir(parents=True, exist_ok=True)
            if target.is_dir():
                shutil.copytree(target, link_path)
            elif target.is_file():
                shutil.copyfile(target, link_path)
            else:
                raise _unpack_error(archive, "contains a device or special file.")
        if len(still) == len(pending):
            raise _unpack_error(archive, "contains a link whose target is not in the download.")
        pending = still


def _safe_extract(archive: Path, dest: Path) -> Path:
    """Unpack one top-level folder.

    A symlink or hardlink whose target stays inside ``dest`` is copied into place (materialised).
    Links that point outside, absolute paths, names that climb with ``..``, and device files are
    refused before anything is written.
    """
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        regular: list[tarfile.TarInfo] = []
        links: list[tarfile.TarInfo] = []
        for member in tar.getmembers():
            _archive_parts(archive, member.name)
            if member.issym() or member.islnk():
                _link_target_parts(archive, member)
                links.append(member)
            elif member.isdir() or member.isreg():
                regular.append(member)
            else:
                raise _unpack_error(archive, "contains a device or special file.")
        try:
            if hasattr(tarfile, "data_filter"):
                tar.extractall(dest, members=regular, filter="data")
            else:
                tar.extractall(dest, members=regular)
        except (tarfile.TarError, OSError):
            raise _unpack_error(archive, "could not be unpacked.") from None
        _materialise_links(archive, dest, links)
    tops = [path for path in dest.iterdir() if path.is_dir()]
    if len(tops) != 1:
        raise SearxngError("Couldn't unpack SearXNG: the download doesn't hold a single folder. Nothing was installed.")
    return tops[0]


def _engine_names(raw: Any) -> list[str]:
    names: list[str] = []
    if not isinstance(raw, list):
        return names
    for item in raw:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, (list, tuple)) and item and isinstance(item[0], str):
            names.append(item[0])
        elif isinstance(item, dict) and isinstance(item.get("engine"), str):
            names.append(item["engine"])
    return names[:20]


def _result_rows(raw: Any, limit: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return rows
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        title = item.get("title") if isinstance(item.get("title"), str) else ""
        snippet = item.get("content") if isinstance(item.get("content"), str) else ""
        engine = item.get("engine") if isinstance(item.get("engine"), str) else ""
        rows.append({"title": title, "url": url, "snippet": snippet[:500], "engine": engine})
        if len(rows) >= limit:
            break
    return rows


def _answers(raw: Any) -> list[str]:
    found: list[str] = []
    if not isinstance(raw, list):
        return found
    for item in raw:
        text = item if isinstance(item, str) else item.get("answer") if isinstance(item, dict) else None
        if isinstance(text, str) and text.strip():
            found.append(text.strip()[:500])
        if len(found) >= 3:
            break
    return found


async def lookup_pinned_commit(client: httpx.AsyncClient | None = None) -> dict[str, str]:
    """The pinned commit on GitHub. Raises ``SearxngError`` with a plain reason when it cannot be fetched."""
    own = client is None
    if client is None:
        client = httpx.AsyncClient(follow_redirects=True, trust_env=False, timeout=httpx.Timeout(30, connect=15),
                                   headers={"User-Agent": "Jig", "Accept": "application/vnd.github+json"})
    try:
        try:
            response = await client.get(PINNED_COMMIT_URL)
        except httpx.HTTPError as exc:
            raise _pin_fetch_error(f"the network request failed ({type(exc).__name__}).") from None
        if response.status_code != 200:
            raise _pin_fetch_error(f"GitHub returned HTTP {response.status_code}.")
        try:
            sha = response.json().get("sha")
        except ValueError:
            raise _pin_fetch_error("GitHub's reply was not JSON.") from None
        if sha != PINNED_COMMIT:
            raise _pin_fetch_error("GitHub's commit does not match the pin.")
        return {"version": PINNED_VERSION, "commit": PINNED_COMMIT, "sha256": PINNED_SHA256, "licence": LICENCE}
    finally:
        if own:
            await client.aclose()


class Searxng:
    """One data folder's SearXNG: detect, install, start on demand, stop only what Jig started."""

    def __init__(self, data_dir: Path, *, audit: Any = None, discover_port: int | None = DEFAULT_PORT,
                 release_url: str | None = None):
        self.data_dir = Path(data_dir)
        self.root = install_dir(self.data_dir)
        self.audit = audit
        self.discover_port = discover_port
        self.release_url = release_url or PINNED_URL
        self.install_state: dict[str, Any] = {"status": "idle"}
        self._task: asyncio.Task[None] | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._tail: list[str] = []
        self._http: httpx.AsyncClient | None = None

    # Files --------------------------------------------------------------------------------------------------
    @property
    def _state_path(self) -> Path:
        return self.root / "state.json"

    @property
    def _pid_path(self) -> Path:
        return self.root / "jig.pid"

    @property
    def _settings_path(self) -> Path:
        return self.root / "settings.yml"

    @property
    def _src(self) -> Path:
        return self.root / "src"

    @property
    def _venv_python(self) -> Path:
        folder = "Scripts" if sys.platform == "win32" else "bin"
        name = "python.exe" if sys.platform == "win32" else "python"
        return self.root / "venv" / folder / name

    def _read_state(self) -> dict[str, Any]:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_state(self, data: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self._state_path)

    def enabled(self) -> bool:
        state = self._read_state()
        if "enabled" not in state:
            return True
        return bool(state["enabled"])

    def managed_installed(self) -> bool:
        state = self._read_state()
        return state.get("origin") == "managed" and self._venv_python.is_file() and self._src.is_dir()

    def _record(self, kind: str, summary: str, *, actor: str = "runtime", **data: Any) -> None:
        if self.audit is not None:
            self.audit.record(kind, summary, actor=actor, **data)
        log.info("%s", summary)

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(trust_env=False, follow_redirects=False,
                                           timeout=httpx.Timeout(SEARCH_TIMEOUT_S, connect=5))
        return self._http

    # Recognising a running SearXNG --------------------------------------------------------------------------
    async def is_searxng(self, port: int) -> bool:
        url = f"http://{BIND}:{port}/config"
        try:
            response = await self._client().get(url)
        except httpx.HTTPError:
            return False
        if response.status_code != 200:
            return False
        try:
            return looks_like_searxng(response.json())
        except ValueError:
            return False

    async def _existing_port(self) -> int | None:
        if self.discover_port is None:
            return None
        if await self.is_searxng(self.discover_port):
            return self.discover_port
        return None

    def _base(self, port: int) -> str:
        return f"http://{BIND}:{port}"

    # Status -------------------------------------------------------------------------------------------------
    async def status(self) -> dict[str, Any]:
        state = self._read_state()
        existing = await self._existing_port()
        managed = self.managed_installed()
        port = existing or (state.get("port") if isinstance(state.get("port"), int) else None)
        if existing is not None:
            summary = (f"SearXNG is already running on this computer at {BIND}:{existing}. Jig will use it. "
                       "Jig did not install it and will not stop it.")
            using = "existing"
        elif managed and self.enabled():
            remembered = state.get("port")
            summary = ("Search is installed. Jig will start it when you look something up, and stop it when Jig stops. "
                       "It does not start when you sign in to Windows.")
            using = "installed"
            port = remembered if isinstance(remembered, int) else port
        elif managed:
            summary = "Search is installed, and turned off in Settings."
            using = "installed"
        elif not self.enabled():
            summary = "Search is turned off in Settings."
            using = "none"
        else:
            summary = "Search isn't installed."
            using = "none"
        install = dict(self.install_state)
        if install.get("status") == "running" and install.get("step"):
            summary = str(install["step"])
        return {
            "enabled": self.enabled(),
            "installed": managed,
            "existing": existing is not None,
            "using": using,
            "summary": summary,
            "folder": str(self.root) if managed else None,
            "address": f"{BIND}:{port}" if port and using != "none" else None,
            "port": port,
            "version": PINNED_VERSION if managed else None,
            "licence": LICENCE,
            "starts_at_login": False,
            "install": install,
        }

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        state = self._read_state()
        state["enabled"] = bool(enabled)
        self._write_state(state)
        self._record("search.enabled" if enabled else "search.disabled",
                     "search turned on" if enabled else "search turned off", actor="user", enabled=bool(enabled))
        return {"enabled": bool(enabled)}

    # Install ------------------------------------------------------------------------------------------------
    def start_install(self) -> dict[str, Any]:
        if self._task is not None and not self._task.done():
            return self.install_state
        self.install_state = {"status": "running", "step": "Checking whether SearXNG is already running on this computer"}
        self._task = asyncio.get_running_loop().create_task(self._install_task())
        return self.install_state

    async def _install_task(self) -> None:
        try:
            result = await self.install()
            self.install_state = {"status": "done", **result}
        except asyncio.CancelledError:
            self.install_state = {"status": "idle"}
            raise
        except SearxngError as exc:
            self.install_state = {"status": "failed", "error": str(exc)}
            self._record("search.install_failed", "SearXNG install failed", actor="user",
                         error_type=type(exc).__name__, error_chars=len(str(exc)))
        except Exception as exc:
            self.install_state = {"status": "failed",
                                 "error": f"Couldn't install SearXNG ({type(exc).__name__}). Nothing was installed."}
            self._record("search.install_failed", "SearXNG install failed", actor="user",
                         error_type=type(exc).__name__, error_chars=len(str(exc)))

    async def install(self) -> dict[str, Any]:
        """Download the pinned release, or adopt a SearXNG that is already running. Raises ``SearxngError``
        with a plain reason when the pin cannot be fetched. Does not register a Windows sign-in task."""
        existing = await self._existing_port()
        if existing is not None:
            self._write_state({**self._read_state(), "enabled": True, "origin": "existing", "port": existing})
            self._record("search.using_existing", f"using the SearXNG already running on {BIND}:{existing}",
                         actor="user", port=existing)
            return {"using": "existing", "port": existing,
                    "summary": (f"SearXNG is already running on this computer at {BIND}:{existing}. "
                                "Jig will use it, and has not installed a second copy.")}
        if self.managed_installed():
            return {"using": "installed", "already": True, "summary": "Search is already installed."}
        if self.release_url == PINNED_URL:
            self._step("Asking GitHub for the pinned SearXNG release")
            await lookup_pinned_commit()
        had = self.managed_installed()
        try:
            await self._download_into_place()
        except asyncio.CancelledError:
            if not had:
                shutil.rmtree(self.root, ignore_errors=True)
            raise
        except SearxngError:
            if not had:
                shutil.rmtree(self.root, ignore_errors=True)
            raise
        result = {"using": "installed", "port": self._read_state().get("port"), "version": PINNED_VERSION,
                  "summary": "Search is installed. Jig will start it when you look something up."}
        self.install_state = {"status": "done", **result}
        return result

    def _step(self, text: str) -> None:
        self.install_state = {"status": "running", "step": text}
        echo = getattr(self, "echo", None)
        if echo is not None:
            echo(text)

    async def _download_into_place(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(self.root).free
        if free < INSTALL_FREE_BYTES:
            raise SearxngError(f"Couldn't install SearXNG: this needs about {INSTALL_FREE_BYTES // 2**20} MB free, "
                               f"and {free // 2**20} MB is free. Nothing was installed.")
        self._step(f"Downloading SearXNG {PINNED_VERSION}")
        archive = await self._fetch_archive()
        self._step("Unpacking SearXNG")
        staging = self.root / ".unpack"
        shutil.rmtree(staging, ignore_errors=True)
        try:
            extracted = await asyncio.to_thread(_safe_extract, archive, staging)
            src = self._src
            if src.exists():
                shutil.rmtree(src)
            extracted.rename(src)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            archive.unlink(missing_ok=True)
        port = await self._choose_port()
        self._write_settings(port)
        self._step("Creating SearXNG's own Python environment")
        await self._create_venv()
        self._write_launcher()
        self._step("Checking that SearXNG starts")
        await self._verify(port)
        self._write_state({
            "enabled": True, "origin": "managed", "port": port, "version": PINNED_VERSION,
            "commit": PINNED_COMMIT, "sha256": PINNED_SHA256, "licence": LICENCE,
        })
        self._record("search.installed", f"installed SearXNG {PINNED_VERSION} on {BIND}:{port}", actor="user",
                     version=PINNED_VERSION, commit=PINNED_COMMIT, sha256=PINNED_SHA256, port=port, licence=LICENCE)

    async def _choose_port(self) -> int:
        """8090 when it is free. Another free local port when that port is taken.
        An existing SearXNG is adopted before install reaches here, so this never binds a second copy onto it."""
        if self.discover_port is not None and not _port_open(self.discover_port):
            return self.discover_port
        return _free_port()

    async def _fetch_archive(self) -> Path:
        folder = self.root / ".download"
        folder.mkdir(parents=True, exist_ok=True)
        partial = folder / "searxng.tar.gz.part"
        digest = hashlib.sha256()
        try:
            async with httpx.AsyncClient(follow_redirects=True, trust_env=False,
                                         timeout=httpx.Timeout(120, connect=20),
                                         headers={"User-Agent": "Jig"}) as http:
                try:
                    async with http.stream("GET", self.release_url) as response:
                        if response.status_code != 200:
                            raise _pin_fetch_error(f"the download returned HTTP {response.status_code}.")
                        with partial.open("wb") as fh:
                            async for chunk in response.aiter_bytes(1 << 16):
                                fh.write(chunk)
                                digest.update(chunk)
                except httpx.HTTPError as exc:
                    raise _pin_fetch_error(f"the network request failed ({type(exc).__name__}).") from None
        except SearxngError:
            partial.unlink(missing_ok=True)
            raise
        if self.release_url == PINNED_URL and digest.hexdigest() != PINNED_SHA256:
            partial.unlink(missing_ok=True)
            raise SearxngError(
                f"Couldn't install SearXNG {PINNED_VERSION}: the download doesn't match the pinned checksum, "
                "so it was deleted. Nothing was installed."
            )
        archive = folder / "searxng.tar.gz"
        partial.replace(archive)
        return archive

    def _write_settings(self, port: int) -> None:
        secret = hashlib.sha256(os.urandom(32)).hexdigest()
        # limiter off, so Valkey/Redis is not required. JSON is on, so Jig can read results. Loopback only.
        text = (
            "use_default_settings: true\n"
            "general:\n"
            "  instance_name: \"Jig search\"\n"
            "  enable_metrics: false\n"
            "server:\n"
            f"  bind_address: \"{BIND}\"\n"
            f"  port: {port}\n"
            "  limiter: false\n"
            "  public_instance: false\n"
            "  image_proxy: false\n"
            f"  secret_key: \"{secret}\"\n"
            "search:\n"
            "  formats:\n"
            "    - html\n"
            "    - json\n"
            "  autocomplete: \"\"\n"
        )
        self._settings_path.write_text(text, encoding="utf-8")
        _keep_private(self._settings_path)

    def _write_launcher(self) -> None:
        # Jig's file, not SearXNG's. On Windows, SearXNG imports the Unix ``pwd`` module while loading,
        # including when the limiter (and so Valkey) is off. A stand-in lets that import succeed.
        launcher = self.root / "launch.py"
        launcher.write_text(
            "\"\"\"Start the unpacked SearXNG. This file is Jig's, not part of SearXNG.\"\"\"\n"
            "import sys\n"
            "import types\n"
            "from pathlib import Path\n"
            "\n"
            "if sys.platform == \"win32\":\n"
            "    pwd = types.ModuleType(\"pwd\")\n"
            "    pwd.getpwuid = lambda uid: types.SimpleNamespace(pw_name=\"jig\", pw_uid=0)\n"
            "    sys.modules.setdefault(\"pwd\", pwd)\n"
            "\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parent / \"src\"))\n"
            "from searx.webapp import run\n"
            "\n"
            "run()\n",
            encoding="utf-8",
        )

    async def _create_venv(self) -> None:
        python = _python_for_venv()
        venv = self.root / "venv"
        if venv.exists():
            shutil.rmtree(venv)
        self._step("Creating SearXNG's own Python environment")
        if await asyncio.to_thread(_can_import_venv, python):
            await self._run_checked([str(python), "-m", "venv", str(venv)], "creating its Python environment")
        else:
            # The installed Jig uses embeddable Python: no venv module, and no pip. Copying the
            # interpreter leaves Jig's own Python untouched.
            await asyncio.to_thread(_create_embedded_venv, python, venv)
            await self._bootstrap_pip()
        pip = [str(self._venv_python), "-s", "-m", "pip", "install", "--disable-pip-version-check",
               "--no-warn-script-location"]
        site = self._embedded_site()
        if site is not None:
            pip.append(f"--target={site}")
        pip.extend(["-r", str(self._src / "requirements.txt")])
        self._step("Installing SearXNG's Python libraries")
        await self._run_checked(pip, "installing its Python libraries")

    def _embedded_site(self) -> Path | None:
        """Site-packages for an embeddable-Python environment, whose prefix is the interpreter folder.
        None when ``python -m venv`` created this environment and pip already knows where to install."""
        scripts = self._venv_python.parent
        if any(scripts.glob("python*._pth")):
            return self.root / "venv" / "Lib" / "site-packages"
        return None

    async def _bootstrap_pip(self) -> None:
        """Install pip into an embeddable-Python environment. A normal virtual environment already has pip."""
        folder = self.root / ".download"
        folder.mkdir(parents=True, exist_ok=True)
        script = folder / "get-pip.py"
        self._step("Downloading pip")
        try:
            async with httpx.AsyncClient(follow_redirects=True, trust_env=False,
                                         timeout=httpx.Timeout(120, connect=20),
                                         headers={"User-Agent": "Jig"}) as http:
                try:
                    response = await http.get(GET_PIP_URL)
                except httpx.HTTPError as exc:
                    raise SearxngError(
                        f"Couldn't install SearXNG: pip could not be downloaded ({type(exc).__name__}). "
                        "Nothing was installed."
                    ) from None
        except SearxngError:
            script.unlink(missing_ok=True)
            raise
        if response.status_code != 200 or not response.content:
            script.unlink(missing_ok=True)
            raise SearxngError(
                f"Couldn't install SearXNG: the pip download returned HTTP {response.status_code}. "
                "Nothing was installed."
            )
        script.write_bytes(response.content)
        site = self.root / "venv" / "Lib" / "site-packages"
        self._step("Installing pip into SearXNG's Python environment")
        try:
            await self._run_checked(
                [str(self._venv_python), "-s", str(script), "--no-warn-script-location", f"--target={site}"],
                "installing pip",
            )
        finally:
            script.unlink(missing_ok=True)

    async def _run_checked(self, argv: list[str], what: str) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=str(self.root), stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                **({"creationflags": _NO_WINDOW} if sys.platform == "win32" else {}),
            )
        except OSError as exc:
            raise SearxngError(f"Couldn't install SearXNG while {what}: {type(exc).__name__}. Nothing was installed.") \
                from None
        out, _ = await proc.communicate()
        if proc.returncode != 0:
            detail = hide_query(out.decode("utf-8", "replace")).strip().splitlines()
            tail = detail[-1][:200] if detail else "no output"
            if "/search" in tail or "q=" in tail.lower():
                tail = "the installer printed a line that was left out"
            raise SearxngError(f"Couldn't install SearXNG while {what} (exit {proc.returncode}): {tail}. "
                               "Nothing was installed.")

    async def _verify(self, port: int) -> None:
        try:
            await self._start_owned(port)
        except SearxngUnavailable:
            raise SearxngError("Couldn't install SearXNG: it did not start. Nothing was left running.") from None
        try:
            if not await self._wait_ready(port):
                raise SearxngError("Couldn't install SearXNG: it did not start. Nothing was left running.")
        finally:
            await self._stop_owned_process()

    # Process -----------------------------------------------------------------------------------------------
    def _owned_pid(self) -> int | None:
        if self._proc is not None and self._proc.returncode is None and self._proc.pid:
            return self._proc.pid
        try:
            text = self._pid_path.read_text(encoding="utf-8").strip().split()
        except OSError:
            return None
        if not text or not text[0].isdigit():
            return None
        pid = int(text[0])
        image = _process_image(pid)
        if image is None:
            return None
        if os.path.normcase(str(Path(image).resolve())) != os.path.normcase(str(self._venv_python.resolve())):
            return None
        return pid

    def _child_env(self, port: int) -> dict[str, str]:
        env = dict(os.environ)
        for key in ("SEARXNG_VALKEY_URL", "SEARXNG_REDIS_URL"):
            env.pop(key, None)
        secret = ""
        try:
            for line in self._settings_path.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("secret_key:"):
                    secret = line.split(":", 1)[1].strip().strip('"')
        except OSError:
            secret = ""
        env.update({
            "SEARXNG_SETTINGS_PATH": str(self._settings_path),
            "SEARXNG_BIND_ADDRESS": BIND,
            "SEARXNG_PORT": str(port),
            "SEARXNG_LIMITER": "false",
            "SEARXNG_PUBLIC_INSTANCE": "false",
            "SEARXNG_IMAGE_PROXY": "false",
        })
        if secret:
            env["SEARXNG_SECRET"] = secret
        return env

    async def _start_owned(self, port: int) -> None:
        if self._owned_pid() and await self.is_searxng(port):
            return
        if await self.is_searxng(port) and not self._owned_pid():
            # Somebody else's SearXNG is on our port. Do not start a second one, and do not stop theirs.
            return
        argv = [str(self._venv_python), "-s", str(self.root / "launch.py")]
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *argv, cwd=str(self.root), env=self._child_env(port), stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                **({"creationflags": _NO_WINDOW} if sys.platform == "win32" else {}),
            )
        except OSError as exc:
            raise SearxngUnavailable(WILL_NOT_START) from exc
        assert self._proc.stdout is not None
        self._tail = []
        asyncio.get_running_loop().create_task(self._drain(self._proc.stdout))
        self._pid_path.write_text(f"{self._proc.pid} {port}\n", encoding="utf-8")
        self._record("search.started", f"started SearXNG on {BIND}:{port}", pid=self._proc.pid, port=port)

    async def _drain(self, stream: asyncio.StreamReader) -> None:
        while raw := await stream.readline():
            text = hide_query(raw.decode("utf-8", "replace")).strip()
            if not text or "/search" in text or "q=" in text.lower():
                continue
            self._tail.append(text[:240])
            del self._tail[:-15]

    async def _wait_ready(self, port: int) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + READY_TIMEOUT_S
        while loop.time() < deadline:
            if self._proc is not None and self._proc.returncode is not None:
                return False
            if await self.is_searxng(port):
                return True
            await asyncio.sleep(0.4)
        return False

    async def _stop_owned_process(self) -> None:
        """Stop the SearXNG process Jig started. A process Jig did not start is left alone."""
        proc = self._proc
        self._proc = None
        pid = proc.pid if proc is not None and proc.returncode is None else self._owned_pid()
        if proc is not None and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), STOP_TIMEOUT_S)
            except TimeoutError:
                proc.kill()
                await proc.wait()
        elif pid:
            image = _process_image(pid)
            ours = image is not None and (
                os.path.normcase(str(Path(image).resolve())) == os.path.normcase(str(self._venv_python.resolve())))
            if ours:
                argv = ["taskkill", "/PID", str(pid), "/T", "/F"] if sys.platform == "win32" else ["kill", str(pid)]
                flags = {"creationflags": _NO_WINDOW} if sys.platform == "win32" else {}
                await asyncio.to_thread(subprocess.run, argv, capture_output=True, **flags)
        if pid:
            self._pid_path.unlink(missing_ok=True)
            self._record("search.stopped", "stopped the SearXNG Jig started", pid=pid)

    async def ensure_started(self) -> str | None:
        """The base URL to query, starting Jig's copy when it is installed and nothing local is already SearXNG.
        None when search is turned off, not installed, and no local SearXNG is running."""
        if not self.enabled():
            raise SearxngUnavailable(TURNED_OFF)
        async with self._lock:
            existing = await self._existing_port()
            if existing is not None:
                return self._base(existing)
            if not self.managed_installed():
                return None
            port = self._read_state().get("port")
            if not isinstance(port, int):
                port = DEFAULT_PORT if self.discover_port is None else self.discover_port
            if _port_open(port) and not await self.is_searxng(port) and self._owned_pid() is None:
                port = _free_port()
                self._write_settings(port)
                state = self._read_state()
                state["port"] = port
                self._write_state(state)
            try:
                await self._start_owned(port)
            except OSError:
                raise SearxngUnavailable(WILL_NOT_START) from None
            if not await self._wait_ready(port):
                await self._stop_owned_process()
                raise SearxngUnavailable(WILL_NOT_START)
            return self._base(port)

    async def search(self, query: str, *, limit: int = 5) -> dict[str, Any]:
        """JSON results from the local SearXNG. Never scrapes another engine and never invents rows."""
        text = query.strip()
        if not text:
            raise SearxngError("web_search: the query is empty")
        limit = min(max(int(limit), 1), 10)
        base = await self.ensure_started()
        if base is None:
            raise SearxngUnavailable(ABSENT)
        try:
            response = await self._client().get(f"{base}/search", params={"q": text, "format": "json", "language": "en"})
        except httpx.HTTPError:
            raise SearxngUnavailable(WILL_NOT_START) from None
        if response.status_code == 403:
            raise SearxngUnavailable(NO_JSON)
        if response.status_code != 200:
            raise SearxngUnavailable(
                f"Search isn't available. SearXNG answered HTTP {response.status_code}, so there are no results. "
                "I won't look this up by scraping DuckDuckGo or any other site, and I haven't made up any results."
            )
        try:
            payload = response.json()
        except ValueError:
            raise SearxngUnavailable(WILL_NOT_START) from None
        if not isinstance(payload, dict):
            raise SearxngUnavailable(WILL_NOT_START)
        results = _result_rows(payload.get("results"), limit)
        out: dict[str, Any] = {
            "via": "SearXNG on this computer",
            "results": results,
            "unresponsive_engines": _engine_names(payload.get("unresponsive_engines")),
        }
        answers = _answers(payload.get("answers"))
        if answers:
            out["answers"] = answers
        if not results:
            out["note"] = "SearXNG returned no results. Do not invent any."
        return out

    async def remove(self) -> dict[str, Any]:
        """Delete the copy Jig installed. A SearXNG Jig did not install is left running and is not deleted."""
        await self._stop_owned_process()
        managed = self.managed_installed() or self._read_state().get("origin") == "managed"
        if self.root.exists() and (managed or self._venv_python.is_file() or self._src.is_dir()):
            shutil.rmtree(self.root, ignore_errors=True)
            self._record("search.removed", "removed the SearXNG Jig installed", actor="user")
            return {"removed": True, "summary": "Removed the SearXNG Jig installed. A search program that was already "
                    "running on this computer, if there was one, has been left as it is."}
        state = self._read_state()
        if state.get("origin") == "existing":
            self._state_path.unlink(missing_ok=True)
            return {"removed": False, "summary": "Jig did not install SearXNG. It was using one already running on "
                    "this computer, and that program has been left as it is. Search is now turned off until you "
                    "install it or turn it on again."}
        return {"removed": False, "summary": "Search isn't installed, so there was nothing to remove."}

    async def stop(self) -> None:
        """Jig is stopping. Cancel an install still running, and stop only a SearXNG this Jig started."""
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._stop_owned_process()
        if self._http is not None:
            await self._http.aclose()
            self._http = None


# Settings API -----------------------------------------------------------------------------------------------
class _SearchConfirm(BaseModel):
    confirm: bool | None = None


class _SearchUse(_SearchConfirm):
    enabled: bool | None = None


def search_router(jig_of: Any, require_local: Any, require_confirm: Any) -> Any:
    """``/search`` routes. Installing, removing and the on/off switch only work on this computer."""
    from fastapi import APIRouter, HTTPException

    router = APIRouter(tags=["search"])

    def service(request: Any) -> Searxng:
        jig = jig_of(request)
        found = getattr(jig, "searxng", None)
        if found is None:
            raise HTTPException(503, "Search isn't available until Jig has started.")
        return found

    @router.get("/search")
    async def search_status(request: Request) -> dict[str, Any]:
        return await service(request).status()

    @router.post("/search/install")
    async def search_install(request: Request, body: _SearchConfirm) -> dict[str, Any]:
        require_local(request, "Installing search")
        require_confirm(body, "Search was not installed")
        return service(request).start_install()

    @router.post("/search/remove")
    async def search_remove(request: Request, body: _SearchConfirm) -> dict[str, Any]:
        require_local(request, "Removing search")
        require_confirm(body, "Search was not removed")
        return await service(request).remove()

    @router.post("/search/use")
    async def search_use(request: Request, body: _SearchUse) -> dict[str, Any]:
        require_local(request, "Changing search")
        require_confirm(body, "Search was not changed")
        if body.enabled is None:
            raise HTTPException(400, 'Send "enabled": true or false.')
        return service(request).set_enabled(body.enabled)

    return router


# Command line -----------------------------------------------------------------------------------------------
def add_parsers(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser("search", help="install or turn on search (SearXNG), the same as Settings > Search")
    parser.add_argument("action", choices=["status", "install", "remove", "use"],
                        help="status: whether search is installed; install: download the pinned SearXNG "
                             "(asks first); remove: delete the copy Jig installed; use on|off: turn search on or off")
    parser.add_argument("value", nargs="?", help="for 'use': on or off")
    parser.add_argument("--yes", action="store_true", help="confirm install or remove without the y/N prompt")
    parser.add_argument("--data-dir", help="data folder (default: from the config)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")


def _confirmed(yes: bool, question: str) -> bool:
    if yes:
        return True
    if not sys.stdin or not sys.stdin.isatty():
        print("Nothing changed: there is no terminal to confirm on. Re-run with --yes to confirm.", file=sys.stderr)
        return False
    try:
        answer = input(f"{question} [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}


def _api(config: Any) -> tuple[str, dict[str, str]] | None:
    from .auth import TokenStore
    from .instance import running_instance

    info = running_instance(config.data_dir)
    if info is None:
        return None
    host = info.get("host") or BIND
    if host in ("0.0.0.0", "::", ""):
        host = BIND
    base = f"http://{host}:{info.get('port') or config.server.port}"
    return base, {"Authorization": f"Bearer {TokenStore(config.data_dir).get()}"}


def _print_status(body: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(body, indent=2))
        return
    print(body.get("summary") or body.get("error") or "")
    if body.get("folder"):
        print(f"Folder: {body['folder']}")
    if body.get("address"):
        print(f"Address: {body['address']}")


def run(args: argparse.Namespace) -> int:
    from .config import load_config

    config = load_config(args.config, **({"data_dir": args.data_dir} if args.data_dir else {}))
    api = _api(config)
    if api is not None:
        return _run_via_api(args, *api)
    return asyncio.run(_run_local(args, config.data_dir))


def _run_via_api(args: argparse.Namespace, base: str, headers: dict[str, str]) -> int:
    with httpx.Client(trust_env=False, timeout=httpx.Timeout(30, connect=10)) as http:
        if args.action == "status":
            response = http.get(f"{base}/search", headers=headers)
        elif args.action == "use":
            enabled = _use_value(args.value)
            if enabled is None:
                return 2
            if not _confirmed(args.yes, "Turn search on?" if enabled else "Turn search off?"):
                print("Nothing changed.")
                return 0
            response = http.post(f"{base}/search/use", headers=headers, json={"confirm": True, "enabled": enabled})
        elif args.action == "install":
            if not _confirmed(args.yes, "Install search? This downloads SearXNG, a separate program (AGPL), into its "
                              "own folder. It does not start when you sign in. Nothing leaves this machine except the "
                              "searches SearXNG itself sends to the search engines it uses"):
                print("Nothing changed.")
                return 0
            response = http.post(f"{base}/search/install", headers=headers, json={"confirm": True})
            if response.status_code == 200:
                return _poll_install(http, base, headers, args.json)
        else:
            if not _confirmed(args.yes, "Remove the SearXNG Jig installed? A SearXNG that was already running is left "
                              "as it is"):
                print("Nothing changed.")
                return 0
            response = http.post(f"{base}/search/remove", headers=headers, json={"confirm": True})
        return _show_response(response, args.json)


def _poll_install(http: httpx.Client, base: str, headers: dict[str, str], as_json: bool) -> int:
    seen = ""
    for _ in range(900):
        response = http.get(f"{base}/search", headers=headers)
        if response.status_code != 200:
            return _show_response(response, as_json)
        body = response.json()
        install = body.get("install") or {}
        step = str(install.get("step") or install.get("error") or "")
        if step and step != seen and not as_json:
            print(step, flush=True)
            seen = step
        status = install.get("status")
        if status == "idle":
            time.sleep(0.3)
            continue
        if status in {"done", "failed"}:
            _print_status(install if status == "failed" else body, as_json)
            return 0 if status == "done" else 1
        time.sleep(1)
    print("SearXNG is still installing. Settings > Search shows where it got to.", file=sys.stderr)
    return 1


def _show_response(response: httpx.Response, as_json: bool) -> int:
    try:
        body = response.json()
    except ValueError:
        print(response.text[:400], file=sys.stderr)
        return 1
    if response.status_code >= 400:
        print(body.get("error") or body.get("detail") or response.text[:400], file=sys.stderr)
        return 1
    _print_status(body, as_json)
    return 0


def _use_value(value: str | None) -> bool | None:
    if value == "on":
        return True
    if value == "off":
        return False
    print("Use: jig search use on   or   jig search use off", file=sys.stderr)
    return None


async def _run_local(args: argparse.Namespace, data_dir: Path) -> int:
    service = Searxng(data_dir)
    try:
        if args.action == "status":
            _print_status(await service.status(), args.json)
            return 0
        if args.action == "use":
            enabled = _use_value(args.value)
            if enabled is None:
                return 2
            if not _confirmed(args.yes, "Turn search on?" if enabled else "Turn search off?"):
                print("Nothing changed.")
                return 0
            _print_status(service.set_enabled(enabled), args.json)
            return 0
        if args.action == "install":
            if not _confirmed(args.yes, "Install search? This downloads SearXNG, a separate program (AGPL), into its "
                              "own folder. It does not start when you sign in. Nothing leaves this machine except the "
                              "searches SearXNG itself sends to the search engines it uses"):
                print("Nothing changed.")
                return 0
            if not args.json:
                service.echo = print
            try:
                result = await service.install()
            except SearxngError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            _print_status(result, args.json)
            return 0
        if not _confirmed(args.yes, "Remove the SearXNG Jig installed? A SearXNG that was already running is left "
                          "as it is"):
            print("Nothing changed.")
            return 0
        _print_status(await service.remove(), args.json)
        return 0
    finally:
        await service.stop()
