"""Long pages are read in parts or searched, and every cut is said out loud, never made silently.

Real public pages (GOV.UK's bank-holiday data, example.com) through the real web_fetch tool and a real runtime."""

from __future__ import annotations

import httpx
import pytest

from jig.constants import Mode
from jig.errors import ToolArgumentError
from jig.policy.gate import CallContext
from jig.runtime import DEPENDENCY_RESULT_CHARS

BANK_HOLIDAYS = "https://www.gov.uk/bank-holidays.json"
USER_AGENT = {"User-Agent": "Jig/0.1 (+local personal agent)"}


def _web_fetch(jig):
    spec = jig.registry.get("web_fetch")
    ctx = jig._tool_context(CallContext("r_pages", None, Mode.RESEARCH, "Find the next bank holidays."))
    return lambda **args: spec.fn(ctx, **args)


@pytest.mark.network
async def test_a_long_page_is_read_in_parts_and_says_so(jig):
    full = httpx.get(BANK_HOLIDAYS, headers=USER_AGENT).text
    limit = jig.config.web_fetch.max_chars
    assert len(full) > limit, "GOV.UK's file is expected to be longer than one web_fetch result"
    fetch = _web_fetch(jig)

    first = await fetch(url=BANK_HOLIDAYS)
    assert first["truncated"] and first["total_chars"] == len(full) and first["offset"] == 0
    assert first["text"] == full[:limit] and first["next_offset"] == limit
    assert f"offset={limit}" in first["note"] and f"of {len(full)}" in first["note"]

    parts, result = [first["text"]], first
    while "next_offset" in result:
        result = await fetch(url=BANK_HOLIDAYS, offset=result["next_offset"])
        assert result["truncated"] and result["offset"] > 0
        parts.append(result["text"])
    assert "".join(parts) == full  # nothing lost between parts
    assert "northern-ireland" in result["text"] and "offset=" not in result["note"]

    # Asking for more than the configured limit still gets one part, and the note says what is missing.
    big = await fetch(url=BANK_HOLIDAYS, max_chars=10 * limit)
    assert len(big["text"]) == limit and big["next_offset"] == limit


@pytest.mark.network
async def test_find_returns_the_passages_that_mention_a_word(jig):
    full = httpx.get(BANK_HOLIDAYS, headers=USER_AGENT).text
    fetch = _web_fetch(jig)
    found = await fetch(url=BANK_HOLIDAYS, find="St Andrew")
    assert found["matches"] == full.lower().count("st andrew") > 0 and found["passages"]
    for passage in found["passages"]:
        assert full[passage["offset"]:passage["offset"] + len(passage["text"])] == passage["text"]
        assert "st andrew" in passage["text"].lower()
    assert "note" not in found

    everywhere = await fetch(url=BANK_HOLIDAYS, find="date")  # far more than one result can hold
    assert sum(len(p["text"]) for p in everywhere["passages"]) == jig.config.web_fetch.max_chars
    assert "Read on with offset=" in everywhere["note"]

    missing = await fetch(url=BANK_HOLIDAYS, find="no such holiday xyz")
    assert missing["matches"] == 0 and not missing["passages"] and "does not appear" in missing["note"]

    with pytest.raises(ToolArgumentError, match="past the end"):
        await fetch(url=BANK_HOLIDAYS, offset=len(full) + 10)


@pytest.mark.network
async def test_a_short_page_is_whole(jig):
    page = await _web_fetch(jig)(url="https://example.com/")
    assert not page["truncated"] and "note" not in page and "next_offset" not in page
    assert page["total_chars"] == len(page["text"])


async def test_a_long_earlier_result_is_marked_where_it_was_cut(jig):
    goal = jig.store.create_goal(title="Diary", description="Find dates, then write a note.")
    long_result = "x" * (DEPENDENCY_RESULT_CHARS + 1234)
    first = jig.store.create_task(title="Find dates", description="Find them.", mode=Mode.RESEARCH, goal_id=goal["id"])
    jig.store.update_task(first["id"], result=long_result)
    second = jig.store.create_task(title="Write note", description="Write it.", mode=Mode.ACTION, goal_id=goal["id"],
                                   depends_on=[first["id"]])
    prompt, _ = jig._task_prompt(jig.store.get_task(second["id"]))
    assert f"first {DEPENDENCY_RESULT_CHARS} of {len(long_result)} characters" in prompt
    short = jig.store.create_task(title="Short", description="Short.", mode=Mode.RESEARCH, goal_id=goal["id"])
    jig.store.update_task(short["id"], result="25 December")
    third = jig.store.create_task(title="Use short", description="Use it.", mode=Mode.ACTION, goal_id=goal["id"],
                                  depends_on=[short["id"]])
    assert "Cut here" not in jig._task_prompt(jig.store.get_task(third["id"]))[0]
