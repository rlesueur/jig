"""web_fetch treats only HTTP 200 as a page. A 202 from a search engine is a failed fetch, not results."""

from __future__ import annotations

from jig.tools.web import page_status_error


def test_http_200_is_a_page() -> None:
    assert page_status_error("https://example.com/prices", 200) is None


def test_http_202_is_a_failed_fetch() -> None:
    """DuckDuckGo answers a blocked search with 202 and a challenge, not with results."""
    message = page_status_error("https://html.duckduckgo.com/html/", 202)
    assert message == "https://html.duckduckgo.com/html/ returned HTTP 202"


def test_http_403_is_a_failed_fetch() -> None:
    message = page_status_error("https://www.argos.co.uk/search/", 403)
    assert message == "https://www.argos.co.uk/search/ returned HTTP 403"
