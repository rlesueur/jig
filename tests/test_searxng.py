"""SearXNG is optional and separate. Search never scrapes another engine, and install says why when the pin
cannot be fetched. No mocks: a closed local port is a real refused connection, a running SearXNG on
127.0.0.1:8090 is asked for real, and the pinned archive is the real upstream tarball."""

from __future__ import annotations

import hashlib
import io
import os
import socket
import subprocess
import sys
import tarfile
from pathlib import Path

import httpx
import pytest

from jig.constants import Mode
from jig.errors import ToolError
from jig.searxng import (ABSENT, PINNED_COMMIT, PINNED_SHA256, PINNED_URL, PINNED_VERSION, Searxng, SearxngError,
                         SearxngUnavailable, _can_import_venv, _create_embedded_venv, _safe_extract, install_dir,
                         looks_like_searxng, lookup_pinned_commit, search_router)
from jig.tools.builtin import build_registry
from jig.tools.registry import ToolContext
from jig.tools.web import search_scrape_refusal

REPO = Path(__file__).resolve().parents[1]


def _closed_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def _context(searxng: Searxng | None) -> ToolContext:
    return ToolContext(sandbox=None, memory=None, store=None, config=None, http=None, mode=Mode.RESEARCH,
                       run_id="run-search", task_id=None, searxng=searxng)


def test_searxng_folder_stays_out_of_the_checkout() -> None:
    root = install_dir(REPO / "data")
    resolved = root.resolve()
    assert resolved != REPO.resolve()
    assert REPO.resolve() not in resolved.parents


def test_searxng_folder_uses_the_data_dir_outside_the_checkout(tmp_path: Path) -> None:
    assert install_dir(tmp_path) == tmp_path.resolve() / "searxng"


def test_config_json_is_recognised_as_searxng() -> None:
    assert looks_like_searxng({"engines": [], "brand": {"GIT_URL": "https://github.com/searxng/searxng"}})
    assert not looks_like_searxng({"engines": [], "brand": {"GIT_URL": "https://example.com"}})
    assert not looks_like_searxng({"ok": True})


def test_settings_explains_install_search_in_plain_words() -> None:
    html = (REPO / "jig" / "web" / "index.html").read_text(encoding="utf-8")
    assert 'data-testid="search-install">Install search<' in html
    assert 'class="conn-steps"' in html
    assert 'data-testid="search-steps"' in html
    assert "<strong>Install search.</strong>" in html
    assert "<strong>Turn on Use search.</strong>" in html
    assert "<strong>Check it works.</strong>" in html
    assert "Nothing leaves this machine except the searches SearXNG itself sends to the search engines it uses." in html
    assert "It does not start when you sign in to Windows." in html
    assert "does not delete it or stop it." in html
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "AGPL-3.0-or-later" in readme
    assert "only when you ask" in readme or "only when you choose Install search" in readme
    assert "not part of Jig" in readme


async def test_install_says_why_when_the_pin_cannot_be_fetched(tmp_path: Path) -> None:
    """A real connection refusal, not a stand-in client. The reason names the pin and that nothing was installed."""
    port = _closed_port()
    service = Searxng(tmp_path, discover_port=port, release_url=f"http://127.0.0.1:{port}/searxng.tar.gz")
    try:
        with pytest.raises(SearxngError) as caught:
            await service.install()
    finally:
        await service.stop()
    message = str(caught.value)
    assert message.startswith(f"Couldn't fetch the pinned SearXNG release {PINNED_VERSION}:")
    assert "Nothing was installed." in message
    assert not (install_dir(tmp_path) / "venv").exists()
    assert not (install_dir(tmp_path) / "src").exists()


async def test_pinned_release_fetch_skips_with_a_clear_reason() -> None:
    """When GitHub cannot be asked for the pin, skip with that reason. When it can, the pin must match."""
    try:
        info = await lookup_pinned_commit()
    except SearxngError as exc:
        pytest.skip(str(exc))
    assert info["commit"] == PINNED_COMMIT
    assert info["version"] == PINNED_VERSION
    assert info["licence"] == "AGPL-3.0-or-later"


async def test_web_search_refuses_to_scrape_duckduckgo_when_searxng_is_absent(tmp_path: Path) -> None:
    service = Searxng(tmp_path, discover_port=_closed_port())
    try:
        with pytest.raises(SearxngUnavailable) as caught:
            await service.search("flats near the old mill")
        message = str(caught.value)
        assert message == ABSENT
        assert "DuckDuckGo" in message
        assert "haven't made up any results" in message
        assert "duckduckgo.com" not in message.lower()
        source = (REPO / "jig" / "searxng.py").read_text(encoding="utf-8")
        tool = (REPO / "jig" / "tools" / "builtin.py").read_text(encoding="utf-8")
        assert "duckduckgo.com" not in source
        assert "html.duckduckgo" not in tool
        spec = build_registry().get("web_search")
        with pytest.raises(ToolError) as tool_caught:
            await spec.fn(_context(service), query="flats near the old mill")
        assert str(tool_caught.value) == ABSENT
    finally:
        await service.stop()


async def test_search_turned_off_does_not_scrape(tmp_path: Path) -> None:
    service = Searxng(tmp_path, discover_port=_closed_port())
    service.set_enabled(False)
    try:
        with pytest.raises(SearxngUnavailable) as caught:
            await service.search("openstreetmap")
    finally:
        await service.stop()
    assert "turned off in Settings" in str(caught.value)
    assert "DuckDuckGo" in str(caught.value)


async def test_web_fetch_refuses_a_duckduckgo_results_page() -> None:
    url = "https://html.duckduckgo.com/html/?q=flats"
    message = search_scrape_refusal(url)
    assert message is not None
    assert "SearXNG" in message
    assert "invent" in message
    assert search_scrape_refusal("https://example.com/prices") is None
    spec = build_registry().get("web_fetch")
    with pytest.raises(ToolError) as caught:
        await spec.fn(_context(None), url=url)
    assert "SearXNG" in str(caught.value)
    assert "html.duckduckgo.com" not in str(caught.value)


async def test_search_uses_a_real_local_searxng_when_one_is_running(tmp_path: Path) -> None:
    service = Searxng(tmp_path)
    try:
        if not await service.is_searxng(8090):
            pytest.skip("No SearXNG is running at http://127.0.0.1:8090")
        try:
            found = await service.search("example")
        except SearxngUnavailable as exc:
            pytest.skip(str(exc))
    finally:
        await service.stop()
    assert found["via"] == "SearXNG on this computer"
    assert isinstance(found["results"], list)
    for row in found["results"]:
        assert row["url"].startswith(("http://", "https://"))
        assert "title" in row


def test_search_settings_routes_do_not_ask_for_a_query_parameter(tmp_path: Path) -> None:
    """Settings asks GET /search with no query string. The request object must not become a required parameter."""
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient

    service = Searxng(tmp_path, discover_port=_closed_port())

    def jig_of(_request: object) -> object:
        class Holder:
            searxng = service
        return Holder()

    def require_local(_request: object, _action: str) -> None:
        return None

    def require_confirm(body: object, message: str) -> None:
        if not getattr(body, "confirm", None):
            raise HTTPException(400, message)

    app = FastAPI()
    app.include_router(search_router(jig_of, require_local, require_confirm))
    with TestClient(app) as client:
        status = client.get("/search")
        assert status.status_code == 200, status.text
        assert "summary" in status.json()
        refused = client.post("/search/use", json={"confirm": False, "enabled": True})
        assert refused.status_code == 400
        assert refused.json()["detail"] == "Search was not changed"
        turned_off = client.post("/search/use", json={"confirm": True, "enabled": False})
        assert turned_off.status_code == 200
        assert turned_off.json()["enabled"] is False
        again = client.get("/search")
        assert again.status_code == 200
        assert again.json()["enabled"] is False


def _bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o644
    tar.addfile(info, io.BytesIO(data))


def _symlink(tar: tarfile.TarFile, name: str, target: str) -> None:
    info = tarfile.TarInfo(name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    tar.addfile(info)


def _hardlink(tar: tarfile.TarFile, name: str, target: str) -> None:
    info = tarfile.TarInfo(name)
    info.type = tarfile.LNKTYPE
    info.linkname = target
    tar.addfile(info)


def _special(tar: tarfile.TarFile, name: str, kind: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.devmajor = 1
    info.devminor = 3
    tar.addfile(info)


def _good_archive(path: Path) -> None:
    """A real tar: an in-folder file link, a ``..`` link that stays inside, a hardlink, and a directory link.
    The links are stored before the files they name, so unpacking cannot depend on archive order."""
    with tarfile.open(path, "w") as tar:
        _symlink(tar, "pkg/inside.txt", "hello.txt")
        _symlink(tar, "pkg/sub/up.txt", "../hello.txt")
        _hardlink(tar, "pkg/hard.txt", "pkg/hello.txt")
        _symlink(tar, "pkg/alias", "real")
        _bytes(tar, "pkg/hello.txt", b"hello")
        info = tarfile.TarInfo("pkg/real")
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        tar.addfile(info)
        _bytes(tar, "pkg/real/note.txt", b"note")


def test_unpack_materialises_links_that_stay_inside_the_archive(tmp_path: Path) -> None:
    archive = tmp_path / "good.tar"
    _good_archive(archive)
    root = _safe_extract(archive, tmp_path / "out")
    assert (root / "hello.txt").read_bytes() == b"hello"
    assert (root / "inside.txt").read_bytes() == b"hello"
    assert (root / "sub" / "up.txt").read_bytes() == b"hello"
    assert (root / "hard.txt").read_bytes() == b"hello"
    assert (root / "alias" / "note.txt").read_bytes() == b"note"
    assert (root / "real" / "note.txt").read_bytes() == b"note"
    for path in root.rglob("*"):
        assert not path.is_symlink()


def _bad_archive(path: Path, kind: str) -> None:
    with tarfile.open(path, "w") as tar:
        _bytes(tar, "pkg/hello.txt", b"hello")
        if kind == "symlink-escape":
            _symlink(tar, "pkg/escape.txt", "../../outside.txt")
        elif kind == "symlink-absolute":
            _symlink(tar, "pkg/abs.txt", "/etc/passwd")
        elif kind == "symlink-drive":
            _symlink(tar, "pkg/drive.txt", "C:/Windows/notepad.exe")
        elif kind == "hardlink-escape":
            _hardlink(tar, "pkg/escape.txt", "../outside.txt")
        elif kind == "hardlink-absolute":
            _hardlink(tar, "pkg/abs.txt", "/etc/passwd")
        elif kind == "member-dotdot":
            _bytes(tar, "pkg/../../outside.txt", b"nope")
        elif kind == "member-absolute":
            _bytes(tar, "/tmp/evil.txt", b"nope")
        elif kind == "char-device":
            _special(tar, "pkg/device", tarfile.CHRTYPE)
        elif kind == "fifo":
            _special(tar, "pkg/fifo", tarfile.FIFOTYPE)
        else:
            raise AssertionError(kind)


@pytest.mark.parametrize("kind", [
    "symlink-escape",
    "symlink-absolute",
    "symlink-drive",
    "hardlink-escape",
    "hardlink-absolute",
    "member-dotdot",
    "member-absolute",
    "char-device",
    "fifo",
])
def test_unpack_refuses_links_and_paths_that_escape(tmp_path: Path, kind: str) -> None:
    archive = tmp_path / "bad.tar"
    _bad_archive(archive, kind)
    dest = tmp_path / "out"
    with pytest.raises(SearxngError) as caught:
        _safe_extract(archive, dest)
    message = str(caught.value)
    assert message.startswith("Couldn't unpack SearXNG:")
    assert "Nothing was installed." in message
    assert list(dest.rglob("*")) == []


def _pinned_archive() -> Path:
    """The real pinned tarball. A cached copy is used only when its bytes still match the pin."""
    cache = Path(os.environ.get("TEMP") or os.environ.get("TMPDIR") or "/tmp") / "jig-searxng-pin" / f"{PINNED_SHA256}.tar.gz"
    if cache.is_file() and hashlib.sha256(cache.read_bytes()).hexdigest() == PINNED_SHA256:
        return cache
    cache.parent.mkdir(parents=True, exist_ok=True)
    partial = cache.with_suffix(".part")
    try:
        with httpx.Client(follow_redirects=True, trust_env=False, timeout=httpx.Timeout(120, connect=20),
                          headers={"User-Agent": "Jig"}) as http:
            response = http.get(PINNED_URL)
    except httpx.HTTPError as exc:
        pytest.skip(f"Couldn't fetch the pinned SearXNG archive: {type(exc).__name__}")
    if response.status_code != 200:
        pytest.skip(f"Couldn't fetch the pinned SearXNG archive: HTTP {response.status_code}")
    digest = hashlib.sha256(response.content).hexdigest()
    if digest != PINNED_SHA256:
        partial.unlink(missing_ok=True)
        pytest.fail(f"pinned archive checksum {digest} does not match {PINNED_SHA256}")
    partial.write_bytes(response.content)
    partial.replace(cache)
    return cache


def test_unpacks_the_real_pinned_searxng_archive(tmp_path: Path) -> None:
    """The pinned source archive contains an in-folder link. Unpacking copies its target and checks the pin."""
    archive = _pinned_archive()
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == PINNED_SHA256
    with tarfile.open(archive) as tar:
        links = [member for member in tar.getmembers() if member.issym() or member.islnk()]
    assert links, "the pinned archive no longer contains a link"
    root = _safe_extract(archive, tmp_path / "out")
    assert (root / "requirements.txt").is_file()
    assert (root / "searx" / "webapp.py").is_file()
    apache = root / "utils" / "templates" / "etc" / "apache2"
    httpd = root / "utils" / "templates" / "etc" / "httpd"
    assert httpd.is_dir() and not httpd.is_symlink()
    assert apache.is_dir() and not apache.is_symlink()
    assert (apache / "sites-available" / "searxng.conf").read_bytes() == (
        httpd / "sites-available" / "searxng.conf").read_bytes()
    assert not any(path.is_symlink() for path in root.rglob("*"))


def test_search_errors_use_the_error_colour() -> None:
    js = (REPO / "jig" / "web" / "app.js").read_text(encoding="utf-8")
    css = (REPO / "jig" / "web" / "style.css").read_text(encoding="utf-8")
    assert ".saved.error-text" in css
    assert "function setSaved" in js
    render = js[js.index("function renderSearch"):js.index("async function loadSearch")]
    assert "setSaved('search-saved', body.install.error, true)" in render
    mcp = js[js.index("function mcpNote"):js.index("function mcpDetail")]
    assert "setSaved('mcp-saved'" in mcp


def _bundled_python() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        pytest.skip("LOCALAPPDATA is not set")
    python = Path(local) / "Programs" / "Jig" / "python" / "python.exe"
    if not python.is_file():
        pytest.skip("Jig's installed Python is not on this computer")
    return python


def test_installed_python_has_no_venv_and_gets_its_own_interpreter(tmp_path: Path) -> None:
    """The Windows installer's Python cannot run ``python -m venv``. SearXNG still gets its own interpreter,
    and that interpreter does not see Jig's packages. Jig's install folder is only read."""
    bundled = _bundled_python()
    assert _can_import_venv(bundled) is False
    assert _can_import_venv(Path(sys.executable)) is True
    _create_embedded_venv(bundled, tmp_path / "venv")
    py = tmp_path / "venv" / "Scripts" / "python.exe"
    flags = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    proc = subprocess.run([str(py), "-s", "-c", "import encodings, ssl, sys; print('\\n'.join(sys.path))"],
                          capture_output=True, text=True, **flags)
    assert proc.returncode == 0, proc.stderr
    lowered = proc.stdout.lower().replace("/", "\\")
    assert "\\programs\\jig\\python\\lib\\site-packages" not in lowered
    blocked = subprocess.run([str(py), "-s", "-c", "import fastapi"], capture_output=True, text=True, **flags)
    assert blocked.returncode != 0
