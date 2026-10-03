"""SearXNG is optional and separate. Search never scrapes another engine, and install says why when the pin
cannot be fetched. No mocks: a closed local port is a real refused connection, and a running SearXNG on
127.0.0.1:8090 is asked for real."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from jig.constants import Mode
from jig.errors import ToolError
from jig.searxng import (ABSENT, PINNED_COMMIT, PINNED_VERSION, Searxng, SearxngError, SearxngUnavailable,
                         install_dir, looks_like_searxng, lookup_pinned_commit, search_router)
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
