"""Plain-English steps for connecting each account, shown in Settings > Connections.

Each guide has a one-line ``summary``, the ``steps`` in order (a step may link to the page on the
provider's site where it happens) and, for some, an ``advanced`` route. They describe the same setup as
docs/connectors-setup.md, without the technical detail.
"""

from __future__ import annotations

from typing import Any


def _step(text: str, *links: tuple[str, str]) -> dict[str, Any]:
    """One step, with (url, label) links to where it happens."""
    return {"text": text, "links": [{"url": u, "label": label} for u, label in links]}


_GOOGLE_SETUP = [
    _step("Google needs a one-off setup before the first connection: you make your own small Google app. It's "
          "free, only you use it, and it covers Gmail, Calendar and Drive together. Sign in to Google Cloud with "
          "the Google account you want Jig to use, and create a project (call it Jig).",
          ("https://console.cloud.google.com/projectcreate", "Create a project")),
    _step("Switch on the parts of Google that Jig uses. Open each link and choose Enable (just the ones you "
          "want).", ("https://console.cloud.google.com/apis/library/gmail.googleapis.com", "Gmail"),
          ("https://console.cloud.google.com/apis/library/calendar-json.googleapis.com", "Google Calendar"),
          ("https://console.cloud.google.com/apis/library/drive.googleapis.com", "Google Drive")),
    _step("Set up the sign-in page: choose Get started, call the app Jig, pick your email address, choose "
          "External, add your email address again as the contact, agree, and choose Create.",
          ("https://console.cloud.google.com/auth/overview", "Open Google Auth Platform")),
    _step("Open Audience and choose Publish app, then Confirm, so the status says In production. (If you "
          "leave it in Testing, Google disconnects Jig every 7 days.)",
          ("https://console.cloud.google.com/auth/audience", "Open Audience")),
    _step("Open Clients and choose Create client. For Application type pick Desktop app, call it Jig, and "
          "choose Create. In the box that appears, choose Download JSON.",
          ("https://console.cloud.google.com/auth/clients", "Open Clients")),
    _step("Back here, choose 'Choose the downloaded file' and pick the file you just downloaded. Jig keeps it "
          "in its vault on this computer; you can then delete the download."),
]
_GOOGLE_CONNECT = _step(
    "Choose Connect and sign in to Google. Google warns that it 'hasn't verified this app': it's your own app, "
    "so choose Advanced, then Go to Jig. Google only shows this the first time. Then tick what Jig may do, "
    "and choose Continue.")
_GOOGLE_NOTE = ("A shared Google app, checked by Google, so that nobody has to do this setup, is being looked "
                "into for after launch.")


def _google(what: str) -> dict[str, Any]:
    return {"summary": f"Sign in with your Google account so Jig can {what}.",
            "setup": _GOOGLE_SETUP, "steps": [_GOOGLE_CONNECT], "note": _GOOGLE_NOTE}


GUIDES: dict[str, dict[str, Any]] = {
    "gmail": _google("read your mail and, with your OK each time, send it"),
    "google-calendar": _google("read your calendars and, with your OK each time, change events"),
    "google-drive": _google("search and read your files and, with your OK each time, save new ones"),
    "microsoft": {
        "summary": "Sign in with your Microsoft account: a personal one (Outlook.com, Hotmail, Live) or a work "
                   "or school one.",
        "steps": [
            _step("Choose Connect. Microsoft's sign-in page opens in a new tab."),
            _step("Sign in, check what Jig asks for, and choose Accept. Come back here: it connects by itself."),
            _step("Work or school account: if Microsoft says 'Need admin approval', your organisation lets only "
                  "its IT team approve apps. Send them the request from that page, or ask whether they have "
                  "their own app for Jig to use (below)."),
        ],
        "advanced": "If your organisation gives you its own app registration, paste its Application (client) "
                    "ID here (and its tenant, if it only works for your organisation). Jig then signs in with "
                    "that app instead of its own.",
    },
    "github": {
        "summary": "Sign in with GitHub and choose which repositories Jig may use.",
        "steps": [
            _step("First choose the repositories: open the Jig GitHub App, choose Install (or Configure), pick "
                  "'Only select repositories', tick the ones Jig may use, and save. You can change this any "
                  "time.", ("{install_url}", "Choose repositories")),
            _step("Choose Connect. Jig shows a short code and opens GitHub's device page."),
            _step("Type the code there, then choose Authorise. Come back here: it connects by itself."),
        ],
        "advanced": "Or use a fine-grained personal access token: make one on GitHub with only the repositories "
                    "you want, Contents and Pull requests read-only, and Issues read-only (or read and write, to "
                    "let Jig comment and open issues), and paste it "
                    "here. Jig refuses classic tokens, which reach every repository you can.",
        "advanced_links": [{"url": "https://github.com/settings/personal-access-tokens/new",
                            "label": "Make a fine-grained token"}],
    },
    "slack": {
        "summary": "Add a Jig bot to your Slack workspace. It can read the channels you invite it to and, with "
                   "your OK each time, post.",
        "steps": [
            _step("Create a Slack app: choose Create New App, then From scratch, call it Jig and pick your "
                  "workspace.", ("https://api.slack.com/apps", "Open Slack apps")),
            _step("Open OAuth & Permissions. Under Bot Token Scopes add channels:read, channels:history and "
                  "chat:write (and users:read to see names)."),
            _step("Choose Install to Workspace and Allow. Copy the Bot User OAuth Token (it starts with xoxb-)."),
            _step("In Slack, invite the bot to a channel: type /invite @Jig in it."),
            _step("Paste the token below and choose Connect. Jig checks it with Slack and keeps it in its vault."),
        ],
    },
    "discord": {
        "summary": "Add a Jig bot to your own Discord server. It can read the channels it can see and, with "
                   "your OK each time, post.",
        "steps": [
            _step("Create an application called Jig.", ("https://discord.com/developers/applications",
                                                         "Open the Discord developer portal")),
            _step("Open Bot, choose Reset Token and copy the token (Discord shows it once). On the same page, "
                  "turn on Message Content Intent."),
            _step("Add the bot to your server: copy the Application ID from General Information, paste it "
                  "here, then open the link and pick your server.",
                  ("https://discord.com/oauth2/authorize?client_id=YOUR_APPLICATION_ID&scope=bot&permissions=68608",
                  "Add the bot")),
            _step("Paste the bot token below and choose Connect."),
        ],
    },
    "matrix": {
        "summary": "Use your own Matrix account. Jig can read and, with your OK each time, post in rooms "
                   "without end-to-end encryption.",
        "steps": [
            _step("Type your homeserver (for example https://matrix.org)."),
            _step("Easiest: type your Matrix ID (like @you:matrix.org) and your password. Jig signs in once as a "
                  "new device called Jig, keeps only that device's key and forgets the password."),
            _step("Or type 'token' as the sign-in, and paste an access token instead of a password. (A token "
                  "copied from Element belongs to Element, so disconnecting Jig signs Element out too.)"),
        ],
    },
    "signal": {
        "summary": "Send yourself (and, if you allow it, receive) Signal messages, through signal-cli linked to "
                   "your phone like Signal Desktop.",
        "steps": [
            _step("Get signal-cli, which needs Java 25 or newer. In Set up step by step, Jig can download both for "
                  "you (signal-cli from its GitHub release, Java from Eclipse Temurin), or you can install them "
                  "yourself.", ("https://github.com/AsamK/signal-cli/releases", "Download signal-cli")),
            _step("Link it to your phone: Jig shows a QR code; in Signal on your phone, open Settings > Linked "
                  "devices > Link new device and scan it. Jig says when it's linked."),
            _step("Choose what Jig may do and Connect. Jig fills in your number and where signal-cli is, and "
                  "checks that it works before saving anything."),
        ],
    },
}


def guide(name: str, *, install_url: str | None = None) -> dict[str, Any] | None:
    """The guide for one connector, with GitHub's "choose repositories" link filled in (or left out while
    the app isn't known)."""
    g = GUIDES.get(name)
    if g is None:
        return None
    out = dict(g)
    for key in ("setup", "steps"):
        if key in out:
            steps = []
            for s in out[key]:
                links = [{**k, "url": install_url} if k["url"] == "{install_url}" else k for k in s["links"]
                         if k["url"] != "{install_url}" or install_url]
                steps.append({**s, "links": links})
            out[key] = steps
    return out
