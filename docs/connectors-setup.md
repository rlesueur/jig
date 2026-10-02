# Connecting Jig to your accounts

Jig's connectors let it work with your own accounts: your mailbox, calendars, files, code and messages. Each one uses your own app registration or token, and talks directly from your computer to the provider. Nothing goes through a Jig server, because there isn't one.

This page explains, for each provider, what you need to create, which permissions (scopes) to grant and why, and how to hand the credentials to Jig safely.

## Status

Every connector below is built and tested without an account against the provider's real service: a made-up token or client gets the provider's real refusal, and the gate, limits and approvals are tested with Jig's real policy engine. Each also has a live test that runs against your own account once you have done its setup; until then it is skipped and says why.

| Connector | `jig connect` name | What it can do |
| --- | --- | --- |
| Gmail | `gmail` | search and read threads, list labels, draft, send, reply, label and archive |
| Google Calendar | `google-calendar` | list calendars, read events; create, change and cancel events |
| Google Drive | `google-drive` | search and read files (Docs as text, Sheets as CSV); create files, change files Jig created |
| Outlook calendar and OneDrive | `microsoft` | the same for your Outlook calendar; search, list, read and save OneDrive files |
| GitHub | `github` | see repositories, read issues, pull requests and files; comment and open issues |
| Slack | `slack` | list and read channels the bot is in; post and reply in threads |
| Discord | `discord` | list and read channels the bot can see; post |
| Matrix | `matrix` | list joined rooms, read unencrypted messages; post in unencrypted rooms |
| Signal | `signal` | send messages (to yourself while testing); receive new messages if you allow it |
| Paying and booking in the browser | (built in) | detects checkouts, payments and bookings; you always decide; Jig never types card details |

Every send, post, write, change or cancellation needs your approval (see below). None of the connectors can delete mail or files, share files, close or merge on GitHub, or ping a whole channel.

## How credentials are handled

- **Never paste a secret into a chat, an issue, a config file or this repository.** Jig only takes credentials through its own command line, which prompts without echoing what you type (or reads them from standard input with `--stdin`), or from a file you downloaded from the provider, which you can delete afterwards.
- **Everything goes into Jig's vault** (Windows DPAPI on Windows, the OS keyring elsewhere, or the key-file backend in a container). That covers app client secrets, access tokens and refresh tokens. The vault entries are named `connector.<provider>.<what>`.
- **The model never sees them.** Connector credentials are used only inside the connector code, after the policy gate has approved the action. A core rule blocks any tool from referencing a `connector.*` secret with `{{secret:...}}`, the same way model API keys are protected, and token values are redacted from every tool result, error and audit entry.
- **Tokens only go to the provider.** Each connector has a fixed list of hosts (for example `gmail.googleapis.com`, `graph.microsoft.com`, `api.github.com`), and Jig refuses to send its token anywhere else, over anything but HTTPS.
- **Least privilege.** Each connector asks only for the scopes of the access level you choose (`--access`).
- **Disconnect at any time.** `jig disconnect <provider>` revokes the grant at the provider where the provider supports that (Google, Slack and Matrix do), and deletes the tokens from the vault. Where it can't (Microsoft, GitHub, Discord, Signal), it says exactly where to remove the access yourself.

The commands that are the same for every connector:

```powershell
.\.venv\Scripts\jig connections                 # every connector: connected or not, account, scopes, last error
.\.venv\Scripts\jig connect <provider>          # connect (opens your browser for providers that use OAuth)
.\.venv\Scripts\jig disconnect <provider>       # revoke where possible and delete the tokens from the vault
```

Run them with the same `--config` (and so the same data directory) as the Jig you use, for example `.\.venv\Scripts\jig --config jig.local.toml connect gmail`. They work whether or not Jig is running; a running Jig picks up a new connection on its next tool call. Settings > Connections in the web UI shows the same status, and has Connect and Disconnect buttons for the browser sign-in connectors once the app credentials are in the vault. Connectors that use a token you type (GitHub, Slack, Discord, Matrix, Signal) are connected in a terminal only, so the token never passes through the browser.

## What Jig does with a connected account

- **Reads are reads.** Searching and reading only read. These tools also work in "just looking" (read-only) mode.
- **Every send, post, write, label change or cancellation is an action.** It is reviewed by the safety checker (the Sentinel), and it needs your approval. Sending mail, posting a message and changing a calendar are human-only, so no rule can make them automatic; saving a file and drafting or relabelling mail ask by default. Before review, Jig looks up what the action refers to (the calendar and event, the channel, the repository and issue, the folder) and shows it on the approval card. Read-only mode blocks all of them.
- **What comes back is untrusted.** An email, an event, a file or a message is written by someone else. Jig tells the model to treat connector content as data, never as instructions, and the Sentinel reviews every action that follows from it.
- **Everything is in the audit log**, with credentials redacted.
- **Rate limits.** If a provider says "slow down" (HTTP 429 or 503), Jig waits as long as the provider asks, up to a limit, and then fails with a clear message. It never retries an action you did not approve, and never quietly does something else instead.

### Limits for testing

Every connector supports a `[connectors.<name>]` section in the config, which the gate checks before the Sentinel or an approval, and the tool checks again just before it acts:

```toml
[connectors.google-calendar]
allowed_targets = ["[Jig test]"]        # the only places Jig may change: calendars, folders, channels, rooms, repositories
allowed_recipients = ["you@example.com"] # the only people it may send to or invite
required_prefix = "[Jig test]"          # every subject, title, file name or message must start with this
```

Use these in the config you test with. Each connector's section below gives the values its live test expects.

---

## Google: Gmail, Google Calendar and Google Drive

Gmail, Google Calendar and Google Drive share one Google Cloud project and one OAuth client, so you do this setup once. Each connector gets its own grant with only its own scopes, so you can connect and disconnect them separately.

Official documentation used for these steps: [Create a Google Cloud project](https://developers.google.com/workspace/guides/create-project), [Enable Google Workspace APIs](https://developers.google.com/workspace/guides/enable-apis), [Configure the OAuth consent screen](https://developers.google.com/workspace/guides/configure-oauth-consent), [Create access credentials](https://developers.google.com/workspace/guides/create-credentials), [OAuth 2.0 for installed apps](https://developers.google.com/identity/protocols/oauth2/native-app), [Gmail API scopes](https://developers.google.com/workspace/gmail/api/auth/scopes), [Calendar API scopes](https://developers.google.com/workspace/calendar/api/auth), [Drive API scopes](https://developers.google.com/workspace/drive/api/guides/api-specific-auth).

### 1. Create a project and turn on the APIs

1. Go to the [Google Cloud console](https://console.cloud.google.com/) and sign in with the Google account whose mailbox, calendar and Drive Jig should use.
2. Create a project: the project picker at the top > **New project**, name it (for example "Jig"), **Create**, and make sure it is selected.
3. Go to **Menu > APIs & Services > Library** and **Enable** each of these three: **Gmail API**, **Google Calendar API** and **Google Drive API**.

### 2. Set up the consent screen (Google Auth platform)

1. Go to **Menu > Google Auth platform > Branding**. If it says "Google Auth platform not configured yet", click **Get started**.
2. **App information:** App name "Jig (personal)", User support email: your address. **Next**.
3. **Audience:** choose **External** (for a personal @gmail.com account; with a Google Workspace account you can choose **Internal** instead). **Next**.
4. **Contact information:** your address. **Next**, agree to the Google API Services User Data Policy, **Continue**, **Create**.
5. Go to **Audience** > **Test users** > **Add users**, add your own Google address, **Save**. Leave the app in **Testing**: it is for you only and does not need Google's verification.
6. Go to **Data access** > **Add or remove scopes** and add all of the scopes below (paste them into "Manually add scopes", one per line), then **Update** and **Save**:

   | Scope | Used by | Lets Jig |
   | --- | --- | --- |
   | `https://www.googleapis.com/auth/gmail.readonly` | Gmail `read`, `send` | read mail and labels |
   | `https://www.googleapis.com/auth/gmail.compose` | Gmail `send` | create drafts and send (each send needs your approval) |
   | `https://www.googleapis.com/auth/gmail.modify` | Gmail `manage` | read, send, label and archive; never permanent deletion |
   | `https://www.googleapis.com/auth/calendar.calendarlist.readonly` | Calendar | see which calendars you have (not change them) |
   | `https://www.googleapis.com/auth/calendar.events.readonly` | Calendar `read` | read events |
   | `https://www.googleapis.com/auth/calendar.events` | Calendar `write` | also create, change and cancel events (each needs your approval) |
   | `https://www.googleapis.com/auth/drive.readonly` | Drive `read`, `write` | search and read your files |
   | `https://www.googleapis.com/auth/drive.file` | Drive `write` | create files, and change only files Jig created (each needs your approval) |

   Listing a scope here does not grant it. Jig asks for only the scopes of the connector and access level you choose, and Google shows you each one on the consent page. None of these lets Jig delete calendars or Drive files permanently, and Jig has no tool that does.
7. In [Google Calendar](https://calendar.google.com/), create a separate calendar for testing: **Other calendars > + > Create new calendar**, name it exactly `[Jig test]`. The live Calendar tests use only that calendar.

**Important: in Testing, Google's refresh tokens expire after 7 days** for these scopes ([Google's documentation](https://developers.google.com/identity/protocols/oauth2#expiration)). After that, the Google tools fail with a clear "reconnect" message and you run `jig connect <name>` again. Publishing the app to "In production" removes that limit but, for Gmail's restricted scopes, needs Google's verification, which a personal project does not need.

### 3. Create the OAuth client

1. Go to **Menu > Google Auth platform > Clients** > **Create client**.
2. **Application type: Desktop app**. Name: "Jig desktop". **Create**.
3. In the dialogue that appears, click **Download JSON** and save the file somewhere private, for example your Downloads folder. (For a desktop app Google calls the second value a "client secret", but it is not truly secret: Google's documentation says installed apps cannot keep it confidential. Jig still keeps it in the vault.)

Jig uses the desktop "loopback" flow with PKCE: it opens Google's sign-in page in your browser and listens on `http://127.0.0.1:<random port>` for the answer, which only works on this computer. You do not need to add a redirect URI for a desktop client.

### 4. Give the client to Jig and connect

```powershell
.\.venv\Scripts\jig --config jig.local.toml connect gmail --client-json "$env:USERPROFILE\Downloads\client_secret_XXXX.json"
.\.venv\Scripts\jig --config jig.local.toml connect google-calendar --access write
.\.venv\Scripts\jig --config jig.local.toml connect google-drive --access write
```

The first command stores the client ID and client secret in the vault as `connector.google.client` (shared by the Google connectors) and tells you when it is safe to delete the downloaded file. Each command opens your browser at Google's consent page. Sign in, check the permissions listed, and allow them. The browser shows "Jig is connected" and the command prints the account it connected and the scopes Google granted. Jig then makes one real call to that API, so if you forgot to enable it in step 1 you find out straight away (and the new grant is revoked again).

If you'd rather not use the file, run `jig connect gmail` without `--client-json`: it prompts for the client ID and the client secret, without echoing the secret.

### Gmail

**Access levels** (`--access`, default `send`):

| Level | Scopes | Lets Jig |
| --- | --- | --- |
| `read` | `gmail.readonly` | search and read mail, list labels |
| `send` (default) | `gmail.readonly`, `gmail.compose` | also create drafts, send and reply (each send needs your approval) |
| `manage` | `gmail.modify` | also add and remove labels and archive (each needs your approval). Gmail's `modify` scope includes reading and sending, but not permanent deletion. |

Jig never deletes mail: there is no delete tool, and adding the `TRASH` label is refused.

For testing, Jig may send only to addresses you list, and only with subjects that start with a fixed marker:

```toml
[connectors.gmail]
allowed_recipients = ["you@example.com"]   # your own address(es), exactly
required_prefix = "[Jig test]"             # every outgoing subject must start with this (after "Re: ")
```

The live tests (`tests/test_connector_gmail_live.py`) create, label, archive and read back only messages they sent themselves, and never touch other mail. They run when you set `JIG_LIVE_GMAIL_CONFIG` (the config of a Jig data directory where Gmail is connected) and `JIG_LIVE_GMAIL_ADDRESS` (your address, which must be the only allowed recipient).

### Google Calendar

**Access levels:** `read` (default: list calendars and read events) and `write` (also create, change and cancel events). Creating, changing and cancelling are human-only: the approval card shows the calendar and, for a change, the event as it is now. Guests you add get Google's invitation email, and are told about changes and cancellations; with no guests, Google sends nothing.

```toml
[connectors.google-calendar]
allowed_targets = ["[Jig test]"]   # the calendars Jig may change, by exact name or calendar id
required_prefix = "[Jig test]"     # every event title must start with this
```

Live test: connect with `--access write`, add the section above, then set `JIG_LIVE_GCAL_CONFIG` to that config and run `tests\test_connector_google_calendar_live.py`. It creates, reads, moves and cancels one `[Jig test]` event with no guests in the `[Jig test]` calendar, checks that a rename without the prefix, an event in your main calendar and a denied approval are all stopped, and cancels its event even if it fails.

### Google Drive

**Access levels:** `read` (default: search and read files) and `write` (also create files and replace the contents of files Jig created). Google itself enforces the second part: with the `drive.file` scope, Jig can't change any file it didn't create, even if asked. Google Docs and Slides are read as plain text and Sheets as CSV; other kinds (PDFs, images) return their details and link only. Jig has no delete or share tool.

```toml
[connectors.google-drive]
allowed_targets = ["root"]        # the folders Jig may save in: "root" (My Drive) or folder ids
required_prefix = "[Jig test]"    # every file name must start with this
```

Live test: connect with `--access write`, add the section above, set `JIG_LIVE_GDRIVE_CONFIG` and run `tests\test_connector_google_drive_live.py`. It creates one `[Jig test]` text file in My Drive, reads and changes it, checks that a file without the prefix and a denied approval are stopped, and deletes its file at the end.

### Disconnecting

`jig disconnect <gmail|google-calendar|google-drive>` revokes the grant at Google (`https://oauth2.googleapis.com/revoke`) and deletes the tokens from the vault. Google removes the app's access for your whole account, so the other Google connectors need connecting again afterwards. `jig disconnect google-client` also removes the stored client. You can check or remove the app's access yourself at [myaccount.google.com/connections](https://myaccount.google.com/connections).

---

## Microsoft: Outlook calendar and OneDrive

One Microsoft Entra app registration and one Microsoft sign-in cover both your Outlook calendar and your OneDrive, through Microsoft Graph.

Official documentation: [Register an application](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app), [Redirect URIs and the localhost exception](https://learn.microsoft.com/en-us/entra/identity-platform/reply-url), [Add a redirect URI](https://learn.microsoft.com/en-us/entra/identity-platform/how-to-add-redirect-uri), [Public client apps](https://learn.microsoft.com/en-us/entra/identity-platform/msal-client-applications), [Authorisation code flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow), [Microsoft Graph permissions](https://learn.microsoft.com/en-us/graph/permissions-reference).

1. You need a Microsoft Entra tenant to register an app. Microsoft's quickstart asks for an Azure account (a [free one](https://azure.microsoft.com/free/) works) and you can use its **Default Directory**. Sign in to the [Microsoft Entra admin center](https://entra.microsoft.com/).
2. **Entra ID > App registrations > New registration.** Name: "Jig (personal)".
3. **Supported account types: Personal Microsoft accounts only** (for an Outlook.com, Hotmail or Live account). Jig signs in through Microsoft's `consumers` endpoint, so it works with personal accounts only.
4. Leave Redirect URI empty for now and click **Register**. Copy the **Application (client) ID** from the Overview page. It is an identifier, not a secret.
5. **Authentication > Add a platform (or Add redirect URI) > Mobile and desktop applications**, enter `http://localhost` and save. Microsoft ignores the port for `localhost`, so Jig's random loopback port works. Do **not** create a client secret or certificate: Jig is a public client and uses PKCE instead.
6. **API permissions > Add a permission > Microsoft Graph > Delegated permissions**, add:

   | Permission | Lets Jig |
   | --- | --- |
   | `User.Read` | see your name and address, to show which account is connected |
   | `offline_access` | get a refresh token, so you don't sign in every hour |
   | `Calendars.Read` | read your Outlook calendar (`read` access) |
   | `Calendars.ReadWrite` | also create, change and cancel events, each with your approval (`write` access) |
   | `Files.Read` | search and read your OneDrive files (`read` access) |
   | `Files.ReadWrite` | also save files, each with your approval (`write` access) |

   None of these needs admin consent. Jig asks only for the ones of the access level you choose.
7. In [Outlook calendar](https://outlook.live.com/calendar/), create a calendar for testing: **Add calendar > Create blank calendar**, name it exactly `[Jig test]`. In [OneDrive](https://onedrive.live.com/), create a folder named `Jig test` at the top level. The live tests use only those.
8. Connect:

   ```powershell
   .\.venv\Scripts\jig --config jig.local.toml connect microsoft --access write --client-id <Application (client) ID>
   ```

   Jig stores the client ID in the vault as `connector.microsoft.client` (or asks for it if you leave out `--client-id`) and opens Microsoft's sign-in page in your browser. The command prints the account it connected and the permissions granted.

**Access levels:** `read` (default) and `write`, as in the table. Creating, changing and cancelling events are human-only; if you organised an event with guests, Outlook emails them about changes and cancellations. Saving a file asks by default and never replaces an existing file unless the call says so; the approval card says whether it would. Jig has no delete-file or share tool. OneDrive text files are read as text; Word, PDF and other files return their details and link only.

```toml
[connectors.microsoft]
allowed_targets = ["[Jig test]", "Jig test"]   # calendars (exact name or id) and OneDrive folders (path) Jig may change
required_prefix = "[Jig test]"                 # every event title and file name must start with this
```

Live test: connect with `--access write`, add the section above, set `JIG_LIVE_MICROSOFT_CONFIG` and run `tests\test_connector_microsoft_live.py`. It creates, reads, changes and cancels one `[Jig test]` event with no guests in the `[Jig test]` calendar, and saves, reads, replaces and deletes one `[Jig test]` file in the `Jig test` folder. It checks that a rename without the prefix, your main calendar, the top of OneDrive and a denied approval are all stopped.

Microsoft has no endpoint to revoke a single app's refresh token, so `jig disconnect microsoft` deletes the tokens from the vault and tells you to remove the app's access at [account.live.com/consent/Manage](https://account.live.com/consent/Manage).

---

## GitHub

Jig uses a **fine-grained personal access token** that you make on GitHub and type into `jig connect github`. It can see your repositories, read issues, pull requests and files, and (with `write` access) comment and open issues. Every comment and new issue is reviewed by the Sentinel and **always needs your approval**, whatever your rules say. Jig has no tool to close, merge, delete or push.

Official documentation: [Managing your personal access tokens](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens).

1. Create a throwaway repository for testing, for example `jig-connector-test` (private is fine). Jig's tests only write to that repository.
2. Go to **GitHub > Settings > Developer settings > Personal access tokens > Fine-grained tokens > Generate new token** ([github.com/settings/personal-access-tokens](https://github.com/settings/personal-access-tokens)).
3. Name "Jig", an expiry (the shortest you are comfortable with), **Resource owner:** you, **Repository access: Only select repositories**, and pick the repositories Jig may use (start with just the test repository).
4. **Repository permissions:** Metadata: **Read-only** (GitHub requires it); Contents: **Read-only**; Issues: **Read-only** for `read` access, **Read and write** for `write` access; Pull requests: **Read-only**. Nothing else. (Commenting on a pull request goes through its issue, so Issues: Read and write covers it.)
5. **Generate token**, then connect:

   ```powershell
   .\.venv\Scripts\jig connect github                  # read access
   .\.venv\Scripts\jig connect github --access write   # also comment and open issues (each needs your approval)
   ```

   Jig asks for the token without showing it (or reads it from standard input with `--stdin`), checks it with GitHub, and stores it only in the vault. Jig only accepts fine-grained tokens (`github_pat_`); classic tokens (`ghp_`) reach every repository you can, so Jig refuses them. If the token has an expiry, Jig marks the connection as needing reconnecting just before it expires.

GitHub doesn't let Jig see which permissions a fine-grained token has, so `write` access in Jig means "Jig may try". If the token lacks Issues: Read and write for a repository, GitHub refuses the comment and Jig tells you which permission to add.

```toml
[connectors.github]
allowed_targets = ["<your-login>/jig-connector-test"]   # the only repositories Jig may write in (owner/name, any case)
required_prefix = "[Jig test]"                         # every comment and every issue title must start with this
allowed_recipients = ["@<your-login>"]                 # optional: the only people a comment or issue may @mention
```

A repository that has been renamed or transferred is refused (Jig doesn't follow the move), and both the name asked for and the name GitHub reports must be on the list.

Live test: set `JIG_LIVE_GITHUB_CONFIG` and `JIG_LIVE_GITHUB_REPO` (`<your-login>/jig-connector-test`) and run `tests\test_connector_github_live.py`. It opens one `[Jig test]` issue in the test repository, comments on it, reads both back, checks that a write to another repository is blocked and that a denied approval stops a comment, then closes the issue.

`jig disconnect github` deletes the token from the vault. GitHub has no way for Jig to revoke a personal access token, so also delete it at [github.com/settings/personal-access-tokens](https://github.com/settings/personal-access-tokens).

---

## Slack

Jig uses a bot that you create in your own workspace. Reading needs no approval; posting always needs your approval.

Official documentation: [Create an app](https://api.slack.com/quickstart), [Token types](https://api.slack.com/concepts/token-types), [Scopes](https://api.slack.com/scopes).

1. Go to [api.slack.com/apps](https://api.slack.com/apps) > **Create New App > From scratch**, name it "Jig" and pick your workspace.
2. **OAuth & Permissions > Scopes > Bot Token Scopes**, add `channels:read` (list public channels), `channels:history` (read channels the bot is in) and `chat:write` (post as the bot; for `write` access). Optionally `users:read`, to show names instead of user ids. If a required scope is missing, `jig connect slack` refuses and lists what is missing.
3. **Install to Workspace** and allow. The token you need is the **Bot User OAuth Token** (it starts with `xoxb-`; Jig refuses user tokens, `xoxp-`). If you add scopes later, reinstall the app.
4. Create a channel for testing, for example `#jig-test`, and type `/invite @Jig` in it. Copy its **Channel ID** (click the channel name; it is at the bottom, like `C0123456789`).
5. Connect: `.\.venv\Scripts\jig connect slack --access write` (or `--access read`). Jig asks for the token without showing it, or reads it with `--stdin`.

```toml
[connectors.slack]
allowed_targets = ["C0123456789"]   # channel ids, or exact names such as "jig-test"
required_prefix = "[Jig test]"
```

Jig never notifies a whole channel or group (`@here`, `@channel`, `@everyone` and user groups are refused), posts plain text with no link previews, and can't read private channels or direct messages with these scopes.

`jig disconnect slack` revokes the bot token at Slack (`auth.revoke`) and deletes it from the vault. To use Slack again, reinstall the app to get a new token.

---

## Discord

Discord does not allow automating a normal user account, so Jig uses a bot that you add to your own server.

Official documentation: [Building your first Discord app](https://discord.com/developers/docs/quick-start/getting-started), [Gateway intents](https://discord.com/developers/docs/events/gateway#gateway-intents).

1. [discord.com/developers/applications](https://discord.com/developers/applications) > **New Application**, name it "Jig".
2. **Bot** > **Reset Token** and keep the token for the next step but one (Discord shows it once). On the same page, under **Privileged Gateway Intents**, turn on **Message Content Intent**; without it, Discord hides the text of other people's messages. Optionally turn off **Public Bot**.
3. Copy the **Application ID** from **General Information** and open `https://discord.com/oauth2/authorize?client_id=YOUR_APPLICATION_ID&scope=bot&permissions=68608`, then pick your server. `68608` is View Channels + Send Messages + Read Message History; nothing else is needed.
4. Create a channel for testing, for example `#jig-test`. Turn on **User Settings > Advanced > Developer Mode**, right-click the channel and **Copy Channel ID**.
5. Connect: `.\.venv\Scripts\jig connect discord --access write` (or `--access read`). Jig asks for the bot token without showing it, or reads it with `--stdin`.

```toml
[connectors.discord]
allowed_targets = ["123456789012345678"]   # channel ids
required_prefix = "[Jig test]"
```

Every post is sent with mentions turned off, so it never pings anyone, and is at most 2,000 characters (Discord's limit).

Discord has no way to revoke a bot token through its API. `jig disconnect discord` deletes the token from the vault; to make it useless, go to your application's **Bot** page and choose **Reset Token**.

Live tests for Slack and Discord: make a config connected with `write` access and the limits above set to the test channel. The workspace or server also needs one other channel (such as `#general`): the test tries to post there and checks that Jig refuses. Set `JIG_LIVE_SLACK_CONFIG` and `JIG_LIVE_SLACK_CHANNEL`, or `JIG_LIVE_DISCORD_CONFIG` and `JIG_LIVE_DISCORD_CHANNEL`, and run `tests\test_connector_slack_live.py` or `tests\test_connector_discord_live.py`.

---

## Matrix

Jig uses your own Matrix account on the homeserver you choose. It can list the rooms you have joined, read their text messages and, with your approval each time, post a plain-text message.

Official documentation: [Client-server API](https://spec.matrix.org/latest/client-server-api/).

**Encryption.** Jig has no end-to-end encryption keys. In an encrypted room it marks every encrypted message as unreadable and refuses to post. Use an unencrypted room for anything you want Jig to read or post in.

The homeserver must be an `https://` address on the public internet. Jig follows the domain's `/.well-known/matrix/client` delegation (`https://matrix.org` becomes `https://matrix-client.matrix.org`), checks that it answers, and only ever sends your access token to that host.

Choose one way to sign in:

- **Recommended: log in once as a new device called "Jig".** Jig asks for your password (hidden), logs in, keeps only the access token it gets back and forgets the password. Disconnecting later signs out only this session.

  ```powershell
  .\.venv\Scripts\jig connect matrix --option homeserver=https://matrix.org --option login=@you:matrix.org
  ```

  Your homeserver must offer password login (matrix.org does). If it uses single sign-on only, use a token.
- **Paste an access token** (Element: Settings > Help & About > Advanced > Access Token). That token belongs to Element's session, so **disconnecting Jig signs Element out too**.

  ```powershell
  .\.venv\Scripts\jig connect matrix --option homeserver=https://matrix.org --option login=token
  ```

Every post says it mentions nobody (an empty `m.mentions`), so `@room` or a name in the text notifies no one.

Access levels: `read` (list rooms and read text messages) or `send` (the default: also post, each post needs your approval). Matrix tokens have no scopes of their own, so the level is a limit Jig applies itself.

For testing, create a room with encryption turned off (Element: New room > turn off "Enable end-to-end encryption") and copy its room id (Settings > Advanced > Internal room ID):

```toml
[connectors.matrix]
allowed_targets = ["!abc123:matrix.org"]   # the only rooms Jig may post in (room ids, not names)
required_prefix = "[Jig test]"
```

Live test: set `JIG_LIVE_MATRIX_CONFIG` and `JIG_LIVE_MATRIX_ROOM` and run `tests\test_connector_matrix_live.py`. It posts one `[Jig test]` message to that room and reads it back, and checks that a post to another room and a denied approval post nothing.

`jig disconnect matrix` logs the session out at the homeserver, then deletes the token from the vault.

---

## Signal

Jig sends (and, if you allow it, receives) Signal messages through [signal-cli](https://github.com/AsamK/signal-cli), a program on your computer that is linked to your phone as a separate device, like Signal Desktop. Jig runs it directly, never through a shell, and passes the message text on standard input.

**What Jig stores.** Nothing secret: only your number and the path to signal-cli. signal-cli keeps the linked device's keys in its own data folder (by default `%USERPROFILE%\.local\share\signal-cli`), outside Jig's vault. Anyone who can read that folder can use your Signal account as that device, so keep it in your user profile only.

1. Install Java 21 or newer (for example the Microsoft Build of OpenJDK or Eclipse Temurin) for your user only, and check that `java -version` works in a new PowerShell window.
2. Download the latest `signal-cli-<version>.tar.gz` from [its releases](https://github.com/AsamK/signal-cli/releases) and unpack it into a folder in your profile, for example `C:\Users\you\signal-cli`. The program is `bin\signal-cli.bat`.
3. Link it to your phone: run `C:\Users\you\signal-cli\bin\signal-cli.bat link -n Jig`. It prints a `sgnl://linkdevice?...` link; turn it into a QR code (signal-cli's [linking guide](https://github.com/AsamK/signal-cli/wiki/Linking-other-devices-(Provisioning)) shows how) and scan it in Signal on your phone: **Settings > Linked devices > Link new device**. Check it with `signal-cli.bat listAccounts`.
4. Connect:

   ```powershell
   .\.venv\Scripts\jig connect signal --option number=+447700900123 --option signal_cli=C:\Users\you\signal-cli\bin\signal-cli.bat
   ```

   Jig checks that the program is signal-cli, that it has an account for your number and that Signal's servers accept it. Nothing is stored if any check fails.

Don't run `signal-cli daemon` for the same account while Jig uses it: signal-cli allows one process per account.

Access levels: `send` (the default: send messages, each needs your approval) or `receive` (also receive new messages). Receiving takes messages off signal-cli's queue, so Jig sees each one once, and signal-cli sends the usual delivery receipts (never read receipts). Your phone still gets every message.

For testing, send only to yourself (it arrives in Note to Self):

```toml
[connectors.signal]
allowed_targets = ["+447700900123"]   # your own number, in international format
required_prefix = "[Jig test]"
```

Live test: set `JIG_LIVE_SIGNAL_CONFIG` and `JIG_LIVE_SIGNAL_NUMBER` and run `tests\test_connector_signal_live.py`. It sends one `[Jig test]` message to your Note to Self, and checks that a send to another number and a denied approval send nothing.

`jig disconnect signal` makes Jig forget the number and path. Jig can't unlink itself: on your phone, open **Settings > Linked devices** and unlink "Jig". To remove signal-cli's copy of the keys as well, run `signal-cli.bat -a <your number> deleteLocalAccountData` after unlinking.

---

## Paying and booking in the browser

Jig can browse shops and booking sites in its sandboxed browser, but **it never completes a payment or a booking on its own**:

- **Before every click and form submission**, Jig looks at the page (without changing it) and checks for a checkout, payment or booking: card or bank fields, a payment provider's frame (Stripe, PayPal, Klarna, Adyen and others), a button such as "Pay", "Place order", "Buy now" or "Book", or a checkout page that shows prices.
- **If it finds one, you always decide.** A core rule asks you, and no rule you write can make it automatic. The approval card shows the shop or site, the items and the total it found on the page, and the button. The avatar shows that Jig is shopping.
- **Jig never types card or bank details.** Typing or filling a card number, security code, expiry date, IBAN, sort code or account number is refused by the browser itself, before anything is typed. You enter payment details yourself, in your own browser.

The tests for this run against real public demo pages and stop at the confirmation step: they check that the checkout is recognised, the card fields are refused, and that a denied approval leaves the page unchanged. They need Docker, because the browser runs in Jig's sandbox. If your sandbox image is older than this feature, Jig says so and asks you to run `jig sandbox build`.

---

## Checklist

A one-page version of what to do is in the project's working notes (`setup-checklist.md`); the full details are above.
