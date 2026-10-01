"""Fetch and print arXiv abstracts (real API calls) so the review is written from the papers themselves.

Usage: python fetch_abstracts.py 2607.14611 2605.15338 ...
Also appends the raw records to abstracts_cache.jsonl (gitignored) for later reading.
"""

from __future__ import annotations

import json
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

ATOM = "{http://www.w3.org/2005/Atom}"
CACHE = Path(__file__).resolve().parent / "abstracts_cache.jsonl"


def main() -> None:
    ids = sys.argv[1:]
    sys.stdout.reconfigure(encoding="utf-8")
    with httpx.Client(headers={"User-Agent": "jig-research-lit/0.1"}, timeout=60) as client:
        for start in range(0, len(ids), 10):
            batch = ids[start:start + 10]
            r = client.get("https://export.arxiv.org/api/query",
                           params={"id_list": ",".join(batch), "max_results": len(batch)})
            r.raise_for_status()
            for e in ET.fromstring(r.text).findall(f"{ATOM}entry"):
                rec = {
                    "id": e.findtext(f"{ATOM}id", "").rsplit("/abs/", 1)[-1],
                    "title": " ".join(e.findtext(f"{ATOM}title", "").split()),
                    "authors": [a.findtext(f"{ATOM}name", "") for a in e.findall(f"{ATOM}author")],
                    "published": e.findtext(f"{ATOM}published", ""),
                    "abstract": " ".join(e.findtext(f"{ATOM}summary", "").split()),
                }
                with CACHE.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                print(f"## {rec['id']} ({rec['published'][:10]}) {rec['title']}\n{', '.join(rec['authors'][:6])}\n"
                      f"{rec['abstract']}\n")
            time.sleep(3.1)


if __name__ == "__main__":
    main()
