"""Fetch non-arXiv sources and benchmark licences over HTTP, recording what was actually returned.

Writes web_sources.json. verify_citations.py only cites a [web.*] entry whose page was fetched
here with HTTP 200 and whose <title> contains the expected title.
"""

from __future__ import annotations

import json
import re
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent

# Benchmarks we may integrate or adapt: GitHub repository -> licence via the GitHub API.
REPOS = {
    "agentdojo": "ethz-spylab/agentdojo",
    "longmemeval": "xiaowu0162/LongMemEval",
    "locomo": "snap-research/locomo",
    "injecagent": "uiuc-kang-lab/InjecAgent",
    "bipia": "microsoft/BIPIA",
}
HF_DATASETS = {"longmemeval": "xiaowu0162/longmemeval-cleaned"}


def main() -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    out: dict = {"fetched_on": today, "web": {}, "licences": {}}
    web = tomllib.loads((HERE / "candidates.toml").read_text(encoding="utf-8")).get("web", {})
    with httpx.Client(headers={"User-Agent": "jig-research-lit/0.1"}, timeout=30, follow_redirects=True) as c:
        for key, w in web.items():
            r = c.get(w["url"])
            m = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.S | re.I)
            title = " ".join(m.group(1).split()) if m else ""
            out["web"][key] = {"url": w["url"], "status": r.status_code, "page_title": title,
                               "matches": r.status_code == 200 and w["title"].lower()[:40] in title.lower()}
        for key, repo in REPOS.items():
            r = c.get(f"https://api.github.com/repos/{repo}")
            data = r.json() if r.status_code == 200 else {}
            lic = (data.get("license") or {}).get("spdx_id")
            out["licences"][key] = {"repo": f"https://github.com/{repo}", "status": r.status_code, "spdx": lic}
        for key, ds in HF_DATASETS.items():
            r = c.get(f"https://huggingface.co/api/datasets/{ds}")
            data = r.json() if r.status_code == 200 else {}
            lic = next((t.split(":", 1)[1] for t in data.get("tags", []) if t.startswith("license:")), None)
            out["licences"][f"{key}_hf"] = {"dataset": f"https://huggingface.co/datasets/{ds}",
                                            "status": r.status_code, "license": lic}
    (HERE / "web_sources.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
