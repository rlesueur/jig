"""Getting Signal ready from Settings, without a terminal: find Java and signal-cli, download them when the
person asks, and link signal-cli to their phone with a QR code shown in Jig.

- Java. signal-cli 0.14 needs Java 25 or newer. Jig looks where Java installers put it (JAVA_HOME, PATH,
  and the usual folders, so a Java installed after Jig started is found too) and asks each ``java -version``.
- Downloading, only when the person chooses it. signal-cli (GPL-3.0) comes from its official GitHub release
  and, if no Java 25 is found, a Java runtime (Eclipse Temurin, GPL-2.0 with the Classpath Exception) from
  Adoptium's API. Each file is checked against the SHA-256 its publisher lists before it is unpacked, and
  both go in ``<data folder>/tools``, used only by Jig. Neither is part of Jig's installer.
- Linking. Jig runs ``signal-cli link -n Jig``, which prints a ``sgnl://linkdevice`` link and waits until the
  phone scans it (Signal > Settings > Linked devices). Jig shows the link as a QR code (made locally) and,
  once signal-cli prints "Associated with: <number>", checks that signal-cli now has that account.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import platform
import re
import shutil
import sys
import tarfile
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx

from ..errors import ConnectorError
from . import signal

log = logging.getLogger("jig.connectors.signal_setup")

JAVA_NEEDED = 25  # signal-cli 0.14's README: "at least Java Runtime Environment (JRE) 25"
RELEASES_API = "https://api.github.com/repos/AsamK/signal-cli/releases/latest"
RELEASES_PAGE = "https://github.com/AsamK/signal-cli/releases"
ADOPTIUM_API = "https://api.adoptium.net/v3/assets/latest/{version}/hotspot"
TEMURIN_PAGE = f"https://adoptium.net/temurin/releases/?version={JAVA_NEEDED}&package=jre"
DEVICE_NAME = "Jig"
LINK_TIMEOUT_S = 600.0  # Jig stops making new codes after this; "Make a new code" starts again
VERSION_TIMEOUT_S = 30.0
EXE = ".exe" if sys.platform == "win32" else ""
SIGNAL_CLI = "signal-cli.bat" if sys.platform == "win32" else "signal-cli"
JAVA_VENDORS = ("Eclipse Adoptium", "Microsoft", "Java", "Zulu", "Amazon Corretto", "BellSoft", "Semeru",
                "OpenJDK")
_LINK_URI = re.compile(r"^sgnl://linkdevice\?\S+$")
_ASSOCIATED = re.compile(r"^Associated with:\s*(\S+)")
_VERSION = re.compile(r'version "(\d+)(?:\.(\d+))?[^"]*"')
_versions: dict[tuple[str, float], tuple[int, str] | None] = {}


def tools_dir(data_dir: Path) -> Path:
    return data_dir / "tools"


# Java -------------------------------------------------------------------------------------------------------
def java_candidates(tools: Path | None) -> list[Path]:
    """Every java Jig might use, in the order it prefers them: its own, JAVA_HOME, PATH, the usual folders."""
    found: list[Path] = []
    if tools is not None:
        found += sorted((tools / "java").glob(f"*/bin/java{EXE}"), reverse=True)
    if os.environ.get("JAVA_HOME"):
        found.append(Path(os.environ["JAVA_HOME"]) / "bin" / f"java{EXE}")
    if on_path := shutil.which("java"):
        found.append(Path(on_path))
    if sys.platform == "win32":
        roots = {os.environ.get(k) for k in ("ProgramFiles", "ProgramW6432", "LOCALAPPDATA")} - {None}
        for root in sorted(roots):  # type: ignore[type-var]
            base = Path(root) / "Programs" if root == os.environ.get("LOCALAPPDATA") else Path(root)
            for vendor in JAVA_VENDORS:
                found += sorted((base / vendor).glob("*/bin/java.exe"), reverse=True)
    elif sys.platform == "darwin":
        found += sorted(Path("/Library/Java/JavaVirtualMachines").glob("*/Contents/Home/bin/java"), reverse=True)
    else:
        found += sorted(Path("/usr/lib/jvm").glob("*/bin/java"), reverse=True)
    seen: set[str] = set()
    out = []
    for p in found:
        key = os.path.normcase(str(p))
        if key not in seen and p.is_file():
            seen.add(key)
            out.append(p)
    return out


def parse_java_version(text: str) -> tuple[int, str] | None:
    """(major, as printed) from ``java -version``; Java 8 prints "1.8.0_x", so its major is 8."""
    m = _VERSION.search(text)
    if not m:
        return None
    major = int(m.group(1))
    if major == 1 and m.group(2):
        major = int(m.group(2))
    return major, m.group(0).split('"')[1]


async def java_version(java: Path) -> tuple[int, str] | None:
    try:
        key = (os.path.normcase(str(java)), java.stat().st_mtime)
    except OSError:
        return None
    if key in _versions:
        return _versions[key]
    try:
        proc = await asyncio.create_subprocess_exec(str(java), "-version", stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), VERSION_TIMEOUT_S)
    except (OSError, TimeoutError):
        return None
    _versions[key] = parse_java_version(err.decode("utf-8", "replace") + out.decode("utf-8", "replace"))
    return _versions[key]


def _home(java: Path) -> str | None:
    """The Java folder for JAVA_HOME, or None for a launcher that isn't in one (Oracle's javapath)."""
    home = java.parent.parent
    return str(home) if (home / "lib").is_dir() else None


async def find_java(tools: Path | None) -> dict[str, Any]:
    """The first Java 25 or newer: {"ok", "path", "home", "version", "managed"}; otherwise what was found."""
    older = []
    for java in java_candidates(tools):
        v = await java_version(java)
        if v is None:
            continue
        managed = tools is not None and (tools / "java") in java.parents
        if v[0] >= JAVA_NEEDED:
            return {"ok": True, "path": str(java), "home": _home(java), "version": v[1], "managed": managed}
        older.append({"path": str(java), "version": v[1]})
    return {"ok": False, "needed": JAVA_NEEDED, "older": older}


def tools_of(binary: str) -> Path | None:
    """Jig's tools folder, if ``binary`` is a signal-cli Jig downloaded."""
    for parent in Path(binary).parents:
        if parent.name == "signal-cli" and parent.parent.name == "tools":
            return parent.parent
    return None


async def java_env(binary: str, tools: Path | None = None) -> dict[str, str]:
    """The environment to run signal-cli in: JAVA_HOME set to a Java 25 that Jig found (or, for a launcher with
    no Java folder, removed so the start script uses PATH). Unchanged if Jig found none: signal-cli then says
    itself what is missing."""
    env = signal._env()
    java = await find_java(tools or tools_of(binary))
    if java["ok"]:
        if java["home"]:
            env["JAVA_HOME"] = java["home"]
        else:
            env.pop("JAVA_HOME", None)
            env["PATH"] = f"{Path(java['path']).parent}{os.pathsep}{env.get('PATH', '')}"
    return env


# Starting signal-cli ----------------------------------------------------------------------------------------
_CLASSPATH = re.compile(r"^set CLASSPATH=(.+?)\s*$", re.M)
_JVM_OPTS = re.compile(r"^set DEFAULT_JVM_OPTS=(.*?)\s*$", re.M)
_MAIN = re.compile(r'-classpath "%CLASSPATH%"\s+(\S+)\s+%\*')


def start_script(binary: str) -> tuple[list[str], list[str], str] | None:
    """(classpath, JVM options, main class) from signal-cli's Windows start script (Gradle's), or None if it
    isn't one. The script puts every jar's full path on one cmd.exe line, which passes cmd's 8191-character
    limit when signal-cli sits in a long folder path, so Jig starts Java with the same arguments itself."""
    if not signal._is_batch(binary):
        return None
    try:
        text = Path(binary).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    cp, main, opts = _CLASSPATH.search(text), _MAIN.search(text), _JVM_OPTS.search(text)
    if not (cp and main):
        return None
    home = str(Path(binary).resolve().parent.parent)
    classpath = [p.replace("%APP_HOME%", home) for p in cp.group(1).split(";") if p]
    jvm = [a or b for a, b in re.findall(r'"([^"]*)"|(\S+)', opts.group(1))] if opts else []
    if any("%" in p for p in [*classpath, *jvm]):
        return None
    return classpath, jvm, main.group(1)


async def launch(command: list[str], tools: Path | None = None,
                 env: dict[str, str] | None = None) -> tuple[list[str], dict[str, str]]:
    """The process to start for a signal-cli command line, and its environment. For a Gradle start script,
    Java directly with the script's classpath, options and main class; otherwise the command as it is."""
    binary, args = command[0], command[1:]
    env = env if env is not None else await java_env(binary, tools)
    script = start_script(binary)
    if script is None:
        return command, env
    java = await find_java(tools or tools_of(binary))
    if not java["ok"]:
        return command, env  # the script then says itself that Java is missing
    classpath, jvm, main = script
    opts = [*os.environ.get("JAVA_OPTS", "").split(), *env.get("SIGNAL_CLI_OPTS", "").split()]
    return [java["path"], *jvm, *opts, "-classpath", os.pathsep.join(classpath), main, *args], env


# What is here -----------------------------------------------------------------------------------------------
def managed_signal_cli(tools: Path) -> str | None:
    found = sorted((tools / "signal-cli").glob(f"signal-cli-*/bin/{SIGNAL_CLI}"), reverse=True)
    return str(found[0]) if found else None


async def status(tools: Path) -> dict[str, Any]:
    java = await find_java(tools)
    binary = managed_signal_cli(tools)
    if binary is None and (on_path := shutil.which("signal-cli")):
        binary = os.path.abspath(on_path)
    return {"java": java, "signal_cli": {"path": binary, "managed": bool(binary and tools_of(binary))},
            "java_page": TEMURIN_PAGE, "signal_cli_page": RELEASES_PAGE}


def needs_java(path: str) -> bool:
    """signal-cli's start script runs Java; its native Linux build doesn't."""
    if signal._is_batch(path):
        return True
    try:
        with open(path, "rb") as fh:
            return fh.read(2) == b"#!"
    except OSError:
        return True


async def check_ready(binary: str, tools: Path | None = None) -> tuple[str, str]:
    """(absolute path, version) of a signal-cli that runs with a Java it can use, or a ConnectorError saying
    what is missing in plain words."""
    path = signal.find_binary(binary)
    java = await find_java(tools or tools_of(path)) if needs_java(path) else {"ok": True}
    if not java["ok"]:
        older = "; ".join(f"Java {o['version']} at {o['path']}" for o in java["older"])
        raise ConnectorError(f"signal-cli needs Java {JAVA_NEEDED} or newer, and "
                             + (f"I only found older ones ({older})" if older else "I couldn't find Java on this "
                                "computer") + f". Get Java {JAVA_NEEDED} from {TEMURIN_PAGE}, or let me download it")
    version = (await signal.run(signal.argv(path, "--version"), env=await java_env(path, tools))).strip()
    if not version.lower().startswith("signal-cli"):
        raise ConnectorError(f"{path} is not signal-cli: '--version' printed {version[:80]!r}")
    return path, version


# Downloading ------------------------------------------------------------------------------------------------
def _arch() -> str:
    machine = platform.machine().lower()
    return {"amd64": "x64", "x86_64": "x64", "arm64": "aarch64", "aarch64": "aarch64"}.get(machine, machine)


def _os() -> str:
    return {"win32": "windows", "darwin": "mac"}.get(sys.platform, "linux")


async def signal_cli_release(http: httpx.AsyncClient) -> dict[str, Any]:
    """The latest signal-cli release's Java build: {"version", "name", "url", "size", "sha256"}."""
    try:
        r = await http.get(RELEASES_API, timeout=30, headers={"Accept": "application/vnd.github+json"})
        r.raise_for_status()
        release = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ConnectorError(f"couldn't ask GitHub for signal-cli's latest release: {type(exc).__name__}") from None
    version = str(release.get("tag_name", "")).lstrip("v")
    name = f"signal-cli-{version}.tar.gz"
    asset = next((a for a in release.get("assets", []) if a.get("name") == name), None)
    if not version or asset is None:
        raise ConnectorError(f"signal-cli's latest release on GitHub has no {name or 'Java build'}; download it "
                             f"yourself from {RELEASES_PAGE}")
    digest = str(asset.get("digest") or "")
    if not digest.startswith("sha256:"):
        raise ConnectorError(f"GitHub lists no SHA-256 checksum for {name}, so I won't download it; download it "
                             f"yourself from {RELEASES_PAGE}")
    return {"what": "signal-cli", "version": version, "name": name, "url": asset["browser_download_url"],
            "size": int(asset["size"]), "sha256": digest.split(":", 1)[1].lower(), "licence": "GPL-3.0"}


async def java_release(http: httpx.AsyncClient) -> dict[str, Any]:
    """Eclipse Temurin's latest Java runtime (JRE) for this computer, from Adoptium's API."""
    params = {"os": _os(), "architecture": _arch(), "image_type": "jre", "vendor": "eclipse"}
    try:
        r = await http.get(ADOPTIUM_API.format(version=JAVA_NEEDED), params=params, timeout=30)
        r.raise_for_status()
        assets = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ConnectorError(f"couldn't ask Adoptium for Java {JAVA_NEEDED}: {type(exc).__name__}") from None
    pkg = next((a["binary"]["package"] for a in assets if a.get("binary", {}).get("package")), None)
    if not pkg or not pkg.get("checksum"):
        raise ConnectorError(f"Adoptium has no Java {JAVA_NEEDED} runtime for {_os()} on {_arch()}; get Java from "
                             f"{TEMURIN_PAGE}")
    return {"what": "Java", "version": assets[0].get("release_name", ""), "name": pkg["name"], "url": pkg["link"],
            "size": int(pkg["size"]), "sha256": str(pkg["checksum"]).lower(),
            "licence": "GPL-2.0 with the Classpath Exception"}


def _unpack(archive: Path, dest: Path) -> Path:
    """Unpack into a fresh folder inside ``dest``; returns the one folder the archive holds."""
    staging = dest / f".unpack-{os.getpid()}-{int(time.time())}"
    staging.mkdir(parents=True)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for member in z.namelist():
                target = (staging / member).resolve()
                if staging.resolve() not in target.parents and target != staging.resolve():
                    raise ConnectorError(f"{archive.name} has a file outside its folder ({member!r}); not unpacked")
            z.extractall(staging)
    else:
        with tarfile.open(archive) as t:
            t.extractall(staging, filter="data")  # refuses links and paths that lead out of the folder
    tops = [p for p in staging.iterdir()]
    if len(tops) != 1 or not tops[0].is_dir():
        shutil.rmtree(staging, ignore_errors=True)
        raise ConnectorError(f"{archive.name} doesn't hold a single folder; not unpacked")
    final = dest / tops[0].name
    if final.exists():
        shutil.rmtree(final)
    tops[0].rename(final)
    shutil.rmtree(staging, ignore_errors=True)
    return final


class Downloads:
    """One download at a time, for Settings to follow: GET shows ``state``."""

    def __init__(self, tools: Path, audit: Any):
        self.tools = tools
        self.audit = audit
        self.state: dict[str, Any] = {"status": "idle"}
        self._task: asyncio.Task[None] | None = None

    async def close(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    def start(self) -> dict[str, Any]:
        if self._task is not None and not self._task.done():
            return self.state
        self.state = {"status": "running", "step": "Asking GitHub and Adoptium for the latest versions",
                      "done_bytes": 0, "total_bytes": 0}
        self._task = asyncio.get_running_loop().create_task(self._run())
        return self.state

    async def _run(self) -> None:
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(60, connect=20)) as http:
                wanted = [await signal_cli_release(http)]
                if not (await find_java(self.tools))["ok"]:
                    wanted.append(await java_release(http))
                total = sum(w["size"] for w in wanted)
                self.tools.mkdir(parents=True, exist_ok=True)
                free = shutil.disk_usage(self.tools).free
                if free < total * 3:
                    raise ConnectorError(f"there isn't enough free disk space: this needs about "
                                         f"{total * 3 // 2**20} MB while unpacking, and {free // 2**20} MB is free")
                self.state.update(total_bytes=total, items=[{k: w[k] for k in ("what", "version", "size", "licence")}
                                                            for w in wanted])
                done = 0
                for w in wanted:
                    archive = await self._fetch(http, w, done)
                    done += w["size"]
                    self.state.update(step=f"Unpacking {w['what']}")
                    dest = self.tools / ("java" if w["what"] == "Java" else "signal-cli")
                    for old in dest.glob("*"):
                        if old.is_dir():
                            shutil.rmtree(old, ignore_errors=True)
                    await asyncio.to_thread(_unpack, archive, dest)
                    archive.unlink()
                    self.audit.record("connector.signal_download", f"downloaded {w['what']} {w['version']} for "
                                      "Signal", actor="user", provider="signal", what=w["what"], version=w["version"],
                                      source=w["url"], sha256=w["sha256"], licence=w["licence"])
            binary = managed_signal_cli(self.tools)
            if binary is None:
                raise ConnectorError(f"signal-cli unpacked, but its bin\\{SIGNAL_CLI} isn't there")
            self.state.update(step="Checking that signal-cli runs")
            path, version = await check_ready(binary, self.tools)
            self.state = {"status": "done", "signal_cli": path, "version": version,
                          "java": await find_java(self.tools), "items": self.state.get("items", [])}
        except ConnectorError as exc:
            self.state = {"status": "failed", "error": str(exc)}
        except Exception as exc:  # reported to the person, never swallowed
            log.exception("signal-cli download failed")
            self.state = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}

    async def _fetch(self, http: httpx.AsyncClient, w: dict[str, Any], before: int) -> Path:
        partial = self.tools / ".download" / f"{w['name']}.part"
        partial.parent.mkdir(parents=True, exist_ok=True)
        self.state.update(step=f"Downloading {w['what']} {w['version']}")
        sha = hashlib.sha256()
        got = 0
        try:
            async with http.stream("GET", w["url"]) as r:
                r.raise_for_status()
                with partial.open("wb") as fh:
                    async for chunk in r.aiter_bytes(1 << 16):
                        fh.write(chunk)
                        sha.update(chunk)
                        got += len(chunk)
                        self.state["done_bytes"] = before + got
        except httpx.HTTPError as exc:
            partial.unlink(missing_ok=True)
            raise ConnectorError(f"downloading {w['what']} failed: {type(exc).__name__}") from None
        if sha.hexdigest() != w["sha256"]:
            partial.unlink(missing_ok=True)
            raise ConnectorError(f"the {w['what']} download doesn't match the checksum its publisher lists, so I "
                                 "deleted it. Try again; if it happens again, download it yourself")
        archive = partial.with_suffix("")
        partial.replace(archive)
        return archive


# Linking ----------------------------------------------------------------------------------------------------
class CodeExpired(Exception):
    """signal-cli's link code wasn't scanned before Signal's servers dropped it."""


class Link:
    """``signal-cli link`` run by Jig, one at a time, for Settings to follow: GET shows ``state``."""

    def __init__(self, tools: Path, audit: Any, qr: Any):
        self.tools = tools
        self.audit = audit
        self._qr = qr  # text -> an SVG data URI, made locally
        self.state: dict[str, Any] = {"status": "idle"}
        self._task: asyncio.Task[None] | None = None
        self._proc: asyncio.subprocess.Process | None = None

    def start(self, binary: str, *, extra_args: tuple[str, ...] = ()) -> dict[str, Any]:
        self.cancel()
        self.state = {"status": "starting", "started": time.time()}
        self._task = asyncio.get_running_loop().create_task(self._run(binary, extra_args))
        return self.state

    def cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            self.state = {"status": "cancelled"}

    async def close(self) -> None:
        """Stop waiting and end signal-cli (Jig is turning off)."""
        task = self._task
        self.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def _run(self, binary: str, extra_args: tuple[str, ...]) -> None:
        try:
            path, _ = await check_ready(binary, self.tools)
            env = await java_env(path, self.tools)
            deadline = time.monotonic() + LINK_TIMEOUT_S
            number = None
            # signal-cli gives up on a code after about two minutes; a new one replaces it until the deadline.
            while number is None:
                if time.monotonic() >= deadline:
                    self.state = {"status": "expired", "error": "The code wasn't scanned in time, so I stopped "
                                  "waiting. Make a new code to try again."}
                    return
                command, _ = await launch(signal.argv(path, *extra_args, "link", "-n", DEVICE_NAME), self.tools, env)
                try:
                    self._proc = await asyncio.create_subprocess_exec(
                        *command, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE, env=env)
                except OSError as exc:
                    raise ConnectorError(f"could not start {path}: {exc}") from None
                try:
                    number = await asyncio.wait_for(self._follow(self._proc),
                                                    max(1.0, deadline - time.monotonic()))
                except (TimeoutError, CodeExpired):
                    await signal._kill(self._proc)
                    self.state = {"status": "starting", "codes": self.state.get("codes", 0)}
            await signal.check_account(path, number, env=env)
            self.state = {"status": "linked", "number": number, "signal_cli": path}
            self.audit.record("connector.signal_linked", "signal-cli linked to a phone from Settings", actor="user",
                              provider="signal", device_name=DEVICE_NAME)
        except ConnectorError as exc:
            self.state = {"status": "failed", "error": str(exc)}
        except Exception as exc:  # reported to the person, never swallowed
            log.exception("signal-cli link failed")
            self.state = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        finally:
            if self._proc is not None and self._proc.returncode is None:
                await signal._kill(self._proc)
            self._proc = None

    async def _follow(self, proc: asyncio.subprocess.Process) -> str:
        assert proc.stdout is not None and proc.stderr is not None
        stderr = asyncio.ensure_future(proc.stderr.read())  # read alongside, so a chatty stderr can't block it
        number = None
        try:
            while raw := await proc.stdout.readline():
                line = raw.decode("utf-8", "replace").strip()
                if _LINK_URI.match(line):
                    codes = self.state.get("codes", 0) + 1
                    self.state = {"status": "waiting", "uri": line, "qr": self._qr(line), "codes": codes}
                elif m := _ASSOCIATED.match(line):
                    number = m.group(1)
            err = (await stderr).decode("utf-8", "replace").strip()
        finally:
            stderr.cancel()
        code = await proc.wait()
        if code == 0 and number:
            return number
        if "timed out" in err.lower():
            raise CodeExpired()
        raise ConnectorError(f"signal-cli couldn't link (exit code {code}): {err[-600:] or 'no error text'}")
