"""Guided set-up for every connector, one step at a time, in Jig's own voice.

Settings > Connections shows these steps one by one: what Jig says, what the provider's page looks like,
what to click, and a check that the step worked before moving on. The same steps are what the agent
reads (``connection_help``) when someone asks in chat for help connecting an account, so both always say
the same thing.

Every check here is real and read-only: it asks the provider (Google, Discord, a Matrix homeserver), runs
signal-cli, or runs one of the connector's own read tools. None of them ever takes or returns a secret:
tokens, passwords and client files are only typed into Settings, which sends them straight to the vault.

A step's ``action`` tells the page what to show:

  next          nothing to check; "Next"
  done          something only the person can see (Jig can't check it from here); "I've done this"
  project       an optional Google project ID, so the next links open in that project
  google_client choose the downloaded client file; Jig checks it with Google
  connect       a Connect button (browser sign-in, or GitHub's code); done once the account is connected
  token         the connector's own sign-in form (secret fields go straight to the vault)
  check         a non-secret value to type (``input``) and a check (``check``) to run on it
  pick          a list of channels or rooms found with the connected account; picking one reads it
  try           a final read with the connected account, in plain words
  signal_install  what Jig found (Java, signal-cli), "Download for me" with progress, or a path to check
  signal_link   Jig runs signal-cli's link step and shows its link as a QR code until the phone has scanned it
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from urllib.parse import quote

import httpx

from ..constants import Effect, Mode, ToolCategory
from ..errors import ConnectorError, ConnectorNotConnected, JigError
from . import discord, google, matrix, signal_setup
from .base import PROVIDERS, Connectors

SETTINGS_LINK = "#settings/connections/{name}"

SECRET_RULE = ("Never ask for, or accept, a password, token, client secret or client file in the chat. Those are "
               "typed only into Settings > Connections on the computer Jig runs on, which puts them straight "
               "into Jig's vault. If someone pastes one into the chat anyway, tell them to make a new one on the "
               "provider's site (the pasted one should be treated as leaked) and to type the new one in Settings.")

# Slack builds the app from this when the link below is opened (api.slack.com/apps?new_app=1&manifest_json=):
# the name, a bot user and exactly the scopes Jig uses, so nobody has to add them by hand.
SLACK_MANIFEST = {
    "display_information": {"name": "Jig", "description": "Your Jig assistant, reading and (with your OK) posting."},
    "features": {"bot_user": {"display_name": "Jig", "always_online": False}},
    "oauth_config": {"scopes": {"bot": ["channels:read", "channels:history", "chat:write", "users:read"]}},
    "settings": {"org_deploy_enabled": False, "socket_mode_enabled": False, "token_rotation_enabled": False},
}
SLACK_NEW_APP = "https://api.slack.com/apps?new_app=1&manifest_json=" + quote(json.dumps(SLACK_MANIFEST,
                                                                                      separators=(",", ":")))
DISCORD_INVITE = "https://discord.com/oauth2/authorize?client_id={application_id}&scope=bot&permissions=68608"


def _link(url: str, label: str, *, project: bool = False) -> dict[str, Any]:
    """``project``: Google Cloud console pages take ?project=<id>, so the page can open in the right project."""
    return {"url": url, "label": label, **({"project": True} if project else {})}


def _step(id: str, title: str, say: str, *, see: str = "", do: list[str] | None = None,
          links: list[dict[str, Any]] | None = None, action: dict[str, Any] | None = None, note: str = "",
          shared: str = "") -> dict[str, Any]:
    """``shared``: this step belongs to a set-up several connectors share ("google"); once it is done
    (the app client is stored), the page skips it."""
    return {"id": id, "title": title, "say": say, "see": see, "do": do or [], "links": links or [],
            "action": action or {"type": "next"}, "note": note, **({"shared": shared} if shared else {})}


# Google: your own app, one-off, shared by Gmail, Calendar and Drive ------------------------------------

GOOGLE_SETUP = [
    _step("google-intro", "Your own Google app",
          "Google doesn't let Jig share one app with everyone yet, so first we'll make your own small Google app. "
          "It's free, only you use it, and you only do it once: Gmail, Calendar and Drive all use it. I'll check "
          "what I can along the way.",
          do=["Sign in to Google in your browser with the account you want me to use.",
              "Keep this page open next to Google's, and come back here after each step."],
          note="A shared app, checked by Google, so nobody has to do this, is being looked into for after launch.",
          shared="google"),
    _step("google-project", "Create a project",
          "A project is just a folder on Google's side for your app. Let's make one called Jig.",
          see="Google's 'New Project' page: a 'Project name' box, a 'Project ID' underneath it (something like "
              "jig-472913), and a Location you can leave as it is.",
          do=["Type Jig as the project name.", "Choose Create.",
              "Copy the Project ID (it's under the name box, and on the project's dashboard) and paste it below. "
              "Then my next links open straight in your new project. If you can't find it, leave it empty: "
              "just check that 'Jig' is the project selected at the top of each Google page."],
          links=[_link("https://console.cloud.google.com/projectcreate", "Create a project")],
          action={"type": "project"}, shared="google"),
    _step("google-apis", "Switch on Gmail, Calendar and Drive",
          "Next, tell Google which of its services your app may use. Switch on the ones you want me to help "
          "with; all three is fine.",
          see="Each link opens a page with the service's name (Gmail API, Google Calendar API, Google Drive API) "
              "and a blue Enable button. Once it's on, the button says Manage instead.",
          do=["Open each link and choose Enable.", "Wait for the page to change, then come back for the next one."],
          links=[_link("https://console.cloud.google.com/apis/library/gmail.googleapis.com", "Gmail API",
                       project=True),
                 _link("https://console.cloud.google.com/apis/library/calendar-json.googleapis.com",
                       "Google Calendar API", project=True),
                 _link("https://console.cloud.google.com/apis/library/drive.googleapis.com", "Google Drive API",
                       project=True)],
          action={"type": "done", "label": "I've switched them on"},
          note="I can't see your project from here. If one isn't switched on, I'll find out and tell you when you "
               "connect, and nothing is stored.", shared="google"),
    _step("google-consent", "Set up the sign-in page",
          "This is the page Google shows when you let me in. It just needs a name and your email address.",
          see="'Google Auth Platform not configured yet' and a Get started button. Then four short parts: App "
              "Information, Audience, Contact Information and Finish.",
          do=["Choose Get started.", "App name: Jig. User support email: your address. Next.",
              "Audience: External. Next.", "Contact information: your address. Next.",
              "Tick that you agree to the Google API Services User Data Policy, then Continue, then Create."],
          links=[_link("https://console.cloud.google.com/auth/overview", "Google Auth Platform", project=True)],
          action={"type": "done", "label": "I've done this"},
          note="With a Google Workspace (work or school) account you can pick Internal instead; then you can skip "
               "the next step.", shared="google"),
    _step("google-publish", "Publish it",
          "One switch that matters a lot: a new app starts in 'Testing', and Google disconnects Testing apps "
          "every 7 days. Publishing it stops that. You don't need to send it to Google to be checked: it's "
          "your own app.",
          see="The Audience page: 'Publishing status: Testing' and a Publish app button. Google asks 'Push to "
              "production?'",
          do=["Choose Publish app, then Confirm.", "Check the publishing status now says In production.",
              "You don't need to add test users, or anything under Data access."],
          links=[_link("https://console.cloud.google.com/auth/audience", "Audience page", project=True)],
          action={"type": "done", "label": "It says In production"}, shared="google"),
    _step("google-client", "Make the key I sign in with",
          "Now the key itself: a small file Google makes for 'Desktop app' clients. Important: Google only lets "
          "you download it once, straight after you make it.",
          see="The Clients page with a Create client button. After you create it, a box called 'OAuth client "
              "created' with a Download JSON button.",
          do=["Choose Create client.", "Application type: Desktop app. Name: Jig. Choose Create.",
              "In the box that appears, choose Download JSON straight away. (If you closed it first, no harm "
              "done: just create another Desktop app client.)",
              "Choose that file below. I'll check it with Google."],
          links=[_link("https://console.cloud.google.com/auth/clients", "Clients page", project=True)],
          action={"type": "google_client"}, shared="google"),
    _step("google-warning", "Before you sign in: a warning you'll see once",
          "When you connect, Google shows a warning page, because your app hasn't been checked by Google. "
          "That's expected: it's your own app, and only you use it.",
          see="'Google hasn't verified this app', saying the app is asking for access to sensitive info in your "
              "Google Account, with a Back to safety button and a small Advanced link.",
          do=["Choose Advanced (the small link, not Back to safety).", "Then choose Go to Jig (unsafe).",
              "On the next page, tick every box for what Jig may do, then choose Continue. A box you leave "
              "unticked isn't granted."],
          note="You only see this the first time for each part (Gmail, Calendar, Drive).", shared="google"),
]

GOOGLE_TRY = {"gmail": "See your mailbox", "google-calendar": "See your calendars", "google-drive": "Search your Drive"}


def _google(name: str, label: str) -> list[dict[str, Any]]:
    return [*GOOGLE_SETUP,
            _step("connect", f"Connect {label}",
                  f"Now the easy bit. Choose what I may do with {label}, then Connect. A new tab opens at Google.",
                  see="Google's account chooser, then the warning page (Advanced, Go to Jig), then 'Jig wants "
                      "access to your Google Account' with a box to tick for each thing.",
                  do=["Pick your account.", "Advanced, then Go to Jig (unsafe).", "Tick every box, then Continue.",
                      "Come back here: it connects by itself."],
                  action={"type": "connect"}),
            _step("try", GOOGLE_TRY[name], "Let me make sure I can really see it.", action={"type": "try"})]


# The other connectors ------------------------------------------------------------------------------------

STEPS: dict[str, list[dict[str, Any]]] = {
    "gmail": _google("gmail", "Gmail"),
    "google-calendar": _google("google-calendar", "Google Calendar"),
    "google-drive": _google("google-drive", "Google Drive"),
    "microsoft": [
        _step("connect", "Sign in with Microsoft",
              "This one's simple: Jig has its own Microsoft app, so you just sign in and say yes. It works with "
              "Outlook.com, Hotmail and Live accounts, and with work or school accounts.",
              see="Microsoft's sign-in page, then a page listing what Jig asks for (your calendars, your OneDrive "
                  "files, and staying signed in) with an Accept button.",
              do=["Choose what I may do, then Connect.", "Sign in and choose Accept.",
                  "Come back here: it connects by itself."],
              note="With a work or school account, Microsoft may say the app is unverified, or 'Need admin "
                   "approval'. That's your organisation's rule: you can ask for approval from that page, or ask "
                   "your IT team.",
              action={"type": "connect"}),
        _step("try", "See your calendars and files", "Let me check I can see them.", action={"type": "try"}),
    ],
    "github": [
        _step("install", "Choose your repositories",
              "First, pick which repositories I may use. You can change this any time on GitHub.",
              see="The Jig GitHub App's page, with an Install (or Configure) button, then a choice between 'All "
                  "repositories' and 'Only select repositories'.",
              do=["Choose Install (or Configure if you've done this before) and pick your account.",
                  "Choose Only select repositories and tick the ones I may use.", "Choose Install (or Save)."],
              links=[_link("{install_url}", "Choose repositories")],
              action={"type": "done", "label": "I've chosen them"}),
        _step("connect", "Sign in with a code",
              "Now sign in. I'll show you a short code and open GitHub's page; you type the code there.",
              see="GitHub's 'Device activation' page asking for the code, then 'Authorize Jig' listing what it "
                  "may do.",
              do=["Choose Connect.", "Type the code on GitHub's page (or copy it here), then Continue.",
                  "Choose Authorize. Come back here: it connects by itself."],
              action={"type": "connect"}),
        _step("try", "See your repositories", "Let me check which repositories I can see.", action={"type": "try"}),
    ],
    "slack": [
        _step("create", "Make the Jig bot",
              "Slack apps live in your own workspace. My link fills everything in for you: the name, a bot "
              "called Jig, and exactly what it may do.",
              see="Slack asks you to pick a workspace, then shows the app's settings for you to review, then a "
                  "Create button.",
              do=["Pick your workspace, then Next.", "Have a look, then Next again.", "Choose Create."],
              links=[_link(SLACK_NEW_APP, "Make the Jig bot")],
              action={"type": "done", "label": "I've made it"}),
        _step("token", "Install it and copy its token",
              "Now install the bot in your workspace and give me its token. It goes straight into my vault and "
              "I'll check it with Slack.",
              see="The app's settings. In the left-hand menu, Install App (or OAuth & Permissions), with an "
                  "'Install to <your workspace>' button. After you choose Allow, a 'Bot User OAuth Token' "
                  "starting xoxb- with a Copy button.",
              do=["Choose Install App in the left-hand menu, then Install to your workspace, then Allow.",
                  "Copy the Bot User OAuth Token (it starts xoxb-) and paste it below."],
              links=[_link("https://api.slack.com/apps", "Your Slack apps")],
              action={"type": "token"}),
        _step("channel", "Invite me to a channel",
              "I can only read channels I've been invited to. Make one for me (like #jig-test), or use one you "
              "have, and invite me.",
              see="Your Slack channel. Typing /invite @Jig and pressing Enter adds the bot.",
              do=["In Slack, open the channel and type /invite @Jig", "Then choose Look again below and pick it."],
              action={"type": "pick"}),
    ],
    "discord": [
        _step("create", "Make the Jig app",
              "Discord bots live in your own server. First make an app for it on Discord's developer site.",
              see="The Discord Developer Portal with a New Application button (top right), then a box for the "
                  "name. Your new app opens on its General Information page, which shows an Application ID with "
                  "a Copy button.",
              do=["Choose New Application, type Jig, agree to the terms and choose Create.",
                  "On General Information, copy the Application ID and paste it below. I'll check it with "
                  "Discord."],
              links=[_link("https://discord.com/developers/applications", "Discord Developer Portal")],
              action={"type": "check", "check": "discord_app",
                      "input": {"name": "application_id", "prompt": "Application ID",
                                "placeholder": "123456789012345678"}}),
        _step("token", "Turn on reading and copy the bot's token",
              "Two things on the Bot page: let the bot read messages, and copy its token for me. The token goes "
              "straight into my vault.",
              see="The Bot page: Privileged Gateway Intents with a Message Content Intent switch, and a Reset "
                  "Token button. Discord shows the new token once, with a Copy button.",
              do=["Choose Bot in the left-hand menu.",
                  "Turn on Message Content Intent and choose Save Changes. (Without it, Discord hides what "
                  "people write.)",
                  "Choose Reset Token, then Yes, do it! Copy the token and paste it below."],
              links=[_link("https://discord.com/developers/applications", "Discord Developer Portal")],
              action={"type": "token"}),
        _step("invite", "Add the bot to your server",
              "Now add the bot to your server. My link only asks for what I use: seeing channels, reading their "
              "history and sending messages.",
              see="Discord's 'Add to server' page: pick a server, Continue, then Authorise.",
              do=["Open the link, pick your server, Continue, then Authorise.",
                  "Make a channel for me (like #jig-test) if you like, then choose Look again below and pick it."],
              links=[_link(DISCORD_INVITE, "Add the bot to your server")],
              action={"type": "pick"}),
    ],
    "matrix": [
        _step("server", "Your homeserver",
              "Which Matrix server is your account on? For most people it's matrix.org. I'll check it answers.",
              see="Nothing to open: your Matrix ID ends with your server, as in @you:matrix.org.",
              action={"type": "check", "check": "matrix_server",
                      "input": {"name": "homeserver", "prompt": "Homeserver", "placeholder": "https://matrix.org"}}),
        _step("token", "Sign in",
              "Now sign in. The easiest way: your Matrix ID and password. I sign in once as a new device called "
              "Jig, keep only that device's key in my vault, and forget the password.",
              do=["Type your Matrix ID (like @you:matrix.org) and your password, then Connect.",
                  "Or type token as the sign-in and paste an access token instead of a password."],
              note="Jig can't read or post in end-to-end encrypted rooms: it has no encryption keys.",
              action={"type": "token"}),
        _step("room", "Pick a room",
              "Here are the rooms you've joined. Encrypted ones are greyed out, because I can't read those.",
              do=["Pick a room, or make an unencrypted one in Element (New room, then turn off 'Enable "
                  "end-to-end encryption') and choose Look again."],
              action={"type": "pick"}),
    ],
    "signal": [
        _step("install", "Get signal-cli",
              "Signal works through signal-cli, a free program that links to your phone the way Signal Desktop "
              "does. It needs Java 25 or newer. I can download both for you, or use copies you already have.",
              do=["Choose Download for me. I get signal-cli from its official release on GitHub and, if this "
                  "computer has no Java 25, Eclipse Temurin's free Java. I check each against the checksum its "
                  "publisher lists and keep them in my own folder, just for Signal.",
                  "Already have signal-cli? If I found it, choose Use the one I found. If not, open 'I have my "
                  "own signal-cli', type where its bin\\signal-cli.bat is, and choose Check."],
              links=[_link(signal_setup.TEMURIN_PAGE, "Get Java yourself (Eclipse Temurin)"),
                     _link(signal_setup.RELEASES_PAGE, "Get signal-cli yourself")],
              note="The download is about 120 MB for signal-cli, plus about 60 MB for Java if you need it. Neither "
                   "is part of Jig: signal-cli is open source under the GPL-3.0 licence, and Temurin under the "
                   "GPL-2.0 with the Classpath Exception. Both are free.",
              action={"type": "signal_install", "check": "signal_cli",
                      "input": {"name": "signal_cli", "prompt": "Where signal-cli.bat is",
                                "placeholder": "C:\Users\you\\signal-cli\\bin\\signal-cli.bat"}}),
        _step("link", "Link it to your phone",
              "Now I'll show a code for your phone to scan. It adds me to your Signal account as a linked device "
              "called Jig, like Signal Desktop. Your phone stays your main Signal.",
              see="In Signal on your phone: Settings, then Linked devices, then Link new device. Your camera opens "
                  "to scan the code.",
              do=["Choose Show the code.",
                  "On your phone, open Signal and go to Settings > Linked devices > Link new device.",
                  "Point your phone's camera at the code, and confirm if Signal asks.",
                  "Keep this page open: I'll say when it's linked."],
              note="signal-cli keeps the linked device's keys in its own folder in your user profile. To undo the "
                   "link at any time, open Signal on your phone, Settings > Linked devices, and unlink Jig.",
              action={"type": "signal_link"}),
        _step("token", "Connect",
              "Last step: choose what I may do. I've filled in your number and where signal-cli is, and I'll "
              "check that Signal knows the number.",
              action={"type": "token"}),
    ],
}

TRY_TOOLS: dict[str, tuple[str, dict[str, Any]]] = {
    "gmail": ("gmail_list_labels", {}),
    "google-calendar": ("gcal_list_calendars", {}),
    "google-drive": ("gdrive_search", {"query": "Jig", "max_results": 5}),
    "microsoft": ("outlook_list_calendars", {}),
    "github": ("github_list_repos", {"max_results": 10}),
}
PICK_TOOLS = {"slack": "slack_list_channels", "discord": "discord_list_channels", "matrix": "matrix_list_rooms"}
READ_TOOLS = {"slack": ("slack_read_channel", "channel_id"), "discord": ("discord_read_channel", "channel_id"),
              "matrix": ("matrix_read_room", "room_id")}


def walkthrough(name: str, *, install_url: str | None = None) -> list[dict[str, Any]]:
    """The steps for one connector, with its links filled in."""
    steps = []
    for s in STEPS[name]:
        links = []
        for link in s["links"]:
            if "{install_url}" in link["url"]:
                if not install_url:
                    continue
                link = {**link, "url": link["url"].replace("{install_url}", install_url)}
            links.append(link)
        steps.append({**s, "links": links})
    return steps


# Checks --------------------------------------------------------------------------------------------------

def _ok(say: str, **more: Any) -> dict[str, Any]:
    return {"ok": True, "say": say, **more}


def _not_yet(say: str, **more: Any) -> dict[str, Any]:
    return {"ok": False, "say": say, **more}


def _value(values: Any, key: str, what: str, limit: int = 300) -> str:
    v = values.get(key) if isinstance(values, dict) else None
    if not isinstance(v, str) or not v.strip():
        raise ConnectorError(f"type the {what} first")
    if len(v) > limit:
        raise ConnectorError(f"that {what} is too long")
    return v.strip()


async def _read_tool(jig: Any, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run one of the connector's own read tools, as the agent would in read-only mode."""
    spec = jig.registry.get(tool)
    if spec.effect is not Effect.READ:
        raise ConnectorError(f"{tool} isn't a read tool")
    from ..policy.gate import CallContext
    ctx = jig._tool_context(CallContext(run_id=f"r_walk_{uuid.uuid4().hex[:8]}", task_id=None, mode=Mode.RESEARCH,
                                        intent="check a connection step in Settings"))
    return await spec.fn(ctx, **args)


async def google_client_check(http: httpx.AsyncClient, client: dict[str, Any]) -> dict[str, Any]:
    """Ask Google's token endpoint about the stored client with a made-up code. 'invalid_grant' means Google
    knows the client and its secret (only the code is wrong); 'invalid_client' means it doesn't."""
    try:
        r = await http.post(google.TOKEN_URL, timeout=20, data={
            "client_id": client["client_id"], "client_secret": client["client_secret"], "code": "jig-walkthrough-check",
            "grant_type": "authorization_code", "redirect_uri": "http://127.0.0.1"})
    except httpx.HTTPError as exc:
        raise ConnectorError(f"could not reach Google to check the file: {type(exc).__name__}") from None
    try:
        body = r.json()
    except ValueError:
        body = {}
    error = body.get("error") if isinstance(body, dict) else None
    project = client.get("project_id") or ""
    if error == "invalid_grant":
        return _ok("Google knows this app" + (f" (project {project})" if project else "") + ". Your Google app is "
                   "set up: you can delete the downloaded file now.", project_id=project)
    if error == "invalid_client":
        return _not_yet("Google doesn't recognise this app: the client may have been deleted, or the file is from "
                        "another client. Make a new Desktop app client and choose its file.")
    raise ConnectorError(f"Google answered unexpectedly (HTTP {r.status_code}"
                         + (f", {error}" if error else "") + ")")


async def _discord_app(http: httpx.AsyncClient, values: Any) -> dict[str, Any]:
    app_id = discord.snowflake(_value(values, "application_id", "Application ID"), "Application ID")
    try:
        r = await http.get(f"{discord.API}/applications/{app_id}/rpc", timeout=20)
    except httpx.HTTPError as exc:
        raise ConnectorError(f"could not reach Discord: {type(exc).__name__}") from None
    if r.status_code == 404:
        return _not_yet("Discord doesn't know an app with that ID. Copy the Application ID from General Information "
                        "(not the token or the public key) and try again.")
    if r.status_code != 200:
        raise ConnectorError(f"Discord answered HTTP {r.status_code}")
    name = str(r.json().get("name") or "your app")[:100]
    return _ok(f"Found your Discord app \u2018{name}\u2019. I've made the link to add its bot to your server: it's in "
               "the last step.", application_id=app_id, invite_url=DISCORD_INVITE.format(application_id=app_id))


async def _matrix_server(http: httpx.AsyncClient, values: Any) -> dict[str, Any]:
    typed = _value(values, "homeserver", "homeserver")
    base = await matrix.check_homeserver(http, typed if "://" in typed else f"https://{typed}")
    try:
        r = await http.get(f"{base}{matrix.CLIENT}/login", timeout=20)
        flows = [f.get("type") for f in r.json().get("flows", [])] if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError, AttributeError):
        flows = []
    if "m.login.password" in flows:
        return _ok(f"Found your homeserver at {base}. It lets you sign in with a password.", homeserver=base)
    return _ok(f"Found your homeserver at {base}. It doesn't offer password sign-in, so in the next step type token "
               "as the sign-in and paste an access token (Element: Settings, Help & About, Advanced).",
               homeserver=base, password_login=False)


async def _signal_cli(jig: Any, values: Any) -> dict[str, Any]:
    binary, version = await signal_setup.check_ready(_value(values, "signal_cli", "path to signal-cli.bat", 500),
                                                     signal_setup.tools_dir(jig.config.data_dir))
    return _ok(f"signal-cli runs ({version}). Now link it to your phone.", signal_cli=binary)


async def _pick(jig: Any, name: str) -> dict[str, Any]:
    tool = PICK_TOOLS[name]
    if name == "slack":
        out = await _read_tool(jig, tool, {})
        items = [{"id": c["channel_id"], "label": f"#{c['name']}",
                  "note": "" if c["bot_is_member"] else "Jig isn't in this channel yet: /invite @Jig",
                  "disabled": not c["bot_is_member"]} for c in out["channels"]]
    elif name == "discord":
        servers = (await _read_tool(jig, tool, {}))["servers"]
        items = []
        for s in servers[:10]:
            listed = await _read_tool(jig, tool, {"guild_id": s["guild_id"]})
            items += [{"id": c["channel_id"], "label": f"#{c['name']}", "note": f"in {s['name']}", "disabled": False}
                      for c in listed["channels"]]
    else:
        rooms = (await _read_tool(jig, tool, {}))["rooms"]
        items = [{"id": r["room_id"], "label": r.get("name") or r["room_id"],
                  "note": "encrypted: Jig can't read it" if r.get("encrypted") else "",
                  "disabled": bool(r.get("encrypted"))} for r in rooms]
    items.sort(key=lambda i: (i["disabled"], i["label"].lower()))
    if not any(not i["disabled"] for i in items):
        where = {"slack": "a channel I've been invited to (type /invite @Jig in it)",
                 "discord": "a server with the bot in it (open the link in this step)",
                 "matrix": "an unencrypted room you've joined"}[name]
        return _not_yet(f"I can't see {where} yet. Do that, then choose Look again.", items=items)
    return _ok("Here's what I can see. Pick the one you'd like me to use.", items=items)


async def _read(jig: Any, name: str, values: Any) -> dict[str, Any]:
    tool, key = READ_TOOLS[name]
    target = _value(values, "target", "channel or room")
    out = await _read_tool(jig, tool, {key: target, "limit": 5})
    count = len(out.get("messages", []))
    seen = f"I can read it: {count} recent message{'s' if count != 1 else ''}." if count else (
        "I can read it, and it's empty so far.")
    if out.get("note"):
        return _not_yet(f"{seen} But: {out['note']}", target=target)
    return _ok(f"{seen} Everything's set up. Every message I post here needs your OK first.", target=target)


def _names(items: list[dict[str, Any]], key: str, limit: int = 5) -> str:
    names = [str(i.get(key) or "") for i in items if i.get(key)][:limit]
    more = len(items) - len(names)
    return ", ".join(names) + (f" and {more} more" if more > 0 else "")


async def _try(jig: Any, name: str) -> dict[str, Any]:
    tool, args = TRY_TOOLS[name]
    out = await _read_tool(jig, tool, args)
    if name == "gmail":
        return _ok(f"I can see your mailbox ({len(out['labels'])} labels). You're all set: every email I send "
                   "needs your OK first.")
    if name in ("google-calendar", "microsoft"):
        cals = out["calendars"]
        extra = ""
        if name == "microsoft":
            top = await _read_tool(jig, "onedrive_list_folder", {"max_results": 100})
            n = len(top["items"])
            extra = f", and {'over ' if top['more'] else ''}{n} thing{'s' if n != 1 else ''} at the top of your OneDrive"
        return _ok(f"I can see {len(cals)} calendar{'s' if len(cals) != 1 else ''} ({_names(cals, 'name')}){extra}. "
                   "You're all set: every change I make needs your OK first.")
    if name == "google-drive":
        return _ok(f"I can search your Drive (I looked for 'Jig' and found {len(out['files'])} file"
                   f"{'s' if len(out['files']) != 1 else ''}). You're all set: every file I save needs your OK first.")
    repos = out["repos"]
    if not repos:
        return _not_yet("I'm signed in, but I can't see any repositories. Choose some for the Jig GitHub App (the "
                        "first step), then choose Check again.")
    return _ok(f"I can see {_names(repos, 'repo')}. You're all set: every comment or issue I write needs your "
               "OK first.")


CHECKS = {"google_client", "discord_app", "matrix_server", "signal_cli", "pick", "read", "try"}


async def run_check(jig: Any, name: str, check: str, values: Any) -> dict[str, Any]:
    """One step's check. Returns {ok, say, ...}: ``ok`` False means the step isn't done yet (``say`` says
    what to do). A problem the person can fix is a ConnectorError with the provider's own words."""
    if check not in CHECKS:
        raise ConnectorError(f"there's no check called {check!r}")
    if check == "google_client":
        if PROVIDERS[name].family != google.FAMILY:
            raise ConnectorError("only the Google connectors use a client file")
        try:
            client = jig.connections.client(google.FAMILY)
        except ConnectorNotConnected:
            return _not_yet("I don't have your client file yet. Choose the file you downloaded, then Use this file.")
        out = await google_client_check(jig.http, client)
    elif check == "discord_app":
        out = await _discord_app(jig.http, values)
    elif check == "matrix_server":
        out = await _matrix_server(jig.http, values)
    elif check == "signal_cli":
        out = await _signal_cli(jig, values)
    elif check == "pick":
        if name not in PICK_TOOLS:
            raise ConnectorError(f"{PROVIDERS[name].label} has no channels to pick")
        out = await _pick(jig, name)
    elif check == "read":
        if name not in READ_TOOLS:
            raise ConnectorError(f"{PROVIDERS[name].label} has no channels to read")
        out = await _read(jig, name, values)
    else:
        if name not in TRY_TOOLS:
            raise ConnectorError(f"{PROVIDERS[name].label} has nothing to try")
        out = await _try(jig, name)
    jig.audit.record("connector.walkthrough_check", f"{PROVIDERS[name].label}: set-up check {check!r} "
                     + ("passed" if out["ok"] else "not done yet"), actor="user", provider=name, check=check,
                     ok=out["ok"])
    return out


# The agent's view: help in chat ----------------------------------------------------------------------------

def _plain(step: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"title": step["title"], "jig_says": step["say"]}
    if step["see"]:
        out["what_they_will_see"] = step["see"]
    if step["do"]:
        out["what_to_do"] = step["do"]
    if step["links"]:
        out["links"] = [{"label": link["label"], "url": link["url"]} for link in step["links"]]
    if step["note"]:
        out["note"] = step["note"]
    kind = step["action"]["type"]
    if kind in ("token", "google_client"):
        out["typed_where"] = "Settings > Connections only (it goes straight to the vault); never in the chat"
    elif kind in ("check", "pick", "try", "connect", "signal_install", "signal_link"):
        out["checked_in"] = "Settings > Connections, which checks this step for real"
    return out


def register_help_tool(registry: Any, connectors: Connectors) -> None:
    @registry.tool(
        description="Help someone connect one of their accounts (Gmail, Google Calendar, Google Drive, Microsoft, "
        "GitHub, Slack, Discord, Matrix, Signal): the same step-by-step guide Settings > Connections shows, and "
        "whether it is connected now. Use it whenever they ask how to connect, set up or fix an account. Read-only. "
        "Without a provider, lists every account and whether it is connected.",
        effect=Effect.READ, category=ToolCategory.WEB,
        args={"provider": "Which account: gmail, google-calendar, google-drive, microsoft, github, slack, discord, "
                          "matrix or signal. Empty for all of them."},
    )
    async def connection_help(ctx: Any, provider: str = "") -> dict[str, Any]:
        rows = {r["provider"]: r for r in ctx.connectors.store.status()}
        if not provider.strip():
            return {"accounts": [{"provider": n, "label": r["label"], "status": r["status"],
                                  **({"account": r["account"]} if r.get("account") else {})} for n, r in rows.items()],
                    "rules": SECRET_RULE}
        name = provider.strip().lower().replace(" ", "-")
        name = {"google-mail": "gmail", "outlook": "microsoft", "onedrive": "microsoft", "calendar": "google-calendar",
                "drive": "google-drive"}.get(name, name)
        if name not in rows:
            raise JigError(f"no account called {provider!r}; one of {sorted(rows)}")
        r = rows[name]
        steps = walkthrough(name, install_url=r.get("install_url"))
        if r.get("client_configured"):
            steps = [s for s in steps if not s.get("shared")]
        state: dict[str, Any] = {"status": r["status"], "access": r.get("access"), "account": r.get("account")}
        if r.get("last_error"):
            state["last_error"] = r["last_error"]
        if r.get("client_problem"):
            state["app_problem"] = r["client_problem"]
        if PROVIDERS[name].family == google.FAMILY:
            state["google_app_set_up"] = bool(r.get("client_configured"))
        return {
            "provider": name, "label": r["label"], "now": state,
            "settings_link": SETTINGS_LINK.format(name=name),
            "how_to_guide": ("Walk them through these one at a time, in your own friendly words, and wait for them "
                             "after each. The settings_link opens the same guided steps in Settings, where each "
                             "step is checked for real."),
            "steps": [_plain(s) for s in steps],
            "rules": SECRET_RULE,
        }
