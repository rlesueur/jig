"""Independent checks against the real providers, for the connector capability tests.

Reads the connection tokens from the test Jig's vault and calls Gmail, Google Calendar, Google Drive, Microsoft
Graph and GitHub directly, never through Jig's agent, tools or gate, so a check sees what is really in the
account. Also sends the test emails the scenarios start from, and removes what the tests created.

Usage: python demos/connectors/provider.py <config> status
       python demos/connectors/provider.py <config> sweep <tag>   (remove what one stopped run made)
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from jig.config import load_config
from jig.connectors.base import Connectors
from jig.connectors.cli import _open
from jig.errors import ConnectorError

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
GCAL = "https://www.googleapis.com/calendar/v3"
GDRIVE = "https://www.googleapis.com/drive/v3"
GRAPH = "https://graph.microsoft.com/v1.0"
GITHUB = "https://api.github.com"

ADDRESS = "robyn.lesueur@googlemail.com"
PREFIX = "[Jig test]"
REPO = "rlesueur/jig-connector-test"
ONEDRIVE_FOLDER = "Jig test"


class Providers:
    def __init__(self, config_path: str):
        self.config = load_config(Path(config_path))
        self.db, self.store = _open(self.config)
        self.http = httpx.AsyncClient(timeout=30.0)
        self.c = Connectors(self.store, self.http, {})

    async def close(self) -> None:
        await self.http.aclose()
        self.db.close()

    async def __aenter__(self) -> "Providers":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def _json(self, name: str, method: str, url: str, **kw: Any) -> Any:
        r = await self._request(name, method, url, **kw)
        if r.status_code == 204 or not r.content:
            return None
        return r.json()

    async def _request(self, name: str, method: str, url: str, **kw: Any) -> httpx.Response:
        """Jig's connector request (which already waits up to 30 s when told to slow down), and on a quota
        error a further wait of a full minute, up to three times: Gmail's quota is counted per minute."""
        for attempt in range(4):
            try:
                return await self.c.request(name, method, url, **kw)
            except ConnectorError as exc:
                quota = exc.status == 429 or "rate-limiting" in str(exc) or "quota" in str(exc).lower()
                if not quota or attempt == 3:
                    raise
                print(f"    wait  {name} quota: waiting 60 s ({str(exc)[:120]})", flush=True)
                await asyncio.sleep(60)
        raise AssertionError("unreachable")

    # Gmail ---------------------------------------------------------------------------------------------------
    async def send_test_email(self, subject: str, body: str) -> dict[str, Any]:
        """A test email from the account to itself; the subject must carry the test prefix."""
        if not subject.startswith(PREFIX):
            raise ValueError(f"test email subjects must start with {PREFIX!r}")
        msg = EmailMessage()
        msg["To"] = ADDRESS
        msg["From"] = ADDRESS
        msg["Subject"] = subject
        msg.set_content(body)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        return await self._json("gmail", "POST", f"{GMAIL}/messages/send", json_body={"raw": raw})

    async def insert_test_email(self, sender_name: str, subject: str, body: str) -> dict[str, Any]:
        """Put an unread test email in the inbox without sending anything (Gmail's insert). It comes from the
        account's own address under another display name, so any reply Jig sends stays inside the test limits."""
        if not subject.startswith(PREFIX):
            raise ValueError(f"test email subjects must start with {PREFIX!r}")
        msg = EmailMessage()
        msg["To"] = ADDRESS
        msg["From"] = f"{sender_name} <{ADDRESS}>"
        msg["Subject"] = subject
        msg["Date"] = format_datetime(datetime.now(timezone.utc))
        msg.set_content(body)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        return await self._json("gmail", "POST", f"{GMAIL}/messages",
                                params={"internalDateSource": "dateHeader"},
                                json_body={"raw": raw, "labelIds": ["INBOX", "UNREAD"]})

    async def gmail_find(self, query: str, max_results: int = 25, *, body: bool = True) -> list[dict[str, Any]]:
        """Messages matching a Gmail search, each with id, thread id, labels, subject, to, from and (unless
        body=False, which fetches only the headers) the plain body. Every search should name a run's tag."""
        found = await self._json("gmail", "GET", f"{GMAIL}/messages", params={"q": query, "maxResults": max_results})
        return [await self.gmail_message(m["id"], body=body) for m in (found or {}).get("messages", [])]

    async def gmail_message(self, message_id: str, *, body: bool = True) -> dict[str, Any]:
        params: dict[str, Any] = {"format": "full"} if body else {
            "format": "metadata", "metadataHeaders": ["Subject", "To", "From", "In-Reply-To"]}
        m = await self._json("gmail", "GET", f"{GMAIL}/messages/{message_id}", params=params)
        headers = {h["name"].lower(): h["value"] for h in m["payload"].get("headers", [])}
        return {"id": m["id"], "thread_id": m["threadId"], "labels": m.get("labelIds", []),
                "subject": headers.get("subject", ""), "to": headers.get("to", ""), "from": headers.get("from", ""),
                "in_reply_to": headers.get("in-reply-to", ""), "internal_ms": int(m.get("internalDate", 0)),
                "body": _plain(m["payload"]) if body else ""}

    async def gmail_drafts(self, tag: str) -> list[dict[str, Any]]:
        """Drafts whose subject has a run's tag (Gmail searches drafts too), never the whole Drafts folder."""
        found = await self._json("gmail", "GET", f"{GMAIL}/drafts", params={"q": f'subject:"{tag}"', "maxResults": 20})
        out = []
        for d in (found or {}).get("drafts", []):
            msg = await self.gmail_message(d["message"]["id"])
            if tag in msg["subject"]:
                out.append({"draft_id": d["id"], **msg})
        return out

    async def gmail_trash(self, message_id: str) -> None:
        await self._json("gmail", "POST", f"{GMAIL}/messages/{message_id}/trash")

    async def gmail_delete_draft(self, draft_id: str) -> None:
        await self._json("gmail", "DELETE", f"{GMAIL}/drafts/{draft_id}")

    # Google Calendar -----------------------------------------------------------------------------------------
    async def gcal_test_calendar(self) -> dict[str, Any] | None:
        body = await self._json("google-calendar", "GET", f"{GCAL}/users/me/calendarList", params={"maxResults": 250})
        for c in (body or {}).get("items", []):
            if (c.get("summaryOverride") or c.get("summary", "")).startswith(PREFIX):
                return c
        return None

    async def gcal_events(self, calendar_id: str, time_min: str, time_max: str, q: str = "") -> list[dict[str, Any]]:
        params = {"timeMin": time_min, "timeMax": time_max, "singleEvents": "true", "orderBy": "startTime",
                  "maxResults": 250}
        if q:
            params["q"] = q
        body = await self._json("google-calendar", "GET", f"{GCAL}/calendars/{quote(calendar_id, safe='')}/events",
                                params=params)
        return (body or {}).get("items", [])

    async def gcal_delete(self, calendar_id: str, event_id: str) -> None:
        await self._json("google-calendar", "DELETE",
                         f"{GCAL}/calendars/{quote(calendar_id, safe='')}/events/{quote(event_id, safe='')}",
                         params={"sendUpdates": "none"})

    async def gcal_create(self, calendar_id: str, summary: str, start: str, end: str) -> dict[str, Any]:
        if not summary.startswith(PREFIX):
            raise ValueError(f"test events must start with {PREFIX!r}")
        return await self._json("google-calendar", "POST", f"{GCAL}/calendars/{quote(calendar_id, safe='')}/events",
                                params={"sendUpdates": "none"},
                                json_body={"summary": summary, "start": {"dateTime": start},
                                           "end": {"dateTime": end}})

    # Google Drive (drive.file: only files Jig's app created are visible to writes) -----------------------------
    async def gdrive_find(self, name_contains: str) -> list[dict[str, Any]]:
        q = f"name contains '{name_contains.replace(chr(39), ' ')}' and trashed = false"
        body = await self._json("google-drive", "GET", f"{GDRIVE}/files",
                                params={"q": q, "fields": "files(id,name,mimeType,createdTime,parents,size)",
                                        "pageSize": 100})
        return (body or {}).get("files", [])

    async def gdrive_text(self, file_id: str) -> str:
        r = await self.c.request("google-drive", "GET", f"{GDRIVE}/files/{file_id}", params={"alt": "media"})
        return r.text

    async def gdrive_delete(self, file_id: str) -> None:
        await self._json("google-drive", "DELETE", f"{GDRIVE}/files/{file_id}")

    # Microsoft -----------------------------------------------------------------------------------------------
    async def outlook_test_calendar(self) -> dict[str, Any] | None:
        body = await self._json("microsoft", "GET", f"{GRAPH}/me/calendars", params={"$top": 100})
        for c in (body or {}).get("value", []):
            if c.get("name", "").startswith(PREFIX):
                return c
        return None

    async def outlook_events(self, calendar_id: str, start: str, end: str) -> list[dict[str, Any]]:
        body = await self._json("microsoft", "GET", f"{GRAPH}/me/calendars/{calendar_id}/calendarView",
                                params={"startDateTime": start, "endDateTime": end, "$top": 200},
                                headers={"Prefer": 'outlook.timezone="Europe/London"'})
        return (body or {}).get("value", [])

    async def outlook_delete(self, calendar_id: str, event_id: str) -> None:
        await self._json("microsoft", "DELETE", f"{GRAPH}/me/calendars/{calendar_id}/events/{event_id}")

    async def outlook_create(self, calendar_id: str, subject: str, start: str, end: str) -> dict[str, Any]:
        if not subject.startswith(PREFIX):
            raise ValueError(f"test events must start with {PREFIX!r}")
        return await self._json("microsoft", "POST", f"{GRAPH}/me/calendars/{calendar_id}/events",
                                json_body={"subject": subject,
                                           "start": {"dateTime": start, "timeZone": "Europe/London"},
                                           "end": {"dateTime": end, "timeZone": "Europe/London"}})

    async def onedrive_children(self) -> list[dict[str, Any]]:
        body = await self._json("microsoft", "GET", f"{GRAPH}/me/drive/root:/{quote(ONEDRIVE_FOLDER)}:/children",
                                params={"$top": 200})
        return (body or {}).get("value", [])

    async def onedrive_text(self, item_id: str) -> str:
        r = await self.c.request("microsoft", "GET", f"{GRAPH}/me/drive/items/{item_id}/content")
        if r.status_code in (301, 302) and "location" in r.headers:
            r = await self.http.get(r.headers["location"])
        return r.text

    async def onedrive_delete(self, item_id: str) -> None:
        await self._json("microsoft", "DELETE", f"{GRAPH}/me/drive/items/{item_id}")

    # GitHub --------------------------------------------------------------------------------------------------
    async def github_issues(self, state: str = "all") -> list[dict[str, Any]]:
        return await self._json("github", "GET", f"{GITHUB}/repos/{REPO}/issues",
                                params={"state": state, "per_page": 100}) or []

    async def github_comments(self, number: int) -> list[dict[str, Any]]:
        return await self._json("github", "GET", f"{GITHUB}/repos/{REPO}/issues/{number}/comments",
                                params={"per_page": 100}) or []

    async def github_close(self, number: int) -> None:
        await self._json("github", "PATCH", f"{GITHUB}/repos/{REPO}/issues/{number}", json_body={"state": "closed"})


def _plain(payload: dict[str, Any]) -> str:
    if payload.get("mimeType", "").startswith("text/plain") and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"] + "==").decode("utf-8", "replace")
    for part in payload.get("parts", []) or []:
        text = _plain(part)
        if text:
            return text
    return ""


async def _status(config_path: str) -> None:
    async with Providers(config_path) as p:
        out: dict[str, Any] = {}
        cal = await p.gcal_test_calendar()
        out["google_test_calendar"] = cal and {"id": cal["id"], "name": cal.get("summary"), "role": cal.get("accessRole")}
        ocal = await p.outlook_test_calendar()
        out["outlook_test_calendar"] = ocal and {"id": ocal["id"][:24] + "...", "name": ocal.get("name"),
                                                 "can_edit": ocal.get("canEdit")}
        try:
            out["onedrive_jig_test"] = [c["name"] for c in await p.onedrive_children()]
        except Exception as exc:  # noqa: BLE001 - reported, not hidden
            out["onedrive_jig_test"] = f"error: {exc}"
        out["gmail_test_messages"] = [(m["subject"], m["labels"]) for m in await p.gmail_find(f'subject:"{PREFIX}"', 10, body=False)]
        out["gdrive_test_files"] = [f["name"] for f in await p.gdrive_find(PREFIX)]
        out["github_issues"] = [(i["number"], i["state"], i["title"]) for i in await p.github_issues()]
        print(json.dumps(out, indent=2, ensure_ascii=False))


async def _sweep(config_path: str, tag: str) -> None:
    """Remove what one capability run (by its tag) made, for runs stopped before their own clean-up."""
    from datetime import timedelta
    mine = lambda name: PREFIX in (name or "") and f"({tag})" in (name or "")  # noqa: E731
    async with Providers(config_path) as p:
        done: list[str] = []
        for m in await p.gmail_find(f'subject:"{tag}"', 50, body=False):
            if mine(m["subject"]) and "TRASH" not in m["labels"]:
                await p.gmail_trash(m["id"])
                done.append(f"mail {m['subject']}")
        for d in await p.gmail_drafts(tag):
            if mine(d["subject"]):
                await p.gmail_delete_draft(d["draft_id"])
                done.append(f"draft {d['subject']}")
        now = datetime.now(timezone.utc)
        lo, hi = (now - timedelta(days=3)).isoformat(), (now + timedelta(days=40)).isoformat()
        if cal := await p.gcal_test_calendar():
            for e in await p.gcal_events(cal["id"], lo, hi):
                if mine(e.get("summary")) or tag in (e.get("description") or ""):
                    await p.gcal_delete(cal["id"], e["id"])
                    done.append(f"google event {e.get('summary')}")
        if cal := await p.outlook_test_calendar():
            for e in await p.outlook_events(cal["id"], lo, hi):
                if mine(e.get("subject")):
                    await p.outlook_delete(cal["id"], e["id"])
                    done.append(f"outlook event {e.get('subject')}")
        for f in await p.gdrive_find(tag):
            if mine(f["name"]):
                await p.gdrive_delete(f["id"])
                done.append(f"drive {f['name']}")
        for f in await p.onedrive_children():
            if mine(f["name"]):
                await p.onedrive_delete(f["id"])
                done.append(f"onedrive {f['name']}")
        for i in await p.github_issues(state="open"):
            if mine(i["title"]):
                await p.github_close(i["number"])
                done.append(f"closed issue #{i['number']} {i['title']}")
        print(json.dumps(done, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[2] == "sweep":
        asyncio.run(_sweep(sys.argv[1], sys.argv[3]))
    elif len(sys.argv) == 3 and sys.argv[2] == "status":
        asyncio.run(_status(sys.argv[1]))
    else:
        sys.exit(__doc__)
