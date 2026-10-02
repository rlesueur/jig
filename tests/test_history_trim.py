"""A conversation too long for the model's context is replayed without its oldest turns, saying so, and only in
steps, so the prompt stays the same from turn to turn in between. Nothing is deleted from the saved chat."""

from __future__ import annotations

from jig.runtime import HISTORY_SHARE, trimmed_history


def conversation(turns: int) -> list[dict]:
    out = []
    for i in range(turns):
        out.append({"role": "user", "content": f"Question {i}: " + "words " * 50})
        out.append({"role": "assistant", "tool_calls": [{"id": f"c{i}", "type": "function",
                                                         "function": {"name": "current_time", "arguments": "{}"}}]})
        out.append({"role": "tool", "tool_call_id": f"c{i}", "content": "{\"time\": \"12:00\"}"})
        out.append({"role": "assistant", "content": f"Answer {i}: " + "more " * 50})
    return out


def test_a_short_conversation_is_replayed_whole():
    history = conversation(3)
    assert trimmed_history(history, 65_536) == (history, 0)
    assert trimmed_history(history, None) == (history, 0)


def test_a_long_conversation_loses_whole_old_turns_in_steps():
    window = 4_000
    history = conversation(60)
    shown, left_out = trimmed_history(history, window)
    assert left_out > 0 and shown == history[left_out:]
    assert shown[0]["role"] == "user", "cut only where a turn starts, so every tool call keeps its result"
    assert len(str(shown)) / 3 <= HISTORY_SHARE * window * 1.1

    starts = []
    for extra in range(1, 6):
        longer = history + conversation(60 + extra)[-4 * extra:]
        starts.append(trimmed_history(longer, window)[1])
    assert len(set(starts)) <= 2, f"the cut moves only now and then, not every turn: {starts}"


async def test_a_trimmed_chat_says_what_was_left_out_and_keeps_everything(jig):
    history = conversation(40)
    jig.store.save_session("sess_trim", history)
    jig.capabilities = {**jig.capabilities, "agent": {**jig.capabilities.get("agent", {}), "context_tokens": 8_000}}
    items = [item async for item in jig.chat("Reply with the single word: ready.", session_id="sess_trim")]
    start, done = items[0], items[-1]
    assert done["type"] == "done" and done["history_trimmed"] == start["history_trimmed"] > 0, done
    run = jig.store.get_run(done["run_id"])
    first_kept = run["messages"][1]
    assert first_kept["role"] == "user" and first_kept["content"].startswith(
        f"[Jig] The first {done['history_trimmed']} messages of this conversation are not shown here")
    saved = jig.store.get_session("sess_trim")
    assert saved[:len(history)] == history, "the saved conversation keeps every message"
    trims = jig.audit.query(kind="chat.history_trimmed")
    assert trims and '"left_out": %d' % done["history_trimmed"] in trims[0]["data_json"]
