"""Repeated HTTP 404s for one site add a note to the tool result. The error stays the tool's own words.

Nothing is fetched from a search engine here, and no result is invented. Search is described only as installed,
switched off, or not there.
"""

from __future__ import annotations

import json
import socket

from jig.agent.fetch_nudge import (
    GUESSED_URL_NUDGE_AT,
    guessed_url_note,
    http_404_host,
    note_guessed_pages,
    search_hint,
)


class _Search:
    def __init__(self, *, installed: bool = False, enabled: bool = True, port: int | None = None):
        self._installed = installed
        self._enabled = enabled
        self.discover_port = port

    def managed_installed(self) -> bool:
        return self._installed

    def enabled(self) -> bool:
        return self._enabled


def _miss(url: str, status: int = 404) -> str:
    return json.dumps({"error": f"{url} returned HTTP {status}", "error_type": "ToolError"})


def _batch(*urls: str, status: int = 404) -> list[dict]:
    messages: list[dict] = [{"role": "assistant", "content": "", "tool_calls": []}]
    for i, url in enumerate(urls):
        messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": _miss(url, status)})
    return messages


def test_a_single_404_is_not_guessing() -> None:
    assert http_404_host(_miss("https://www.Example.com/missing")) == "example.com"
    assert http_404_host(_miss("https://example.com/missing", status=403)) is None
    assert http_404_host(json.dumps({"error": "Search isn't available.", "error_type": "ToolError"})) is None
    counts: dict[str, int] = {}
    messages = _batch("https://www.example.com/a", "https://example.com/b")
    note_guessed_pages(messages, counts, lambda: "absent")
    assert counts == {"example.com": 2}
    assert all("\n\n[Jig]" not in m["content"] for m in messages if m["role"] == "tool")


def test_three_404s_on_one_site_note_the_latest_result_when_search_is_absent() -> None:
    counts: dict[str, int] = {}
    messages = _batch(
        "https://www.bbc.co.uk/sport/a",
        "https://bbc.co.uk/sport/b",
        "https://www.bbc.co.uk/sport/c",
    )
    note_guessed_pages(messages, counts, lambda: "absent")
    assert counts == {"bbc.co.uk": GUESSED_URL_NUDGE_AT}
    noted = [m for m in messages if "Guessing web addresses is failing" in m["content"]]
    assert noted == [messages[-1]]
    text = noted[0]["content"]
    assert text.startswith('{"error":')
    error = json.loads(text.split("\n\n", 1)[0])["error"]
    assert error == "https://www.bbc.co.uk/sport/c returned HTTP 404"
    assert "Search is not installed" in text
    assert "install search in Settings" in text
    assert "Do not invent what the pages say" in text
    assert "search engine's results page" in text
    assert "DuckDuckGo" not in text
    for earlier in messages[1:-1]:
        assert "\n\n[Jig]" not in earlier["content"]


def test_different_sites_are_counted_apart() -> None:
    counts: dict[str, int] = {}
    messages = _batch(
        "https://example.com/a", "https://example.com/b",
        "https://bbc.co.uk/a", "https://bbc.co.uk/b",
    )
    note_guessed_pages(messages, counts, lambda: "absent")
    assert counts == {"example.com": 2, "bbc.co.uk": 2}
    assert all("Guessing web addresses" not in m["content"] for m in messages)


def test_a_later_batch_is_noted_from_the_running_tally() -> None:
    counts: dict[str, int] = {}
    first = _batch("https://example.com/a", "https://example.com/b")
    note_guessed_pages(first, counts, lambda: "absent")
    second = _batch("https://example.com/c")
    note_guessed_pages(second, counts, lambda: "absent")
    assert counts == {"example.com": 3}
    assert "Search is not installed" in second[-1]["content"]
    assert "Guessing web addresses" not in first[-1]["content"]


def test_the_note_matches_whether_search_is_ready_or_off() -> None:
    ready = guessed_url_note("example.com", "ready")
    off = guessed_url_note("example.com", "off")
    assert "Use web_search" in ready and "not installed" not in ready
    assert "turned off in Settings" in off and "install search" not in off
    assert "Do not invent" in ready and "Do not invent" in off
    assert "—" not in ready and "—" not in off


def test_search_hint_does_not_start_anything(monkeypatch) -> None:
    assert search_hint(None) == "absent"
    assert search_hint(_Search(installed=True, enabled=True)) == "ready"
    assert search_hint(_Search(installed=True, enabled=False, port=1)) == "off"
    assert search_hint(_Search(installed=False, enabled=False)) == "off"

    def fail(port: int) -> bool:
        raise AssertionError(f"should not probe port {port} when search is installed")

    monkeypatch.setattr("jig.agent.fetch_nudge._port_open", fail)
    assert search_hint(_Search(installed=True, enabled=True, port=8090)) == "ready"


def test_an_open_port_counts_as_ready_and_a_closed_one_as_absent() -> None:
    with socket.socket() as listening:
        listening.bind(("127.0.0.1", 0))
        listening.listen(1)
        port = listening.getsockname()[1]
        assert search_hint(_Search(port=port)) == "ready"
    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
    assert search_hint(_Search(port=port)) == "absent"
