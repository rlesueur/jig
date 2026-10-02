"""When a schedule runs: every so often, every day or on chosen days at a local time, or a cron expression.

A repeat is a small dict, stored with the schedule:

* ``{"kind": "interval", "interval_s": 3600}``: every hour, counted from the last run.
* ``{"kind": "daily", "at": "08:00"}``: every day at 08:00.
* ``{"kind": "weekdays", "at": "08:00"}``: Monday to Friday at 08:00.
* ``{"kind": "weekly", "days": ["mon", "thu"], "at": "08:00"}``: on those days at 08:00.
* ``{"kind": "cron", "cron": "0 8 * * 1-5"}``: a standard five-field cron expression
  (minute, hour, day of month, month, day of week; ``*``, lists, ranges, steps, and month and day names).

Calendar times are wall-clock times in the schedule's own IANA timezone, so 08:00 stays 08:00 across
daylight-saving changes. A time that a spring change skips runs at the same moment shifted by the change
(01:30 becomes 02:30); a time that an autumn change repeats runs once, the first time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MIN_INTERVAL_S = 30
KINDS = ("interval", "daily", "weekdays", "weekly", "cron")
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")  # Python's weekday() order
_DAY_WORDS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_CRON_DAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")  # cron numbers Sunday as 0 (and 7)
_MACROS = {"@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *", "@monthly": "0 0 1 * *", "@weekly": "0 0 * * 0",
           "@daily": "0 0 * * *", "@midnight": "0 0 * * *", "@hourly": "0 * * * *"}
_AT = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)")
# Far enough ahead for the rarest valid expression (29 February on a given weekday recurs within 28 years).
_SEARCH_DAYS = 366 * 29
_MISSED_CAP = 1000


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown timezone {name!r}; use an IANA name such as Europe/London") from exc


def _parse_at(value: Any) -> tuple[int, int]:
    m = _AT.fullmatch(str(value or "").strip())
    if not m:
        raise ValueError(f"the time must be HH:MM on the 24-hour clock, such as 08:00, not {value!r}")
    return int(m[1]), int(m[2])


def _field(text: str, lo: int, hi: int, names: tuple[str, ...] = (), name_base: int = 0) -> set[int]:
    def number(tok: str) -> int:
        tok = tok.lower()
        if tok in names:
            return names.index(tok) + name_base
        if not tok.isdigit():
            raise ValueError(f"{tok!r} is not a number{' or a name' if names else ''}")
        return int(tok)

    out: set[int] = set()
    for part in text.split(","):
        rng, _, step_text = part.partition("/")
        step = int(step_text) if step_text.isdigit() and int(step_text) > 0 else None
        if step_text and step is None:
            raise ValueError(f"bad step in {part!r}")
        if rng == "*":
            start, end = lo, hi
        elif "-" in rng:
            a, b = rng.split("-", 1)
            start, end = number(a), number(b)
        else:
            start = number(rng)
            end = hi if step else start
        if not (lo <= start <= hi and lo <= end <= hi) or start > end:
            raise ValueError(f"{part!r} is outside {lo}-{hi}")
        out.update(range(start, end + 1, step or 1))
    return out


@dataclass(frozen=True)
class _Calendar:
    minutes: tuple[int, ...]
    hours: tuple[int, ...]
    month_days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # Python numbering: Monday 0 to Sunday 6
    dom_any: bool
    dow_any: bool

    def day_matches(self, d: date) -> bool:
        if d.month not in self.months:
            return False
        dom, dow = d.day in self.month_days, d.weekday() in self.weekdays
        if self.dom_any and self.dow_any:
            return True
        if self.dom_any:
            return dow
        if self.dow_any:
            return dom
        return dom or dow  # cron: when both are restricted, either may match


def _parse_cron(expr: str) -> _Calendar:
    text = _MACROS.get(expr.strip().lower(), expr.strip())
    fields = text.split()
    if len(fields) != 5:
        raise ValueError(f"a cron expression has five fields (minute hour day-of-month month day-of-week), "
                         f"not {len(fields)}: {expr!r}")
    try:
        minutes = _field(fields[0], 0, 59)
        hours = _field(fields[1], 0, 23)
        dom = _field(fields[2], 1, 31)
        months = _field(fields[3], 1, 12, _MONTHS, 1)
        dow_cron = _field(fields[4], 0, 7, _CRON_DAYS, 0)
    except ValueError as exc:
        raise ValueError(f"cron expression {expr!r}: {exc}") from None
    weekdays = frozenset((d - 1) % 7 for d in dow_cron)  # cron 0 and 7 are Sunday; Python's Sunday is 6
    return _Calendar(tuple(sorted(minutes)), tuple(sorted(hours)), frozenset(dom), frozenset(months), weekdays,
                     dom_any=fields[2].startswith("*"), dow_any=fields[4].startswith("*"))  # as Vixie cron


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def interval_words(seconds: float) -> str:
    s = int(seconds)
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if s >= size and s % size == 0:
            n = s // size
            return f"Every {unit}" if n == 1 else f"Every {_plural(n, unit)}"
    return f"Every {_plural(s, 'second')}"


def _join(words: list[str]) -> str:
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}"


class Recurrence:
    """A parsed, validated repeat. ``next_after`` works in UTC; calendar matching happens in ``timezone``."""

    def __init__(self, repeat: dict[str, Any], timezone: str | None):
        if not isinstance(repeat, dict):
            raise ValueError("repeat must be an object such as {\"kind\": \"daily\", \"at\": \"08:00\"}")
        kind = repeat.get("kind")
        if kind not in KINDS:
            raise ValueError(f"repeat kind must be one of {', '.join(KINDS)}, not {kind!r}")
        self.kind: str = kind
        self.interval_s: float | None = None
        self.calendar: _Calendar | None = None
        self.timezone: str | None = None
        if kind == "interval":
            extra = set(repeat) - {"kind", "interval_s"}
            if extra:
                raise ValueError(f"an interval repeat takes only interval_s, not {sorted(extra)}")
            value = repeat.get("interval_s")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("an interval repeat needs interval_s, a number of seconds")
            if value < MIN_INTERVAL_S:
                raise ValueError(f"interval_s must be at least {MIN_INTERVAL_S} seconds")
            self.interval_s = float(value)
            self.repeat: dict[str, Any] = {"kind": kind, "interval_s": self.interval_s}
            return
        if not timezone:
            raise ValueError("a calendar repeat needs a timezone")
        _zone(timezone)
        self.timezone = timezone
        if kind == "cron":
            extra = set(repeat) - {"kind", "cron"}
            if extra:
                raise ValueError(f"a cron repeat takes only cron, not {sorted(extra)}")
            expr = str(repeat.get("cron") or "").strip()
            if not expr:
                raise ValueError("a cron repeat needs a cron expression, such as 0 8 * * 1-5")
            self.calendar = _parse_cron(expr)
            self.repeat = {"kind": kind, "cron": expr}
        else:
            allowed = {"kind", "at", "days"} if kind == "weekly" else {"kind", "at"}
            extra = set(repeat) - allowed
            if extra:
                raise ValueError(f"a {kind} repeat takes only {', '.join(sorted(allowed - {'kind'}))}, not {sorted(extra)}")
            hour, minute = _parse_at(repeat.get("at"))
            if kind == "daily":
                days = list(range(7))
            elif kind == "weekdays":
                days = list(range(5))
            else:
                raw = repeat.get("days")
                if not isinstance(raw, list) or not raw:
                    raise ValueError("a weekly repeat needs days, such as [\"mon\", \"thu\"]")
                names = [str(d).strip().lower()[:3] for d in raw]
                bad = [d for d, n in zip(raw, names, strict=True) if n not in DAY_NAMES]
                if bad:
                    raise ValueError(f"unknown day {bad[0]!r}; use mon, tue, wed, thu, fri, sat or sun")
                days = sorted({DAY_NAMES.index(n) for n in names})
            self.calendar = _Calendar((minute,), (hour,), frozenset(range(1, 32)), frozenset(range(1, 13)),
                                      frozenset(days), dom_any=True, dow_any=False)
            self.repeat = {"kind": kind, "at": f"{hour:02d}:{minute:02d}"}
            if kind == "weekly":
                self.repeat["days"] = [DAY_NAMES[d] for d in days]
        self.next_after(datetime.now(UTC))  # fails now, not at the first run, if it never matches a real date

    def next_after(self, after: datetime) -> datetime:
        """The first run strictly after ``after`` (an aware datetime), in UTC."""
        if self.interval_s is not None:
            return after.astimezone(UTC) + timedelta(seconds=self.interval_s)
        cal, tz = self.calendar, ZoneInfo(self.timezone)  # type: ignore[arg-type]
        assert cal is not None
        after = after.astimezone(UTC)
        local = after.astimezone(tz)
        day = local.date()
        for i in range(_SEARCH_DAYS):
            if cal.day_matches(day):
                for h in cal.hours:
                    if i == 0 and h < local.hour:
                        continue
                    for m in cal.minutes:
                        if i == 0 and h == local.hour and m < local.minute:
                            continue
                        # Wall-clock time; fold=0 picks the first of a repeated time and shifts a skipped one.
                        candidate = datetime(day.year, day.month, day.day, h, m, tzinfo=tz).astimezone(UTC)
                        if candidate > after:
                            return candidate
            day += timedelta(days=1)
        raise ValueError(f"{self.describe()} never matches a real date")

    def missed_between(self, due: datetime, now: datetime) -> int:
        """How many further runs fell due after ``due`` up to ``now`` (they are not replayed). Capped at 1000."""
        if self.interval_s is not None:
            return max(0, int((now - due).total_seconds() // self.interval_s))
        count, t = 0, due
        while count < _MISSED_CAP:
            t = self.next_after(t)
            if t > now:
                break
            count += 1
        return count

    def describe(self) -> str:
        """In words, for people: 'Every weekday at 08:00'. The timezone is not included."""
        r = self.repeat
        if self.kind == "interval":
            return interval_words(r["interval_s"])
        if self.kind == "daily":
            return f"Every day at {r['at']}"
        if self.kind == "weekdays":
            return f"Every weekday (Monday to Friday) at {r['at']}"
        if self.kind == "weekly":
            days = [_DAY_WORDS[DAY_NAMES.index(d)] for d in r["days"]]
            return f"Every {_join(days)} at {r['at']}"
        return f"Cron {r['cron']}"
