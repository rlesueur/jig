"""Claim check: does a reply say Jig did something that the run's own tool records show it did not do?

After a reply, its sentences are read for claims that an action was done: saved or written, sent, booked or
scheduled, created, deleted, ordered or paid. Each claim is compared with the run's tool records (tool name, target
and outcome), never with another model's opinion, so it works the same for any model. A claim is ``not_done`` when no
call of that kind succeeded and one was refused or failed, or when none was attempted at all although the request
asked for it. Those get a plain note under the reply.

It is built for precision over recall: a sentence is only read as a claim when it says the action is done (first
person, done passive, or starting with the verb: "I've saved", "has been sent", "Saved to shopping.md"), and never
when it is negated, conditional, a question, a plan, quoted, or about an earlier time. A claim that is not matched
either way is ``unclear`` and gets no note.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from .agent.refusals import REASONS, refusal_kind

VERSION = 1

# What each tool does when it succeeds: the kinds of claim it supports, and what it acts on.
_SAVE, _SEND, _BOOK, _CREATE, _DELETE, _ORDER = "save", "send", "book", "create", "delete", "order"
TOOL_ACTS: dict[str, tuple[frozenset[str], str]] = {}
for _names, _kinds, _object in (
    (("write_file", "gdrive_create_file", "onedrive_upload_file"), (_SAVE, _CREATE), "file"),
    (("gdrive_update_file",), (_SAVE,), "file"),
    (("note_write",), (_SAVE, _CREATE), "note"),
    (("memory_add", "memory_update"), (_SAVE,), "memory"),
    (("memory_forget",), (_DELETE,), "memory"),
    (("gmail_create_draft",), (_SAVE, _CREATE), "draft"),
    (("gmail_send", "gmail_send_draft", "gmail_reply"), (_SEND,), "email"),
    (("slack_post_message", "slack_reply_in_thread", "discord_post_message", "matrix_send_message",
      "signal_send_message"), (_SEND,), "message"),
    (("github_comment",), (_SEND, _CREATE), "comment"),
    (("github_create_issue",), (_CREATE,), "issue"),
    (("gcal_create_event", "outlook_create_event"), (_BOOK, _CREATE), "event"),
    (("gcal_update_event", "outlook_update_event"), (_BOOK,), "event"),
    (("gcal_cancel_event", "outlook_cancel_event"), (_DELETE,), "event"),
    (("schedule_create",), (_BOOK, _CREATE), "schedule"),
):
    for _name in _names:
        TOOL_ACTS[_name] = (frozenset(_kinds), _object)
# Code can write, create and delete files without saying which, so a run of it that succeeded supports those
# claims about files; a browser click or submit is an order only when Jig recognised a checkout.
_CODE_TOOLS = frozenset({"run_command", "run_python"})
_CHECKOUT_TOOLS = frozenset({"browser_click", "browser_submit"})
# Objects that are the same thing for a claim ("the email" is a message; a calendar event can be a schedule).
_SAME = {"email": {"email", "message", "draft"}, "message": {"email", "message", "comment"},
         "comment": {"comment", "message"},
         "event": {"event", "schedule"}, "schedule": {"schedule", "event"}}
# Kinds that change something of the user's, refused in look-don't-touch mode (notes and memories are Jig's own).
_PRIVATE = {"note", "memory"}

# A claim: a done form of one of the kind's verbs. Verbs with objects count only when the sentence names one.
_FILE = r"[^\s\"'`“”‘’<>|*?]*[^\s\"'`“”‘’<>|*?.]\.(?:md|txt|py|csv|json|html?|js|ts|ya?ml|toml|pdf|docx?|xlsx?|pptx?|ipynb|log|ini|cfg|sh|ps1)\b"
_OBJECTS = {
    "file": rf"{_FILE}|\b(?:files?|folders?|workspace|documents?|docs?|spreadsheets?|drive|onedrive)\b",
    "note": r"\bnotes?\b|\bnoted\b",
    "memory": r"\bmemor(?:y|ies)\b|\bremember(?:ed)?\b",
    "draft": r"\bdrafts?\b",
    "email": r"\be-?mails?\b|\bmail\b|\brepl(?:y|ies)\b|\binbox\b",
    "message": r"\bmessages?\b|\bslack\b|\bdiscord\b|\bsignal\b|\bmatrix\b|\bchannel\b",
    "comment": r"\bcomments?\b",
    "issue": r"\bissues?\b",
    "event": r"\bevents?\b|\bmeetings?\b|\bcalendar\b|\bappointments?\b|\binvites?\b|\binvitations?\b|\brehearsal\b"
             r"|\bbookings?\b",
    "schedule": r"\bschedules?\b",
    "order": r"\borders?\b|\bpurchases?\b|\bpayments?\b|\bcheckout\b",
}
_KIND_VERBS: dict[str, list[tuple[str, tuple[str, ...]]]] = {
    _SAVE: [(r"saved|wrote|written|stored|recorded|jotted", ()),
            (r"updated|added|appended|put|noted|created|made", ("file", "note", "memory", "draft"))],
    _SEND: [(r"sent|e-?mailed|replied|forwarded|messaged|delivered", ()),
            (r"posted|shared", ("message", "comment", "email"))],
    _BOOK: [(r"booked|scheduled|reserved", ()),
            (r"added|put|created|placed|set\s+up|made|updated|moved", ("event", "schedule"))],
    _CREATE: [(r"opened|raised|filed", ("issue",)),
              (r"created|made|set\s+up|started|added|left|posted|submitted",
               ("issue", "comment", "file", "draft", "event", "schedule", "note"))],
    _DELETE: [(r"deleted|removed|cancell?ed|erased|forgot(?:ten)?|trashed|cleared|wiped", ())],
    _ORDER: [(r"ordered|purchased|bought|paid|checked\s+out", ()),
             (r"placed|completed|submitted|confirmed|finished|made|finali[sz]ed", ("order",))],
}
_SUBJECT = r"(?:\b(?:i|we)(?:['’]ve|\s+have|\s+had|\s+just|\s+now|\s+also|\s+then|\s+successfully)*\s+)"
_PASSIVE = (r"(?:\b(?:has|have)(?:\s+(?:now|also|successfully))?\s+been(?:\s+(?:now|also|successfully))?\s+"
            r"|\b(?:is|are|was|were|it['’]s|that['’]s)(?:\s+(?:now|also|successfully|all))?\s+)")
_LEAD = r"^(?:done|all\s+done|ok|okay|great|all\s+set|right)?[\s\W]*(?:and\s+)?"
_HEAD = r"^(?:done|ok|okay|great|all\s+set)?[\s\W]*(?:(?:the|your|my)\s+)?(?:[\w.@()\[\]'’-]+\s+){0,3}?"
_STATES = {_SAVE: r"\bnow\s+(?:contains|has|includes|lists|holds|reads|shows)\b",
           _BOOK: r"\b(?:is|are)\s+(?:now\s+)?(?:in|on)\s+your\s+calendar\b"}
_NOT_A_CLAIM = re.compile(
    # negated, conditional, a question or a plan
    r"\b(?:not|never|nothing|unable|cannot|can['’]t|couldn['’]t|without|instead\s+of|rather\s+than|no\s+longer)\b"
    r"|n['’]t\b|\bno\s+(?:\w+\s+){0,3}(?:was|were|has|have|had|is|are)\b|^\W*no\b|\bnone\b"
    r"|\b(?:if|once|unless|would|could|should|shall|will|might|may|can|when|whenever|after|before|until|ready\s+to"
    r"|going\s+to|want\s+me|let\s+me|like\s+me|need\s+to|needs\s+to|try\s+to|tried\s+to|attempted|attempt|meant\s+to"
    r"|supposed\s+to|plan|planned|next)\b|['’]ll\b|\?\s*\W*$"
    # about an earlier time, or someone else's words
    r"|\b(?:earlier|previously|already|yesterday|last\s+time|last\s+week|before\s+now|says|said|according\s+to"
    r"|claims?|reports?|reported|shows\s+that)\b"
    # a heading or a description of what something is
    r"|^\W*(?:what|which|where|how|why|who)\b",
    re.IGNORECASE)
_SOMEONE_ELSE = r"(?!\s+(?:from|by|to\s+you\s+by)\b)"
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_FILE_RE = re.compile(_FILE, re.IGNORECASE)
# The request asked for an action of this kind (only then is a claim with no attempt at all read as not done).
_ASKED = {
    _SAVE: r"\b(?:save|write|store|add|record|update|create|jot)\b",
    _SEND: r"\b(?:send|e-?mail|reply|respond|forward|post|notify)\b",
    _BOOK: r"\b(?:book|schedule|reserve|invite|set\s+up|put\s+.{0,40}\b(?:in|on)\s+(?:my|the|our)\s+calendar"
           r"|add\s+.{0,40}\bto\s+(?:my|the|our)\s+calendar)\b",
    _CREATE: r"\b(?:create|make|file|raise|add|set\s+up|open\s+(?:an?|the)\s+issue)\b",
    _DELETE: r"\b(?:delete|remove|cancel|forget|erase|trash|wipe)\b",
    _ORDER: r"\b(?:order|buy|purchase|pay|checkout|check\s+out)\b",
}

# Words for the note under the reply.
_VERB_WORDS = {_SAVE: "save", _SEND: "send", _BOOK: "book", _CREATE: "create", _DELETE: "delete", _ORDER: "order"}
_OBJECT_WORDS = {"file": "the file", "note": "the note", "memory": "the memory", "draft": "the draft",
                 "email": "this email", "message": "this message", "comment": "the comment", "issue": "the issue",
                 "event": "the event", "schedule": "the schedule", "order": "the order"}
_NOTHING = {_SAVE: "nothing was written", _SEND: "nothing was sent", _BOOK: "nothing was booked",
            _CREATE: "nothing was created", _DELETE: "nothing was deleted", _ORDER: "no order was placed"}


def _plain(text: str) -> str:
    """The reply without what it quotes: code blocks, quoted lines and text in quotation marks. Inline code is
    kept (it often names the file), without its backticks."""
    text = re.sub(r"```.*?(?:```|\Z)", " ", text or "", flags=re.DOTALL)
    text = re.sub(r"^\s*>.*$", " ", text, flags=re.MULTILINE)
    text = re.sub(r"[\"“][^\"”\n]{0,400}[\"”]", " ", text)
    return text.replace("`", "")


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|[;\n]+|\s+(?=but\s)|\s+[–—-]\s+|:\s+(?=[A-Z])", _plain(text))
    return [p.strip(" \t*_#>|-•") for p in parts if p and re.search(r"\w", p)]


def _objects(sentence: str) -> set[str]:
    return {name for name, pattern in _OBJECTS.items() if re.search(pattern, sentence, re.IGNORECASE)}


def _targets(text: str) -> set[str]:
    """Specific things a sentence or tool argument names: file names and email addresses, lower case."""
    found = {m.group(0).lower() for m in _EMAIL.finditer(text)}
    found |= {m.group(0).rsplit("/", 1)[-1].lower() for m in _FILE_RE.finditer(text) if "@" not in m.group(0)}
    return found


def claims_in(text: str) -> list[dict[str, Any]]:
    """Each sentence of ``text`` that says an action is done, with its kind, the objects and targets it names."""
    out = []
    for sentence in sentences(text):
        if _NOT_A_CLAIM.search(sentence):
            continue
        objects = _objects(sentence)
        for kind, verbs in _KIND_VERBS.items():
            verb = _claim_verb(sentence, verbs, objects)
            if not verb and kind in _STATES and (kind != _SAVE or "file" in objects):
                verb = (m := re.search(_STATES[kind], sentence, re.IGNORECASE)) and m.group(0)
            if verb:
                out.append({"kind": kind, "sentence": sentence, "verb": verb.strip(), "objects": sorted(objects),
                            "targets": sorted(_targets(sentence))})
    return out


def _claim_verb(sentence: str, verbs: list[tuple[str, tuple[str, ...]]], objects: set[str]) -> str | None:
    for pattern, needs in verbs:
        if needs and not objects & set(needs):
            continue
        verb = rf"(?:{pattern})\b{_SOMEONE_ELSE}"
        for form in (_SUBJECT + verb, _PASSIVE + verb, _LEAD + verb,
                     rf"\b(?:i|we)\b.*\band\s+(?:then\s+|also\s+)?{verb}"):
            if m := re.search(form, sentence, re.IGNORECASE):
                return m.group(0)
        # A headline that names the thing first: "Order placed.", "Email sent to Priya."
        if (m := re.search(_HEAD + rf"({pattern})\b{_SOMEONE_ELSE}", sentence, re.IGNORECASE)) and _objects(
                sentence[:m.start(1)]):
            return m.group(0)
    return None


def tool_records(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The run's tool calls from its step records: tool, arguments, targets and outcome (the last record of a call
    that ran again after a pause). A call still waiting for the user has no outcome and is left out."""
    calls: dict[str, dict[str, Any]] = {}
    for step in steps:
        if step["type"] != "tool_call" or step["status"] not in ("ok", "error"):
            continue
        raw = (step.get("input") or {}).get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except ValueError:
            args = {}
        output = step.get("output") or {}
        arg_text = " ".join(str(v) for v in args.values() if isinstance(v, (str, list))) if isinstance(args, dict) else ""
        calls[(step.get("input") or {}).get("id") or str(step["id"])] = {
            "tool": step["name"], "ok": step["status"] == "ok" and bool(output.get("ok", True)),
            "refused": refusal_kind(output), "error_type": output.get("error_type"),
            "checkout": bool(((output.get("policy") or {}).get("resolved") or {}).get("checkout")),
            "targets": _targets(arg_text), "objects": _objects(arg_text)}
    return list(calls.values())


def _acts(record: dict[str, Any]) -> tuple[frozenset[str], str | None]:
    if record["tool"] in TOOL_ACTS:
        return TOOL_ACTS[record["tool"]]
    if record["tool"] in _CODE_TOOLS:
        return frozenset({_SAVE, _CREATE, _DELETE}), "code"
    if record["tool"] in _CHECKOUT_TOOLS and record["checkout"]:
        return frozenset({_ORDER}), "order"
    return frozenset(), None


def _fits(record: dict[str, Any], claim: dict[str, Any], *, targets: bool = True) -> bool:
    """Whether a tool call is of the claim's kind, on the same sort of thing, and (with ``targets``) not on a
    different named target. Code only fits a claim about files."""
    kinds, obj = _acts(record)
    if claim["kind"] not in kinds:
        return False
    objects = set(claim["objects"])
    if obj == "code":
        if "file" not in objects:
            return False
    elif objects and obj is not None and not objects & _SAME.get(obj, {obj}):
        return False
    return not (targets and claim["targets"] and record["targets"]
                and not set(claim["targets"]) & record["targets"])


def _judge(claim: dict[str, Any], records: list[dict[str, Any]], mode: str, request: str) -> dict[str, Any]:
    named = set(claim["targets"])
    strict = [r for r in records if _fits(r, claim)]
    loose = [r for r in records if _fits(r, claim, targets=False)]
    done = [r for r in strict if r["ok"]]
    bad = sorted((r for r in strict if not r["ok"]), key=lambda r: (not named & r["targets"], r["refused"] is None))
    if done:
        return {"status": "done", "tool": done[0]["tool"]}
    if bad and (not any(r["ok"] for r in loose) or named & bad[0]["targets"]):
        return {"status": "not_done", "tool": bad[0]["tool"], "why": bad[0]["refused"] or "failed",
                "evidence": "same target" if named & bad[0]["targets"] else "same kind"}
    # Nothing of the kind was attempted: only a claim about something specific, made when the request asked for
    # it, counts. Code that ran could have done anything to files, and a claim that names what another action did
    # ("noted it in the event") is part of that action.
    code_ran = any(r["ok"] and r["tool"] in _CODE_TOOLS for r in records)
    acted_on = {_acts(r)[1] for r in records if r["ok"] and _acts(r)[0]}
    if (not loose and (claim["objects"] or named) and re.search(_ASKED[claim["kind"]], request, re.IGNORECASE)
            and not (code_ran and (not claim["objects"] or "file" in claim["objects"]))
            and not set(claim["objects"]) & acted_on):
        changes_yours = named or set(claim["objects"]) - _PRIVATE
        return {"status": "not_done", "tool": None, "evidence": "not attempted",
                "why": "read_only" if mode == "research" and changes_yours else "never"}
    return {"status": "unclear", "tool": None}


def check(reply: str, steps: list[dict[str, Any]], *, mode: str, request: str = "") -> dict[str, Any]:
    """Compare the claims in ``reply`` with the run's tool records (``steps``: its run step records). ``request`` is
    what the user asked for in this run; ``mode`` the run's mode ("research" is look-don't-touch)."""
    records = tool_records(steps)
    claims = [claim | _judge(claim, records, mode, request) for claim in claims_in(reply)]
    kept_note = any(r["ok"] and r["tool"] == "note_write" for r in records)
    # One note for each kind of claim (saving and creating count as one), about the most specific one.
    flagged: dict[str, dict[str, Any]] = {}
    for c in claims:
        family = _SAVE if c["kind"] == _CREATE else c["kind"]
        if c["status"] == "not_done" and (family not in flagged or c["targets"] and not flagged[family]["targets"]):
            flagged[family] = c
    notes = list(dict.fromkeys(_note(c, kept_note) for c in flagged.values()))
    return {"version": VERSION, "claims": claims, "notes": notes,
            "counts": dict(Counter(f"{c['kind']}:{c['status']}" for c in claims))}


def _note(claim: dict[str, Any], kept_note: bool) -> str:
    kind, objects = claim["kind"], claim["objects"]
    files = [t for t in claim["targets"] if "@" not in t]
    people = [t for t in claim["targets"] if "@" in t]
    if kind in (_SAVE, _CREATE) and files:
        what = f"{_VERB_WORDS[kind]} {files[0]}"
    elif kind == _SEND and people:
        what = f"send this to {people[0]}"
    elif kind == _BOOK:
        what = "book this"
    elif kind == _ORDER:
        what = "place this order"
    else:
        obj = next((o for o in ("email", "message", "issue", "comment", "event", "draft", "note", "file", "memory",
                                "schedule") if o in objects), None)
        what = f"{_VERB_WORDS[kind]} {_OBJECT_WORDS[obj] if obj else 'this'}"
    why = claim["why"]
    if why == "read_only":
        reason = f"{REASONS['read_only']}, so {_NOTHING[kind]}"
    elif why == "never":
        reason = f"it never tried to, so {_NOTHING[kind]}"
    elif why == "failed":
        reason = f"it went wrong, so {_NOTHING[kind]}"
    else:
        reason = REASONS[why]
    extra = (" It kept a private note instead." if kept_note and kind == _SAVE and "note" not in objects
             and why in ("read_only", "never") else "")
    return f"Jig didn't actually {what}: {reason}.{extra}"


def record(result: dict[str, Any]) -> dict[str, Any]:
    """The check for the audit log: counts by kind and outcome only, no words from the reply."""
    return {"claims": len(result["claims"]), "notes": len(result["notes"]), "counts": result["counts"],
            "why": dict(Counter(c["why"] for c in result["claims"] if c["status"] == "not_done"))}
