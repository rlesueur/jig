"""Refused actions: which outcomes count as a refusal, and how Jig says what it was trying to do and why it stopped.

Only what Jig itself recorded is used (the error type and the gate's policy record of each tool call), so it works
the same for any model or provider. A call that succeeded ends a run of refusals; a call that failed for another
reason (the page was not there, a bad argument) neither counts nor ends it; an approval the user has not answered
yet has no outcome, so it is not a refusal.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

# A run is stopped after this many refused actions in a row.
REFUSED_ACTION_LIMIT = 3

# What Jig was trying to do, as "it kept trying to ...".
TOOL_AIMS = {
    "web_fetch": "fetch web pages", "browser_open": "open web pages", "browser_read": "read web pages",
    "browser_screenshot": "look at web pages", "browser_click": "click on web pages", "browser_type": "type into web pages",
    "browser_fill": "fill in forms", "browser_submit": "submit forms", "browser_login": "sign in to a website",
    "list_files": "look through files", "read_file": "read files", "write_file": "save files",
    "note_write": "write notes", "note_list": "look at its notes", "memory_search": "look through its memories",
    "memory_add": "remember things", "memory_forget": "forget memories", "memory_update": "change memories",
    "schedule_create": "set up a schedule", "run_command": "run commands", "run_python": "run code",
    "gmail_send": "send email", "gmail_send_draft": "send email", "gmail_reply": "reply to email",
    "gmail_create_draft": "write email drafts", "gmail_modify_labels": "label email", "gmail_archive": "archive email",
    "gcal_create_event": "add calendar events", "outlook_create_event": "add calendar events",
    "gcal_update_event": "change calendar events", "outlook_update_event": "change calendar events",
    "gcal_cancel_event": "cancel calendar events", "outlook_cancel_event": "cancel calendar events",
    "gdrive_create_file": "save files to your Drive", "gdrive_update_file": "change files in your Drive",
    "onedrive_upload_file": "save files to your OneDrive", "github_comment": "comment on GitHub",
    "github_create_issue": "open GitHub issues", "discord_post_message": "post in Discord",
    "whatsapp_send_message": "send WhatsApp messages",
}

# Why each kind of refusal happened, as part of a sentence.
REASONS = {
    "read_only": "you're in look-don't-touch mode",
    "sentinel": "the Sentinel, Jig's safety check, said no",
    "user": "you said no",
    "rule": "one of your rules says no",
    "core": "Jig's built-in safety rules say no",
    "limit": "a limit you set for that account says no",
    "unchecked": "the Sentinel, Jig's safety check, couldn't do its check, so the answer was no",
}


def refusal_kind(outcome: dict[str, Any] | None) -> str | None:
    """Why a tool call was refused (a key of REASONS), from its recorded outcome; None if it was not refused."""
    if not outcome or outcome.get("ok"):
        return None
    policy = outcome.get("policy") or {}
    error_type = outcome.get("error_type")
    if error_type == "ModeViolation":
        return "read_only"
    if error_type == "ApprovalDenied":
        return "user"
    if error_type == "SentinelError":
        return "unchecked"
    if error_type != "PolicyBlocked":
        return None
    if (policy.get("sentinel") or {}).get("verdict") == "deny":
        return "sentinel"
    if any(f.get("decision") == "block" for f in policy.get("core") or []):
        return "core"
    if "core" in policy:
        return "rule"
    # A connector's limit is checked before the rules, so nothing else was recorded yet.
    return "limit"


def aim(tool: str) -> str:
    return TOOL_AIMS.get(tool, f"use {tool}")


def _joined(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"


def stop_message(refused: list[tuple[str, str]]) -> str:
    """The plain explanation for a run stopped after ``refused`` ((tool, kind) pairs, oldest first)."""
    aims = list(dict.fromkeys(aim(tool) for tool, _ in refused))
    kinds = [k for k, _ in Counter(kind for _, kind in refused).most_common()]
    why = "; ".join(REASONS[k] for k in kinds)  # the reasons have commas of their own
    return (f"Jig stopped because it kept trying to {_joined(aims)}, and each one was refused: {why}. "
            "What it did before that is kept.")


def stop_record(refused: list[tuple[str, str]]) -> dict[str, Any]:
    """The content-free record of the stop, for the run step and the audit log: tools and kinds only."""
    return {"refused": len(refused), "limit": REFUSED_ACTION_LIMIT, "tools": [tool for tool, _ in refused],
            "kinds": dict(Counter(kind for _, kind in refused))}
