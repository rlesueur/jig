"""Jig's own check that a model's reply is still going somewhere, run on the stream as it arrives.

It reads only the text Jig receives, so it works the same against llama.cpp, Ollama, LM Studio, vLLM and cloud
APIs, and it relies on no server feature (samplers, reasoning budgets, grammars) to stop a runaway.

Each part of a reply is checked on its own, every ``CHECK_EVERY`` characters: the reasoning, the text, and each
tool call's arguments. A part has stopped making progress when:

- **it repeats itself**: its last ``WINDOW`` characters compress to under ``MIN_RATIO`` of their size with zlib
  (after runs of one character, such as ``_____`` or spaces, are shortened to three), or it ends in one block
  repeated exactly, at least three times, over ``BLOCK_CHARS`` or more;
- **the answer is already complete** (structured answers only, in the part that holds the answer): a whole JSON
  object followed by more text, or a second tool call;
- **it is going round in circles** (the reasoning only): nearly every phrase in its last ``CIRCLE_WINDOW`` words
  (``CIRCLE_REUSED``, phrases of ``CIRCLE_PHRASE`` words) is one it has now written ``CIRCLE_TIMES`` times or more,
  at ``CIRCLE_LOOKS`` looks in a row, ``CIRCLE_EVERY`` words apart. A model that is stuck rewrites the same few
  paragraphs with small changes, which no exact repeat and no 1,000-character window sees. Reasoning that is getting
  somewhere keeps bringing new phrases, and a redraft (of a plan, or of code) writes its phrases a second or third
  time, not a fourth and a tenth.

Some output is repetitive by nature: code, Markdown tables, CSV, JSON lists of similar items, and tool-call
arguments (file contents, code to run). There only an exact repeat of ``GUARDED_BLOCK_CHARS`` or more counts.
When the user's message asks for repetition ("write it 50 times"), the text stops only once an exact repeat runs
well past the number asked for. A reply the user chose to continue (``relaxed``) stops only at an exact repeat of
``RELAXED_BLOCK_CHARS``.

The thresholds were tuned against real captured runs and real documents: ``scripts/measure_progress_check.py``
replays them and reports the margins. What a stop records (``Stop.record``) is content-free: the reason, the part,
the length of the repeat, the ratio, the share of phrases written before and the position, never the text."""

from __future__ import annotations

import re
import zlib
from collections import deque
from dataclasses import dataclass, field
from typing import Any

WINDOW = 1000
CHECK_EVERY = 256
MIN_RATIO = 0.10
BLOCK_CHARS = 1000
MAX_PERIOD = 1000
GUARDED_BLOCK_CHARS = 4000
RELAXED_BLOCK_CHARS = 20_000
# A repeat the user asked for may run to this many times the count asked for (plus two) before it stops.
REQUESTED_SLACK = 1.5
CIRCLE_PHRASE = 5
CIRCLE_TIMES = 4
CIRCLE_WINDOW = 600
CIRCLE_EVERY = 150
CIRCLE_REUSED = 0.8
CIRCLE_LOOKS = 4
_KEEP = RELAXED_BLOCK_CHARS + 2 * MAX_PERIOD
_RUN = re.compile(r"(.)\1{3,}", re.DOTALL)
_WORD = re.compile(r"[^\W_]+(?:'[^\W_]+)*")
_LONGEST_WORD = 100

_NUMBER_WORDS = {"three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                 "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "hundred": 100,
                 "thousand": 1000}
_TIMES = re.compile(r"\b(\d{1,5}|" + "|".join(_NUMBER_WORDS) + r")\s+times\b", re.IGNORECASE)
_REPEAT = re.compile(r"\b(repeat|repeated|repeating|over and over|again and again)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Stop:
    """Why a reply was stopped. Content-free: it may be logged, audited and shown."""

    reason: str  # "repeated_text", "repeated_block", "circling" or "answer_complete"
    part: str  # "reasoning", "content" or "tool_arguments"
    token: int  # stream chunks received when it fired (about one token each)
    chars: int  # characters of that part so far
    ratio: float | None = None  # zlib ratio of the last WINDOW characters
    period: int | None = None  # length of the repeated block
    repeat_chars: int = 0  # characters the exact repeat covers (0 when there is none)
    reused: float | None = None  # circling: the share of the last CIRCLE_WINDOW phrases written CIRCLE_TIMES times

    kind = "repetition"

    def record(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "reason": self.reason, "part": self.part, "token": self.token,
                               "chars": self.chars, "repeat_chars": self.repeat_chars}
        if self.ratio is not None:
            out["ratio"] = round(self.ratio, 4)
        if self.period is not None:
            out["period"] = self.period
        if self.reused is not None:
            out["reused"] = round(self.reused, 3)
        return out

    def describe(self, subject: str = "it") -> str:
        """What happened, in plain words and without any of the text: "it was repeating itself in its reply: ..."."""
        where = {"reasoning": "its reasoning", "content": "its reply",
                 "tool_arguments": "a tool call's arguments"}[self.part]
        at = f"at about token {self.token:,}"
        if self.reason == "answer_complete":
            return f"{subject} kept writing after its answer was complete, in {where} ({at})"
        if self.reason == "circling":
            return (f"{subject} was going round in circles in {where}: {self.reused:.0%} of the phrases in its last "
                    f"{CIRCLE_WINDOW} words were ones it had already written {CIRCLE_TIMES - 1} times or more ({at})")
        if self.period is not None and self.repeat_chars:
            return (f"{subject} was repeating itself in {where}: a {self.period:,}-character block repeated over "
                    f"{self.repeat_chars:,} characters ({at})")
        return (f"{subject} was repeating itself in {where}: its last {WINDOW:,} characters compress to "
                f"{self.ratio:.3f} of their size ({at})")


def requested_repeats(messages: list[dict[str, Any]]) -> int | None:
    """How many times the latest user message asks for something to be repeated: the number it gives, 0 when it
    asks for repetition without a number, or None when it does not ask for any."""
    text = next((_text(m.get("content")) for m in reversed(messages) if m.get("role") == "user"), "")
    counts = [int(n) if n.isdigit() else _NUMBER_WORDS[n.lower()] for n in _TIMES.findall(text)]
    if counts:
        return max(counts)
    return 0 if _REPEAT.search(text) else None


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


@dataclass
class _Part:
    name: str
    buf: str = ""
    chars: int = 0
    unchecked: int = 0
    # Inside a ``` fence (text and reasoning), from the complete lines seen so far.
    in_fence: bool = False
    line: str = ""
    # The answer-complete check: whether the part opened with "{", and whether its object has closed.
    json_started: bool | None = None
    depth: int = 0
    in_string: bool = False
    escaped: bool = False
    closed: bool = False
    # The circling check (reasoning): how many times each phrase has been written (by a hash of it, never the
    # words), whether each recent one was at least its CIRCLE_TIMES-th time, the last few words and a word still
    # arriving, and the looks so far.
    times: dict[int, int] = field(default_factory=dict)
    recent_again: deque[bool] = field(default_factory=lambda: deque(maxlen=CIRCLE_WINDOW))
    last_words: deque[str] = field(default_factory=lambda: deque(maxlen=CIRCLE_PHRASE))
    word_tail: str = ""
    phrases: int = 0
    unlooked: int = 0
    looks_over: int = 0


@dataclass
class ProgressCheck:
    """Feed it each piece of a streamed reply; it returns a ``Stop`` once the reply stops making progress."""

    # For a structured answer, the part that holds it: "content" (a JSON response format) or "tool_arguments"
    # (the respond tool). None for any other reply.
    answer: str | None = None
    relaxed: bool = False
    # From requested_repeats(): what the user asked to be repeated, if anything.
    requested: int | None = None
    tokens: int = 0
    _parts: dict[str, _Part] = field(default_factory=dict)

    def chunk(self) -> None:
        """Count one stream chunk (about one token)."""
        self.tokens += 1

    def tool_call(self, index: int) -> Stop | None:
        """A tool call has started. A structured answer is one call, so a second one means it is complete."""
        if self.answer == "tool_arguments" and index > 0:
            first = self._parts.get("tool_arguments:0")
            return Stop("answer_complete", "tool_arguments", self.tokens, first.chars if first else 0)
        return None

    def feed(self, part: str, text: str) -> Stop | None:
        """``part`` is "reasoning", "content" or "tool_arguments:<index>"."""
        if not text:
            return None
        state = self._parts.get(part)
        if state is None:
            state = self._parts[part] = _Part(part.split(":")[0])
        state.buf = (state.buf + text)[-_KEEP:]
        state.chars += len(text)
        state.unchecked += len(text)
        if state.name != "tool_arguments":
            _track_fences(state, text)
        if state.name == self.answer and _completed(state, text):
            return Stop("answer_complete", state.name, self.tokens, state.chars)
        if state.name == "reasoning" and not self.relaxed and (stop := self._circling(state, text)):
            return stop
        if state.unchecked < CHECK_EVERY or len(state.buf) < WINDOW:
            return None
        state.unchecked = 0
        return self._repetition(state)

    def _circling(self, state: _Part, text: str) -> Stop | None:
        pending = state.word_tail + text
        words = list(_WORD.finditer(pending))
        state.word_tail = ""
        if words and words[-1].end() == len(pending):  # the last word may go on in the next piece
            state.word_tail = words.pop().group()[-_LONGEST_WORD:]
        for word in words:
            state.last_words.append(word.group().lower())
            if len(state.last_words) < CIRCLE_PHRASE:
                continue
            key = hash(tuple(state.last_words))
            state.times[key] = n = state.times.get(key, 0) + 1
            state.recent_again.append(n >= CIRCLE_TIMES)
            state.phrases += 1
            state.unlooked += 1
            if state.unlooked < CIRCLE_EVERY or state.phrases < CIRCLE_WINDOW:
                continue
            state.unlooked = 0
            reused = sum(state.recent_again) / CIRCLE_WINDOW
            # Code, tables, CSV and JSON lists are repetitive by nature, as for the exact repeats.
            if reused < CIRCLE_REUSED or guarded(state):
                state.looks_over = 0
                continue
            state.looks_over += 1
            if state.looks_over >= CIRCLE_LOOKS:
                return Stop("circling", state.name, self.tokens, state.chars, reused=reused)
        return None

    def _repetition(self, state: _Part) -> Stop | None:
        period, covered = repeated_block(state.buf)

        def stop(reason: str, ratio: float | None = None) -> Stop:
            return Stop(reason, state.name, self.tokens, state.chars, ratio=ratio, period=period or None,
                        repeat_chars=covered)

        if self.relaxed:
            return stop("repeated_block") if covered >= RELAXED_BLOCK_CHARS else None
        if self.requested is not None and state.name == "content":
            if covered >= RELAXED_BLOCK_CHARS or (
                    self.requested and covered >= BLOCK_CHARS
                    and covered / period > self.requested * REQUESTED_SLACK + 2):
                return stop("repeated_block")
            return None
        if guarded(state):
            return stop("repeated_block") if covered >= GUARDED_BLOCK_CHARS else None
        if covered >= BLOCK_CHARS:
            return stop("repeated_block", window_ratio(state.buf))
        ratio = window_ratio(state.buf)
        return stop("repeated_text", ratio) if ratio is not None and ratio < MIN_RATIO else None


def window_ratio(buf: str) -> float | None:
    """zlib's compressed size over the size of the last WINDOW characters, once runs of one character are
    shortened to three; None when fewer than WINDOW characters are left (the end is mostly such runs)."""
    text = _RUN.sub(r"\1\1\1", buf[-4 * WINDOW:])[-WINDOW:]
    if len(text) < WINDOW:
        return None
    raw = text.encode("utf-8")
    return len(zlib.compress(raw, 6)) / len(raw)


def repeated_block(buf: str) -> tuple[int, int]:
    """(period, characters covered) of the shortest block that ``buf`` ends in, repeated exactly at least three
    times over at least BLOCK_CHARS; (0, 0) when there is none."""
    n = len(buf)
    end = buf[-16:]
    for p in range(1, min(MAX_PERIOD, n // 3) + 1):
        need = max(BLOCK_CHARS, 3 * p)
        if (need > n or buf[n - 1] != buf[n - 1 - p] or buf[n - 16 - p:n - p] != end
                or buf[n - need:n - p] != buf[n - need + p:]):
            continue
        start = n - need
        step = 256
        while step:
            if start - step >= 0 and buf[start - step:start] == buf[start - step + p:start + p]:
                start -= step
            else:
                step //= 2
        return p, n - start
    return 0, 0


def guarded(state: _Part) -> bool:
    """Whether the end of this part is output that is repetitive by nature: tool-call arguments, code in a fence,
    a Markdown table, CSV, or a JSON list of similar items."""
    if state.name == "tool_arguments" or state.in_fence:
        return True
    return structured_text(state.buf[-WINDOW:])


def structured_text(window: str) -> bool:
    lines = [ln.strip() for ln in window.split("\n")[1:] if ln.strip()]
    if len(lines) >= 3 and sum(ln.startswith("|") for ln in lines) >= 0.6 * len(lines):
        return True  # Markdown table
    if len(lines) >= 4:
        for sep in (",", "\t", ";"):
            counts = [ln.count(sep) for ln in lines]
            common = max(set(counts), key=counts.count)
            if common >= (1 if sep == "\t" else 2) and counts.count(common) >= 0.7 * len(lines):
                return True  # CSV or TSV
    return window.count("},") + window.count("],") >= 4  # a JSON list of similar items


def _track_fences(state: _Part, text: str) -> None:
    pending = state.line + text
    *complete, state.line = pending.split("\n")
    for line in complete:
        if line.lstrip(" ")[:3] in ("```", "~~~") and len(line) - len(line.lstrip(" ")) <= 3:
            state.in_fence = not state.in_fence
    if len(state.line) > WINDOW:
        state.line = state.line[-WINDOW:]


def _completed(state: _Part, text: str) -> bool:
    """Track a JSON answer as it streams; True once anything but white space follows its closing brace."""
    for ch in text:
        if state.closed:
            if not ch.isspace():
                return True
            continue
        if state.json_started is None:
            if ch.isspace():
                continue
            state.json_started = ch == "{"
        if not state.json_started:
            return False
        if state.in_string:
            if state.escaped:
                state.escaped = False
            elif ch == "\\":
                state.escaped = True
            elif ch == '"':
                state.in_string = False
        elif ch == '"':
            state.in_string = True
        elif ch in "{[":
            state.depth += 1
        elif ch in "}]":
            state.depth -= 1
            if state.depth == 0:
                state.closed = True
    return False
