"""Print what a Jig run did: each tool call with its arguments and what came back, from the run's session.

  python demos/connectors/inspect_run.py <run_id> [--base http://127.0.0.1:8792] [--config ...] [--token ...]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from captest import JigClient  # noqa: E402


def _short(value, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + f"... [{len(text)} chars]"


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("run_id")
    p.add_argument("--base", default="http://127.0.0.1:8792")
    p.add_argument("--config", default=r"C:\Users\you\.jig-connectors-test\jig.toml")
    p.add_argument("--token", default="")
    p.add_argument("--chars", type=int, default=600)
    a = p.parse_args()
    jig = JigClient(a.base, a.config, a.token)
    run = await jig.get(f"/runs/{a.run_id}")
    session = await jig.get(f"/sessions/{run['session_id']}")
    print(f"run {a.run_id} status={run.get('status')} session={run['session_id']}")
    for m in session.get("messages", []):
        if m.get("run_id") not in (None, a.run_id):
            continue
        role = m.get("role")
        if role == "assistant":
            for c in m.get("tool_calls") or []:
                fn = c.get("function", c)
                print(f"-> {fn.get('name')} {_short(fn.get('arguments'), a.chars)}")
            if m.get("content"):
                print(f"   says: {_short(m['content'], a.chars)}")
        elif role == "tool":
            print(f"<- {_short(m.get('content', ''), a.chars)}")
        elif role == "user":
            print(f"## user: {_short(m.get('content', ''), 200)}")


if __name__ == "__main__":
    asyncio.run(main())
