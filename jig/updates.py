"""User-initiated updates. Nothing here runs unless the person asks.

Check for updates (Settings, or the tray menu) asks the GitHub Releases API once, over HTTPS, with no
token. Install update is only for a copy put on Windows by Jig's installer: it downloads that release's
``JigSetup-<version>.exe``, checks the SHA-256 and size published with the same release, and refuses to
continue if they do not match. There is no background check and no silent install. The agent has no tool
for this, and the HTTP routes sit behind the same sign-in as the rest of the API.

A checkout of the source, or Jig in a container, is shown the new version and how to update it by hand.
Those copies do not rewrite themselves.

The source repository is ``[updates] repo`` (or ``JIG_UPDATES_REPO``), ``rlesueur/jig`` if unset. Tests
point it at a private repository; the running app never sends a token.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel

from . import __version__

log = logging.getLogger(__name__)

DEFAULT_REPO = "rlesueur/jig"
# The same form as publish.ps1: final X.Y.Z, or a PEP 440 pre-release (aN, bN, rcN).
VERSION_RE = re.compile(r"^v?(\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?)$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
API = "https://api.github.com"
NOTES_LIMIT = 80_000
# Larger than the published installer (about 26 MB) with room to grow, and a stop if a release is absurd.
MAX_INSTALLER_BYTES = 500 * 1024 * 1024
MAX_CHECKSUM_BYTES = 64 * 1024
PENDING_NAME = "update-pending.json"
RESULT_NAME = "update-result.json"
# Hosts a GitHub release asset is served from, including the redirect off api.github.com.
DOWNLOAD_HOSTS = frozenset({
    "github.com",
    "api.github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "github-releases.githubusercontent.com",
})
# Inno Setup, matching installer/jig.iss. /VERYSILENT shows no wizard. /SUPPRESSMSGBOXES covers any
# MsgBox. SuppressibleMsgBox (the WebView2 note, a failed Start with Windows) returns its default in
# silent mode without a window. /NORESTART stops a reboot. /SP- skips the opening question, which silent
# mode already skips. CloseApplications=no, so Inno does not ask to close programs; PrepareToInstall
# closes Jig's window and tray itself. [Run] is skipifsilent, so the installer does not open Jig; the
# helper starts the tray after a successful install. The AppId is the published one, so this upgrades
# the existing install in place. WriteConfig leaves an existing jig.toml alone, and program files are
# replaced under {app} while settings and data stay in the data folder.
INSTALLER_FLAGS = ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-")

SIGNING_NOTICE = (
    "The Windows installer isn't code-signed yet, so Windows may warn you if you run it yourself. "
    "When you install an update from here, Jig checks the download against the SHA-256 file published "
    "with that same GitHub release, and will not install it if the hash or the size does not match. "
    "That check is the published checksum, not a signature from a certificate authority."
)

HELPER_PS1 = r"""# Detached from Jig: wait for it to exit, run the installer silently, then start the tray.
# Lives in a temp folder so the installer can replace Jig's own files while this script runs.
param(
  [Parameter(Mandatory = $true)][int]$WaitPid,
  [Parameter(Mandatory = $true)][string]$Installer,
  [Parameter(Mandatory = $true)][string]$Pythonw,
  [Parameter(Mandatory = $true)][string]$Config,
  [Parameter(Mandatory = $true)][string]$ResultPath,
  [Parameter(Mandatory = $true)][string]$LogPath,
  [Parameter(Mandatory = $true)][string]$InstallerLog
)
$ErrorActionPreference = 'Stop'

function Write-Log([string]$Message) {
  $dir = Split-Path -Parent $LogPath
  if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  Add-Content -Path $LogPath -Encoding utf8 -Value ("{0} {1}" -f (Get-Date -Format o), $Message)
}

function Write-Result([int]$Code, [string]$ErrorText) {
  $dir = Split-Path -Parent $ResultPath
  if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  $json = @{ exit_code = $Code; error = $ErrorText } | ConvertTo-Json -Compress
  [IO.File]::WriteAllText($ResultPath, $json + "`n", (New-Object System.Text.UTF8Encoding $false))
}

try {
  Write-Log "Waiting for Jig (pid $WaitPid) to finish turning off."
  if ($WaitPid -gt 0) {
    $running = Get-Process -Id $WaitPid -ErrorAction SilentlyContinue
    if ($running) {
      try { Wait-Process -Id $WaitPid -Timeout 180 -ErrorAction Stop }
      catch {
        Write-Log "Jig was still running after the wait, so the installer was not started."
        Write-Result -Code 1 -ErrorText "Jig was still running, so the installer was not started."
        exit 1
      }
    }
  }
  Write-Log "Running the installer."
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $Installer
  $psi.Arguments = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /LOG=`"$InstallerLog`""
  $psi.UseShellExecute = $false
  $psi.CreateNoWindow = $true
  $proc = [Diagnostics.Process]::Start($psi)
  $proc.WaitForExit()
  $code = $proc.ExitCode
  Write-Log "Installer finished with exit code $code."
  Write-Result -Code $code -ErrorText $(if ($code -eq 0) { '' } else { "The installer finished with exit code $code." })
  if ($code -ne 0) { exit $code }
  Write-Log "Starting Jig."
  $tray = New-Object System.Diagnostics.ProcessStartInfo
  $tray.FileName = $Pythonw
  $tray.Arguments = "-m jig.tray --config `"$Config`" --open"
  $tray.WorkingDirectory = Split-Path (Split-Path $Pythonw)
  $tray.UseShellExecute = $false
  $tray.CreateNoWindow = $true
  [Diagnostics.Process]::Start($tray) | Out-Null
  if (Test-Path $Installer) {
    try { Remove-Item -Force $Installer } catch { Write-Log ("Could not delete the installer: " + $_.Exception.Message) }
  }
  exit 0
} catch {
  Write-Log ("Update helper failed: " + $_.Exception.Message)
  Write-Result -Code 1 -ErrorText $_.Exception.Message
  exit 1
}
"""


class InstallIn(BaseModel):
    """Body for Install update. ``confirm`` must be true; the person has already agreed in the window."""

    confirm: bool | None = None
    version: str = ""


class UpdateError(Exception):
    """A problem to show the person, in plain English. ``status`` is the HTTP status for the API."""

    def __init__(self, message: str, *, status: int = 502):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    size: int
    asset_id: int


@dataclass(frozen=True)
class Release:
    version: str
    tag: str
    published: str
    notes: str
    page_url: str
    prerelease: bool
    installer: ReleaseAsset | None
    checksum: ReleaseAsset | None


@dataclass(frozen=True)
class UpdateOffer:
    """The result of one check. ``release`` is set only when a newer version should be offered."""

    current: str
    repo: str
    release: Release | None
    includes_prereleases: bool
    ignored_prerelease: bool

    @property
    def up_to_date(self) -> bool:
        return self.release is None


def check_repo(repo: str) -> str:
    """``owner/name`` with no spaces or URL parts, or an UpdateError the person can read."""
    text = repo.strip()
    if not REPO_RE.fullmatch(text):
        raise UpdateError(f"[updates] repo must be a GitHub repository as owner/name, not {repo!r}.", status=400)
    return text


def parse_jig_version(text: str) -> Version | None:
    """A Jig version (optional leading v), or None when it is not one Jig publishes."""
    match = VERSION_RE.fullmatch(text.strip())
    if not match:
        return None
    try:
        return Version(match.group(1))
    except InvalidVersion:
        return None


def version_text(version: Version) -> str:
    return str(version)


def british_date(iso: str) -> str:
    """``4 October 2026`` from a GitHub timestamp, or the original text if it is not one."""
    if not iso:
        return "an unknown date"
    try:
        when = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    return f"{when.day} {when.strftime('%B')} {when.year}"


def classify_install(deployment: str, marker: Path) -> str:
    """``container``, ``installer`` (the Windows installer wrote installed.json) or ``checkout``."""
    if deployment == "container":
        return "container"
    if marker.is_file():
        return "installer"
    return "checkout"


def install_marker() -> Path:
    """``installed.json`` beside the jig package: ``{app}\\app\\installed.json`` in an installer layout."""
    return Path(__file__).resolve().parent.parent / "installed.json"


def install_kind(deployment: str) -> str:
    return classify_install(deployment, install_marker())


def can_install(kind: str) -> bool:
    """The Windows installer can replace itself. A checkout or a container cannot, and only on Windows."""
    return kind == "installer" and sys.platform == "win32"


def installer_paths() -> tuple[Path, Path]:
    """``({app}, pythonw.exe)`` for this installer layout."""
    marker = install_marker()
    app = marker.parent.parent
    pythonw = app / "python" / "pythonw.exe"
    if not pythonw.is_file():
        raise UpdateError(f"Jig couldn't find its program at {pythonw}, so it can't install an update.", status=409)
    return app, pythonw


def _headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"Jig/{__version__} (https://github.com/rlesueur/jig)",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(connect=15.0, read=120.0, write=30.0, pool=15.0)


def _rate_limited(response: httpx.Response) -> bool:
    if response.status_code == 429:
        return True
    if response.status_code != 403:
        return False
    if response.headers.get("x-ratelimit-remaining") == "0":
        return True
    text = response.text.lower()
    return "rate limit" in text or "secondary rate" in text


def _github_problem(response: httpx.Response, *, what: str) -> UpdateError:
    if _rate_limited(response):
        return UpdateError(
            "GitHub's limit on how often an unsigned-in program can ask about releases has been reached. "
            "Jig only asks when you click Check for updates. Try again later.")
    if response.status_code == 404:
        return UpdateError(f"Jig couldn't find {what}. If this isn't the usual Jig repository, check "
                           "[updates] repo in jig.toml.")
    return UpdateError(f"GitHub didn't answer properly while Jig was trying to {what} (HTTP {response.status_code}). "
                       "Try again later.")


def _check_download_url(url: str) -> None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or host not in DOWNLOAD_HOSTS:
        raise UpdateError("The download address from GitHub wasn't one Jig accepts, so nothing was downloaded.")


def _client(token: str | None) -> httpx.Client:
    def _guard(request: httpx.Request) -> None:
        _check_download_url(str(request.url))

    return httpx.Client(follow_redirects=True, timeout=_timeout(), headers=_headers(token),
                        event_hooks={"request": [_guard]})


def _asset(item: dict[str, Any], name: str) -> ReleaseAsset | None:
    for asset in item.get("assets") or []:
        if not isinstance(asset, dict) or asset.get("name") != name:
            continue
        size, asset_id = asset.get("size"), asset.get("id")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise UpdateError(f"GitHub didn't give a size for {name}, so Jig won't download it.")
        if not isinstance(asset_id, int) or isinstance(asset_id, bool):
            raise UpdateError(f"GitHub didn't give an id for {name}, so Jig won't download it.")
        return ReleaseAsset(name=name, size=size, asset_id=asset_id)
    return None


def _release_from_api(item: dict[str, Any], repo: str) -> Release | None:
    if not isinstance(item, dict) or item.get("draft"):
        return None
    tag = item.get("tag_name")
    if not isinstance(tag, str):
        return None
    version = parse_jig_version(tag)
    if version is None:
        return None
    text = version_text(version)
    notes = item.get("body") if isinstance(item.get("body"), str) else ""
    notes = notes.replace("\r\n", "\n").replace("\x00", "")
    if len(notes) > NOTES_LIMIT:
        notes = notes[:NOTES_LIMIT] + "\n\n(The rest of the release notes are on the release page.)"
    published = item.get("published_at") if isinstance(item.get("published_at"), str) else ""
    page = f"https://github.com/{repo}/releases/tag/{tag}"
    return Release(
        version=text, tag=tag, published=published, notes=notes, page_url=page,
        prerelease=bool(item.get("prerelease")) or version.is_prerelease,
        installer=_asset(item, f"JigSetup-{text}.exe"),
        checksum=_asset(item, f"JigSetup-{text}.exe.sha256"),
    )


def fetch_releases(repo: str, *, token: str | None = None) -> list[Release]:
    """Every published release on ``repo``, newest page first. Raises UpdateError when GitHub can't be asked."""
    repo = check_repo(repo)
    found: list[Release] = []
    url: str | None = f"{API}/repos/{repo}/releases"
    params: dict[str, Any] | None = {"per_page": 100}
    try:
        with _client(token) as client:
            for _ in range(5):
                if not url:
                    break
                response = client.get(url, params=params)
                params = None  # the next page's Link URL already carries its query
                if response.status_code != 200:
                    raise _github_problem(response, what=f"releases for {repo}")
                payload = response.json()
                if not isinstance(payload, list):
                    raise UpdateError("GitHub's reply wasn't a list of releases, so Jig didn't use it.")
                for item in payload:
                    release = _release_from_api(item, repo)
                    if release is not None:
                        found.append(release)
                url = _next_link(response.headers.get("link"))
    except UpdateError:
        raise
    except httpx.HTTPError as exc:
        log.info("update check could not reach GitHub: %s", type(exc).__name__)
        raise UpdateError("Jig couldn't reach GitHub to check for updates. Check this computer's internet "
                          "connection and try again.") from exc
    return found


def _next_link(header: str | None) -> str | None:
    if not header:
        return None
    for part in header.split(","):
        bits = [b.strip() for b in part.split(";")]
        if len(bits) >= 2 and bits[1] == 'rel="next"' and bits[0].startswith("<") and bits[0].endswith(">"):
            return bits[0][1:-1]
    return None


def choose_release(current: str, releases: list[Release]) -> tuple[Release | None, bool]:
    """The newest release newer than ``current``.

    While ``current`` is a pre-release, pre-releases are included. A final version never offers a
    pre-release, and nothing older than ``current`` is offered. Returns the release (or None) and whether
    a newer pre-release was ignored because this copy is a final version.
    """
    cur = parse_jig_version(current)
    if cur is None:
        raise UpdateError(f"This copy's version {current!r} isn't a Jig version, so Jig can't compare it.",
                          status=500)
    include_pre = cur.is_prerelease
    ignored = False
    newer: list[Release] = []
    for release in releases:
        ver = parse_jig_version(release.version)
        if ver is None:
            continue
        if ver <= cur:
            continue
        if ver.is_prerelease and not include_pre:
            ignored = True
            continue
        newer.append(release)
    if not newer:
        return None, ignored
    return max(newer, key=lambda r: parse_jig_version(r.version) or Version("0")), ignored


def check_for_update(repo: str, current: str, *, token: str | None = None) -> UpdateOffer:
    releases = fetch_releases(repo, token=token)
    release, ignored = choose_release(current, releases)
    cur = parse_jig_version(current)
    return UpdateOffer(current=version_text(cur) if cur else current, repo=check_repo(repo), release=release,
                       includes_prereleases=bool(cur and cur.is_prerelease), ignored_prerelease=ignored)


def checkout_instructions(version: str) -> str:
    return (
        f"This copy of Jig is a checkout of the source code, so it won't change itself.\n\n"
        f"To update to {version}, in the repository folder:\n\n"
        f"git pull\n"
        f"pip install -e .\n\n"
        f"Then restart Jig (jig stop, then jig serve). Your data folder is kept."
    )


def container_instructions(version: str, repo: str) -> str:
    owner = check_repo(repo).split("/", 1)[0]
    return (
        f"This copy of Jig runs in a container, so it won't change itself.\n\n"
        f"To update to {version}, on the computer that runs Docker:\n\n"
        f"git pull\n"
        f"docker compose pull\n"
        f"docker compose up -d\n\n"
        f"That keeps your data volumes. The images for this version are ghcr.io/{owner}/jig:{version} and "
        f"ghcr.io/{owner}/jig-sandbox:{version}. compose.yaml pins the version it was released with. Set "
        f"JIG_IMAGE and JIG_SANDBOX_IMAGE in .env to pin another tag, and keep the two the same."
    )


def instructions_for(kind: str, version: str, repo: str) -> str | None:
    if kind == "checkout":
        return checkout_instructions(version)
    if kind == "container":
        return container_instructions(version, repo)
    return None


def parse_sha256(text: str, filename: str) -> str:
    """The hex digest for ``filename`` in a ``.sha256`` file (GNU ``hash  name`` or BSD form)."""
    wanted = filename.lower()
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        bsd = re.fullmatch(r"SHA256 \((.+)\) = ([0-9a-fA-F]{64})", line)
        if bsd and bsd.group(1).lower() == wanted:
            return bsd.group(2).lower()
        gnu = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(\S+)", line)
        if gnu and gnu.group(2).lower() == wanted:
            return gnu.group(1).lower()
    raise UpdateError(f"The release's .sha256 file has no SHA-256 line for {filename}, so Jig will not install it.")


def _download(client: httpx.Client, repo: str, asset: ReleaseAsset, dest: Path, *, limit: int) -> None:
    if asset.size > limit:
        raise UpdateError(f"{asset.name} is {asset.size} bytes, which is larger than Jig will download "
                          f"({limit} bytes). Nothing was saved.")
    url = f"{API}/repos/{repo}/releases/assets/{asset.asset_id}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with client.stream("GET", url, headers={"Accept": "application/octet-stream"}) as response:
            if response.status_code != 200:
                raise _github_problem(response, what=f"the file {asset.name}")
            length = response.headers.get("content-length")
            if length is not None and length != str(asset.size):
                raise UpdateError(f"GitHub said {asset.name} is {asset.size} bytes, but the download says it is "
                                  f"{length} bytes. Jig will not install it.")
            got = 0
            with dest.open("wb") as fh:
                for chunk in response.iter_bytes():
                    got += len(chunk)
                    if got > asset.size:
                        break
                    fh.write(chunk)
    except UpdateError:
        dest.unlink(missing_ok=True)
        raise
    except httpx.HTTPError as exc:
        dest.unlink(missing_ok=True)
        log.info("update download failed: %s", type(exc).__name__)
        raise UpdateError("Jig couldn't download the update from GitHub. Check this computer's internet "
                          "connection and try again.") from exc
    if got != asset.size:
        digest = hashlib.sha256(dest.read_bytes()).hexdigest() if dest.is_file() else ""
        dest.unlink(missing_ok=True)
        raise UpdateError(
            f"The download of {asset.name} doesn't match the release. Jig expected {asset.size} bytes and "
            f"the file was {got} bytes"
            + (f" (SHA-256 {digest})" if digest else "")
            + ". Jig deleted it and will not install it.")


def verify_installer(path: Path, *, filename: str, expected_sha256: str, expected_size: int) -> str:
    """SHA-256 of ``path`` if it matches ``expected_sha256`` and ``expected_size``. Otherwise delete it and refuse."""
    if not path.is_file():
        raise UpdateError(f"The download of {filename} isn't there, so Jig will not install it.")
    size = path.stat().st_size
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    sha_ok = hmac.compare_digest(digest, expected_sha256.lower())
    if size == expected_size and sha_ok:
        return digest
    path.unlink(missing_ok=True)
    raise UpdateError(
        f"The downloaded installer doesn't match the release. Jig expected SHA-256 {expected_sha256.lower()} "
        f"and {expected_size} bytes, and the file was SHA-256 {digest} and {size} bytes. "
        "Jig deleted it and will not install it.")


def download_verified(repo: str, release: Release, dest_dir: Path, *, token: str | None = None) -> tuple[Path, str]:
    """Download the installer and its .sha256 asset, check both, and return the exe path and the digest.

    A mismatch deletes the download and raises UpdateError. There is no other file Jig will install instead.
    """
    repo = check_repo(repo)
    if release.installer is None or release.checksum is None:
        raise UpdateError(f"The release {release.version} has no JigSetup-{release.version}.exe and matching "
                          ".sha256 file, so Jig can't install it. You can download the release from its page.",
                          status=409)
    dest_dir.mkdir(parents=True, exist_ok=True)
    exe = dest_dir / release.installer.name
    checksum = dest_dir / release.checksum.name
    try:
        with _client(token) as client:
            _download(client, repo, release.checksum, checksum, limit=MAX_CHECKSUM_BYTES)
            try:
                text = checksum.read_text(encoding="utf-8-sig")
            except UnicodeError as exc:
                raise UpdateError("The release's .sha256 file isn't text, so Jig will not install the update.") from exc
            expected = parse_sha256(text, release.installer.name)
            _download(client, repo, release.installer, exe, limit=MAX_INSTALLER_BYTES)
    finally:
        checksum.unlink(missing_ok=True)
    digest = verify_installer(exe, filename=release.installer.name, expected_sha256=expected,
                              expected_size=release.installer.size)
    return exe, digest


def _safe_path(path: Path, what: str) -> str:
    text = str(path)
    if '"' in text or "\n" in text or "\r" in text:
        raise UpdateError(f"Jig can't update because the {what} contains a quote or a line break ({text}).",
                          status=409)
    return text


def spawn_helper(*, installer: Path, pythonw: Path, config_path: Path, data_dir: Path, directory: Path) -> None:
    """Start the detached helper. It waits for this process, runs the installer, then starts the tray."""
    if sys.platform != "win32":
        raise UpdateError("The Windows installer only runs on Windows.", status=409)
    script = directory / "jig-update-helper.ps1"
    script.write_text(HELPER_PS1, encoding="utf-8")
    result = data_dir / RESULT_NAME
    log_path = data_dir / "logs" / "update-helper.log"
    installer_log = data_dir / "logs" / "installer.log"
    cmd = [
        "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-WindowStyle", "Hidden", "-File", str(script),
        "-WaitPid", str(os.getpid()),
        "-Installer", _safe_path(installer, "installer path"),
        "-Pythonw", _safe_path(pythonw, "program path"),
        "-Config", _safe_path(config_path, "settings path"),
        "-ResultPath", _safe_path(result, "result path"),
        "-LogPath", _safe_path(log_path, "log path"),
        "-InstallerLog", _safe_path(installer_log, "installer log path"),
    ]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    breakaway = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB, so the helper outlives Jig
    kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    try:
        subprocess.Popen(cmd, creationflags=flags | breakaway, **kwargs)  # noqa: S603
    except OSError:
        subprocess.Popen(cmd, creationflags=flags, **kwargs)  # noqa: S603
    log.info("update helper started for pid %s", os.getpid())


def write_pending(data_dir: Path, *, current: str, target: str, sha256: str, size: int) -> None:
    payload = {"from_version": current, "to_version": target, "sha256": sha256, "bytes": size}
    path = data_dir / PENDING_NAME
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def note_finished_update(state: Any) -> None:
    """If the previous run started an update, record how the installer finished. Safe when nothing is pending."""
    data_dir: Path = state.config.data_dir
    pending_path = data_dir / PENDING_NAME
    if not pending_path.is_file():
        return
    try:
        pending = json.loads(pending_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        state.audit.record("update.failed", "an update was started but its record couldn't be read",
                           actor="runtime", error=type(exc).__name__)
        pending_path.unlink(missing_ok=True)
        return
    result_path = data_dir / RESULT_NAME
    result: dict[str, Any] | None
    try:
        result = json.loads(result_path.read_text(encoding="utf-8-sig")) if result_path.is_file() else None
    except (OSError, UnicodeError, json.JSONDecodeError):
        result = None
    target = pending.get("to_version") if isinstance(pending, dict) else None
    source = pending.get("from_version") if isinstance(pending, dict) else None
    code = result.get("exit_code") if isinstance(result, dict) else None
    if result is None:
        state.audit.record("update.incomplete", f"an update to {target} was started but the installer didn't "
                           "record a result", actor="runtime", from_version=source, to_version=target)
    elif code == 0 and target == __version__:
        state.audit.record("update.installed", f"updated from {source} to {target}", actor="runtime",
                           from_version=source, to_version=target, installer_exit=0)
    else:
        detail = result.get("error") if isinstance(result, dict) and isinstance(result.get("error"), str) else ""
        state.audit.record("update.failed", f"the update to {target} didn't finish"
                           + (f" ({detail[:200]})" if detail else ""),
                           actor="runtime", from_version=source, to_version=target, running_version=__version__,
                           installer_exit=code if isinstance(code, int) else None)
    pending_path.unlink(missing_ok=True)
    result_path.unlink(missing_ok=True)


def status_body(config: Any) -> dict[str, Any]:
    """What About shows before anyone clicks Check for updates. Does not call GitHub."""
    kind = install_kind(config.deployment)
    cur = parse_jig_version(__version__)
    return {
        "version": __version__,
        "prerelease": bool(cur and cur.is_prerelease),
        "kind": kind,
        "repo": config.updates.repo,
        "can_install": can_install(kind),
        "notice": SIGNING_NOTICE if kind == "installer" else (
            "This copy isn't the Windows installer, so Jig won't replace it. Check for updates shows what's new "
            "and how to update this copy yourself."),
        "releases_url": f"https://github.com/{config.updates.repo}/releases",
    }


def prepare_install(config: Any, version: str, directory: Path, *, token: str | None = None) -> dict[str, Any]:
    """Re-check GitHub, download and verify ``version``, and start the helper. Does not stop Jig.

    The caller records the audit entry and shuts Jig down only after this returns. A checkout or a
    container is refused here, before any download.
    """
    wanted = parse_jig_version(version)
    if wanted is None:
        raise UpdateError(f"{version!r} isn't a Jig version.", status=400)
    kind = install_kind(config.deployment)
    if not can_install(kind):
        how = instructions_for(kind, version_text(wanted), config.updates.repo)
        raise UpdateError(how or "The Windows installer only runs on Windows, so Jig can't install this update here.",
                          status=409)
    offer = check_for_update(config.updates.repo, __version__, token=token)
    release = offer.release
    if release is None or parse_jig_version(release.version) != wanted:
        raise UpdateError("That version isn't the update Jig would install. Check for updates again.", status=409)
    directory.mkdir(parents=True, exist_ok=True)
    exe, digest = download_verified(config.updates.repo, release, directory, token=token)
    _, pythonw = installer_paths()
    write_pending(config.data_dir, current=__version__, target=release.version, sha256=digest,
                  size=release.installer.size if release.installer else exe.stat().st_size)
    try:
        spawn_helper(installer=exe, pythonw=pythonw, config_path=config.source, data_dir=config.data_dir,
                     directory=directory)
    except Exception:
        (config.data_dir / PENDING_NAME).unlink(missing_ok=True)
        raise
    return {"version": release.version, "sha256": digest,
            "bytes": release.installer.size if release.installer else exe.stat().st_size,
            "from_version": __version__}


def offer_body(offer: UpdateOffer, kind: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "current": offer.current,
        "repo": offer.repo,
        "up_to_date": offer.up_to_date,
        "includes_prereleases": offer.includes_prereleases,
        "ignored_prerelease": offer.ignored_prerelease,
        "update": None,
    }
    if offer.release is None:
        return body
    release = offer.release
    installable = can_install(kind) and release.installer is not None and release.checksum is not None
    if installable:
        instructions = None
    elif kind == "installer":
        instructions = (f"This release doesn't include JigSetup-{release.version}.exe and its .sha256 file, "
                        "so Jig can't install it from here. You can download it from the release page.")
    else:
        instructions = instructions_for(kind, release.version, offer.repo)
    body["update"] = {
        "version": release.version,
        "published": british_date(release.published),
        "notes": release.notes,
        "page_url": release.page_url,
        "prerelease": release.prerelease,
        "can_install": installable,
        "instructions": instructions,
    }
    return body


def register_update_routes(app: Any, controller: Any, require_local: Any, who: Any) -> None:
    """Authenticated routes. Not a tool, and install only works on the host computer itself."""
    import asyncio
    import tempfile

    from starlette.background import BackgroundTask

    @app.exception_handler(UpdateError)
    async def update_failed(_: Request, exc: UpdateError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=exc.status)

    @app.get("/updates")
    async def updates_status() -> dict[str, Any]:
        """The current version and how this copy was installed. Does not contact GitHub."""
        return status_body(controller.base)

    @app.post("/updates/check")
    async def updates_check() -> dict[str, Any]:
        """Ask GitHub once, because the person clicked Check for updates."""
        offer = await asyncio.to_thread(check_for_update, controller.base.updates.repo, __version__)
        return offer_body(offer, install_kind(controller.base.deployment))

    @app.post("/updates/show")
    async def updates_show(request: Request) -> dict[str, Any]:
        """The tray asks the open window to show About and updates. Local only, and not a check."""
        require_local(request, "Opening About and updates")
        request.app.state.show_updates.set()
        return {"show": True}

    @app.post("/updates/install", status_code=202)
    async def updates_install(request: Request, body: InstallIn) -> JSONResponse:
        """Download, verify, then turn Jig off so the installer can upgrade this copy."""
        require_local(request, "Installing an update")
        if body.confirm is not True:
            raise UpdateError('The update was not installed: send "confirm": true once you have agreed.', status=400)
        request_exit = getattr(request.app.state, "request_exit", None)
        if request_exit is None:
            raise UpdateError("This Jig was not started with 'jig serve', so it can't install an update and open "
                              "again afterwards.", status=409)
        if getattr(request.app.state, "update_in_progress", False):
            raise UpdateError("An update is already being installed.", status=409)
        request.app.state.update_in_progress = True
        directory = Path(tempfile.mkdtemp(prefix="jig-update-"))
        try:
            prepared = await asyncio.to_thread(prepare_install, controller.base, body.version, directory)
        except Exception:
            request.app.state.update_in_progress = False
            raise
        controller.current.audit.record(
            "update.install", f"installing update {prepared['version']} (from {prepared['from_version']})",
            actor="user", from_version=prepared["from_version"], to_version=prepared["version"],
            installer_bytes=prepared["bytes"], sha256=prepared["sha256"], **who(request))
        message = (f"Jig is turning off to install {prepared['version']}. It will open again when the installer "
                   "has finished. Your settings and data are kept.")
        return JSONResponse({"installing": True, "version": prepared["version"], "message": message},
                            status_code=202, background=BackgroundTask(request_exit, "POST /updates/install"))
