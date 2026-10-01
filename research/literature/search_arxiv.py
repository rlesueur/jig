"""Search arXiv (real API calls) to find candidate papers for the review.

Usage: python search_arxiv.py "all:memory AND all:poisoning AND all:agent" [max_results]
Prints id, date, title and first authors; results are candidates only and are
verified separately by verify_citations.py before being cited.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET

import httpx

ATOM = "{http://www.w3.org/2005/Atom}"


def main() -> None:
    query = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 15
    r = httpx.get("https://export.arxiv.org/api/query",
                  params={"search_query": query, "max_results": n, "sortBy": "relevance"}, timeout=60,
                  headers={"User-Agent": "jig-research-lit-search/0.1"})
    r.raise_for_status()
    for e in ET.fromstring(r.text).findall(f"{ATOM}entry"):
        aid = e.findtext(f"{ATOM}id", "").rsplit("/abs/", 1)[-1]
        title = " ".join(e.findtext(f"{ATOM}title", "").split())
        authors = [a.findtext(f"{ATOM}name", "") for a in e.findall(f"{ATOM}author")]
        print(f"{aid:18s} {e.findtext(f'{ATOM}published', '')[:10]}  {title[:110]}  | {', '.join(authors[:3])}")


if __name__ == "__main__":
    main()
