"""User-initiated updates. Version comparison is local. The GitHub calls are the real API, not a stand-in.

The download test uses the private repository rlesueur/jig-update-test (a real installer release). It checks
the file and stops. It does not run the installer: that installer shares the published AppId and the
HKCU\\Software\\Jig install record with a normal Jig install, so running it here would not stay isolated.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import httpx
import pytest
from packaging.version import Version

from jig import __version__
from jig.audit import AuditLog
from jig.config import ConfigError, load_config
from jig.db import Database
from jig.tools.builtin import build_registry
from jig.updates import (INSTALLER_FLAGS, SIGNING_NOTICE, UpdateError, check_for_update, choose_release,
                         classify_install, download_verified, note_finished_update, parse_jig_version,
                         parse_sha256, verify_installer)
from jig.updates import Release

from .server_helpers import free_port, kill, start_jig, token, wait_health

ROOT = Path(__file__).resolve().parent.parent
PRIVATE_REPO = "rlesueur/jig-update-test"
PRIVATE_VERSION = "0.1.0b99"


def _release(version: str, *, prerelease: bool = False, draft: bool = False, installer: bool = True) -> Release:
    return Release(version=version, tag=f"v{version}", published="2026-10-04T12:00:00Z", notes=f"Notes for {version}",
                   page_url=f"https://github.com/rlesueur/jig/releases/tag/v{version}", prerelease=prerelease,
                   installer=None, checksum=None)


def test_versions_compare_as_pep440_and_never_downgrade():
    assert parse_jig_version("0.1.0b1") == Version("0.1.0b1")
    assert parse_jig_version("v0.1.0b1") == Version("0.1.0b1")
    assert parse_jig_version("0.1.0-beta.1") is None  # SemVer, not a Jig version
    assert parse_jig_version("1.0.0.dev1") is None
    current = "0.1.0b1"
    releases = [
        _release("0.1.0b1", prerelease=True),
        _release("0.0.9"),
        _release("0.1.0b2", prerelease=True),
        _release("0.1.0"),
        _release("0.2.0a1", prerelease=True),
    ]
    chosen, ignored = choose_release(current, releases)
    assert chosen is not None and chosen.version == "0.2.0a1"
    assert not ignored
    # A final version does not offer a pre-release, and does not go backwards.
    chosen, ignored = choose_release("0.1.0", [_release("0.1.0b2", prerelease=True), _release("0.0.9"),
                                               _release("0.2.0a1", prerelease=True), _release("0.1.0")])
    assert chosen is None
    assert ignored
    chosen, ignored = choose_release("1.2.0", [_release("1.2.3"), _release("1.2.0"), _release("1.3.0b1", prerelease=True)])
    assert chosen is not None and chosen.version == "1.2.3"
    assert ignored
    assert choose_release("0.1.0b2", [_release("0.1.0b1", prerelease=True)])[0] is None


def test_sha256_file_matches_the_published_form():
    name = "JigSetup-0.1.0b1.exe"
    digest = "e" * 64
    assert parse_sha256(f"{digest}  {name}\n", name) == digest
    assert parse_sha256(f"{digest.upper()} *{name}\n", name) == digest
    assert parse_sha256(f"SHA256 ({name}) = {digest}\n", name) == digest
    with pytest.raises(UpdateError, match="no SHA-256 line"):
        parse_sha256(f"{digest}  other.exe\n", name)


def test_a_mismatched_installer_is_deleted_and_refused(tmp_path: Path):
    path = tmp_path / "JigSetup-9.9.9.exe"
    body = b"not an installer"
    path.write_bytes(body)
    expected = hashlib.sha256(body).hexdigest()
    assert verify_installer(path, filename=path.name, expected_sha256=expected, expected_size=len(body)) == expected
    path.write_bytes(body)
    flipped = bytearray(body)
    flipped[-1] ^= 0xFF
    path.write_bytes(bytes(flipped))
    with pytest.raises(UpdateError, match="doesn't match the release"):
        verify_installer(path, filename=path.name, expected_sha256=expected, expected_size=len(body))
    assert not path.exists()


def test_install_kind_is_the_marker_or_the_container(tmp_path: Path):
    missing = tmp_path / "installed.json"
    assert classify_install("container", missing) == "container"
    assert classify_install("host", missing) == "checkout"
    missing.write_text('{"installer": "0.1.0b1"}\n', encoding="utf-8")
    assert classify_install("host", missing) == "installer"
    assert classify_install("container", missing) == "container"


def test_updates_repo_defaults_and_rejects_a_url(tmp_path: Path):
    config = load_config(data_dir=tmp_path / "data", sandbox_dir=tmp_path / "sandbox")
    assert config.updates.repo == "rlesueur/jig"
    bad = tmp_path / "jig.toml"
    bad.write_text('[model]\nbase_url = "http://127.0.0.1:9/v1"\n[updates]\nrepo = "https://github.com/rlesueur/jig"\n',
                   encoding="utf-8")
    with pytest.raises(ConfigError, match="owner/name"):
        load_config(bad)


def test_update_is_not_a_tool():
    names = {spec.name for spec in build_registry().all()}
    assert names
    assert not (names & {"update", "updates", "check_for_updates", "install_update"})


def test_a_finished_update_is_recorded(tmp_path: Path):
    config = load_config(data_dir=tmp_path / "data", sandbox_dir=tmp_path / "sandbox")
    config.data_dir.mkdir(parents=True)
    db = Database(config.db_path)
    audit = AuditLog(db)

    class State:
        pass

    state = State()
    state.config = config
    state.audit = audit
    pending = config.data_dir / "update-pending.json"
    result = config.data_dir / "update-result.json"
    pending.write_text(json.dumps({"from_version": "0.1.0b1", "to_version": __version__}) + "\n", encoding="utf-8")
    result.write_text(json.dumps({"exit_code": 0, "error": ""}) + "\n", encoding="utf-8")
    note_finished_update(state)
    kinds = [row["kind"] for row in audit.query(limit=20)]
    assert kinds == ["update.installed"]
    assert not pending.exists() and not result.exists()
    pending.write_text(json.dumps({"from_version": "0.1.0b1", "to_version": "9.9.9"}) + "\n", encoding="utf-8")
    result.write_text(json.dumps({"exit_code": 1, "error": "stopped"}) + "\n", encoding="utf-8")
    note_finished_update(state)
    assert [row["kind"] for row in audit.query(limit=20)][-1] == "update.failed"
    db.close()


def test_silent_installer_flags_match_the_script():
    from jig.updates import HELPER_PS1

    for flag in INSTALLER_FLAGS:
        assert flag in HELPER_PS1
    assert "jig.tray" in HELPER_PS1
    assert "Wait-Process" in HELPER_PS1
    # In-place upgrade: the script does not pass a different directory.
    assert "/DIR=" not in HELPER_PS1


def test_signing_notice_is_honest():
    assert "isn't code-signed" in SIGNING_NOTICE
    assert "SHA-256" in SIGNING_NOTICE


@pytest.mark.network
def test_public_github_release_is_the_current_beta():
    offer = check_for_update("rlesueur/jig", __version__)
    assert offer.current == "0.1.0b1"
    assert offer.up_to_date
    assert offer.release is None
    assert offer.includes_prereleases


def _github_token() -> str:
    completed = subprocess.run(["gh", "auth", "token"], check=True, capture_output=True, text=True)
    token_value = completed.stdout.strip()
    if not token_value:
        pytest.fail("gh auth token returned nothing")
    return token_value


@pytest.mark.network
def test_private_release_downloads_and_verifies(tmp_path: Path):
    """The real installer from the private test release. Verified, then left unrun."""
    gh_token = _github_token()
    offer = check_for_update(PRIVATE_REPO, "0.1.0b1", token=gh_token)
    assert offer.release is not None
    assert offer.release.version == PRIVATE_VERSION
    assert "Test release" in offer.release.notes
    exe, digest = download_verified(PRIVATE_REPO, offer.release, tmp_path, token=gh_token)
    assert exe.name == f"JigSetup-{PRIVATE_VERSION}.exe"
    assert exe.stat().st_size == offer.release.installer.size  # type: ignore[union-attr]
    assert digest == hashlib.sha256(exe.read_bytes()).hexdigest()
    # A changed byte is refused, and the file is removed. No other installer is tried.
    blob = bytearray(exe.read_bytes())
    blob[0] ^= 0xFF
    exe.write_bytes(bytes(blob))
    with pytest.raises(UpdateError, match="doesn't match the release"):
        verify_installer(exe, filename=exe.name, expected_sha256=digest,
                         expected_size=offer.release.installer.size)  # type: ignore[union-attr]
    assert not exe.exists()


def test_checkout_refuses_to_install_and_stays_up(tmp_path: Path):
    port = free_port()
    data = tmp_path / "data"
    proc, log = start_jig(data, port)
    try:
        wait_health(port, timeout=90, proc=proc, log=log)
        base = f"http://127.0.0.1:{port}"
        assert httpx.get(f"{base}/updates", timeout=10).status_code == 401
        auth = token(data)
        about = httpx.get(f"{base}/updates", headers=auth, timeout=10)
        assert about.status_code == 200, about.text
        body = about.json()
        assert body["version"] == __version__
        assert body["kind"] == "checkout"
        assert body["can_install"] is False
        assert body["repo"] == "rlesueur/jig"
        assert "won't replace it" in body["notice"]
        refused = httpx.post(f"{base}/updates/install", headers=auth, json={"confirm": True, "version": "9.9.9"},
                             timeout=20)
        assert refused.status_code == 409, refused.text
        assert "git pull" in refused.json()["error"]
        assert "pip install -e ." in refused.json()["error"]
        assert httpx.get(f"{base}/health", timeout=5).status_code == 200
        unconfirmed = httpx.post(f"{base}/updates/install", headers=auth, json={"confirm": False, "version": "9.9.9"},
                                 timeout=10)
        assert unconfirmed.status_code == 400
        shown = httpx.post(f"{base}/updates/show", headers=auth, timeout=10)
        assert shown.status_code == 200
        status = httpx.get(f"{base}/status", headers=auth, timeout=10).json()
        assert status["show_updates"] is True
        status = httpx.get(f"{base}/status", headers=auth, timeout=10).json()
        assert "show_updates" not in status
    finally:
        kill(proc)


@pytest.mark.network
def test_check_for_updates_uses_the_real_github_api(tmp_path: Path):
    port = free_port()
    data = tmp_path / "data"
    proc, log = start_jig(data, port)
    try:
        wait_health(port, timeout=90, proc=proc, log=log)
        base = f"http://127.0.0.1:{port}"
        auth = token(data)
        checked = httpx.post(f"{base}/updates/check", headers=auth, timeout=60)
        assert checked.status_code == 200, checked.text
        body = checked.json()
        assert body["current"] == "0.1.0b1"
        assert body["up_to_date"] is True
        assert body["update"] is None
        assert body["includes_prereleases"] is True
        # Still running: a check does not turn Jig off.
        assert httpx.get(f"{base}/health", timeout=5).status_code == 200
    finally:
        kill(proc)
