# Connecting Jig to your accounts

Jig's connectors let it work with your own accounts: your mailbox, calendars, files, code and messages. Each one uses your own app registration or token, and talks directly from your computer to the provider. Nothing goes through a Jig server, because there isn't one.

This page explains, for each provider, what you need to create, which permissions (scopes) to grant and why, and how to hand the credentials to Jig safely.

## Status

Only what is marked **built** works today. Anything else is not claimed anywhere until it is built and tested.

| Connector | Status | What it can do |
| --- | --- | --- |
| Gmail | **built; live tests need your Google setup** | search and read threads, list labels, draft, send, reply, label and archive (every send needs your approval) |
| Google Calendar | not built yet | |
| Outlook / Microsoft 365 calendar | not built yet | |
| Buying and booking in the browser | existing browser tools; payment checkpoint not built yet | |
| GitHub | not built yet | |
| Slack, Discord | not built yet | |
| Google Drive, OneDrive | not built yet | |
| Signal, Matrix | not built yet | |

The setup steps for connectors that are not built yet are here so you can prepare the accounts in advance. They may still change slightly while those connectors are built; this page will be updated when they are.

## How credentials are handled

- **Never paste a secret into a chat, an issue, a config file or this repository.** Jig only takes credentials through its own command line, which prompts without echoing what you type (or reads them from standard input with `--stdin`), or from a file you downloaded from the provider, which you can delete afterwards.
- **Everything goes into Jig's vault** (Windows DPAPI on Windows, the OS keyring elsewhere, or the key-file backend in a container). That covers app client secrets, access tokens and refresh tokens. The vault entries are named `connector.<provider>.<what>`.
- **The model never sees them.** Connector credentials are used only inside the connector code, after the policy gate has approved the action. A core rule blocks any tool from referencing a `connector.*` secret with `{{secret:...}}`, the same way model API keys are protected, and token values are redacted from every tool result, error and audit entry.
- **Least privilege.** Each connector asks only for the scopes it needs, and Gmail lets you pick a lower access level (`--access read` or `send`).
- **Disconnect at any time.** `jig disconnect <provider>` revokes the grant at the provider where the provider supports that (Google does), and deletes the tokens from the vault. The page for each provider below says how to remove the app's access on the provider's side as well.

The commands that are the same for every connector:

```powershell
.\.venv\Scripts\jig connections                 # every connector: connected or not, account, scopes, last error
.\.venv\Scripts\jig connect <provider>          # connect (opens your browser for providers that use OAuth)
.\.venv\Scripts\jig disconnect <provider>       # revoke where possible and delete the tokens from the vault
```

Run them with the same `--config` (and so the same data directory) as the Jig you use, for example `.\.venv\Scripts\jig --config jig.local.toml connect gmail`. They work whether or not Jig is running; a running Jig picks up a new connection on its next tool call. Settings > Connections in the web UI shows the same status, and has Connect and Disconnect buttons once the app credentials are in the vault.

## What Jig does with a connected account

- **Reads are reads.** Searching and reading mail only reads. These tools also work in "just looking" (read-only) mode.
- **Every send, post, write, label change or delete is an action.** It is reviewed by the safety checker (the Sentinel), and it always needs your approval: sending mail is human-only, so no rule can make it automatic, and drafting or relabelling asks by default. The approval card shows exactly who it goes to and what it says. Read-only mode blocks all of them.
- **What comes back is untrusted.** An email is written by someone else. Jig tells the model to treat connector content as data, never as instructions, and the Sentinel reviews every action that follows from it without seeing the email's text as instructions.
- **Everything is in the audit log**, with credentials redacted.
- **Rate limits.** If a provider says "slow down" (HTTP 429 or 503), Jig waits as long as the provider asks, up to a limit, and then fails with a clear message. It never retries an action you did not approve, and never quietly does something else instead.

---

## Google: Gmail (and later Google Calendar and Google Drive)

Gmail, Google Calendar and Google Drive share one Google Cloud project and one OAuth client. Each connector gets its own grant with only its own scopes, so you can connect and disconnect them separately.

Official documentation used for these steps: [Create a Google Cloud project](https://developers.google.com/workspace/guides/create-project), [Enable Google Workspace APIs](https://developers.google.com/workspace/guides/enable-apis), [Configure the OAuth consent screen](https://developers.google.com/workspace/guides/configure-oauth-consent), [Create access credentials](https://developers.google.com/workspace/guides/create-credentials), [OAuth 2.0 for installed apps](https://developers.google.com/identity/protocols/oauth2/native-app), [Gmail API scopes](https://developers.google.com/workspace/gmail/api/auth/scopes).

### 1. Create a project and turn on the APIs

1. Go to the [Google Cloud console](https://console.cloud.google.com/) and sign in with the Google account whose mailbox Jig should use.
2. Create a project: the project picker at the top > **New project**, name it (for example "Jig"), **Create**, and make sure it is selected.
3. Go to **Menu > APIs & Services > Library**, find **Gmail API** and click **Enable**. While you are there, also enable **Google Calendar API** and **Google Drive API** if you want those connectors later.

### 2. Set up the consent screen (Google Auth platform)

1. Go to **Menu > Google Auth platform > Branding**. If it says "Google Auth platform not configured yet", click **Get started**.
2. **App information:** App name "Jig (personal)", User support email: your address. **Next**.
3. **Audience:** choose **External** (for a personal @gmail.com account; with a Google Workspace account you can choose **Internal** instead). **Next**.
4. **Contact information:** your address. **Next**, agree to the Google API Services User Data Policy, **Continue**, **Create**.
5. Go to **Audience** > **Test users** > **Add users**, add your own Google address, **Save**. Leave the app in **Testing**: it is for you only and does not need Google's verification.
6. Go to **Data access** > **Add or remove scopes** and add the scopes below (paste them into "Manually add scopes"), then **Update** and **Save**:
   - `https://www.googleapis.com/auth/gmail.readonly`
   - `https://www.googleapis.com/auth/gmail.compose`
   - `https://www.googleapis.com/auth/gmail.modify`
   - For later: `https://www.googleapis.com/auth/calendar.events` and `https://www.googleapis.com/auth/calendar.readonly` (Calendar), `https://www.googleapis.com/auth/drive.file` and `https://www.googleapis.com/auth/drive.readonly` (Drive).

   Listing a scope here does not grant it. Jig asks for only the scopes of the access level you choose, and Google shows you each one on the consent page.

**Important: in Testing, Google's refresh tokens expire after 7 days** for these scopes ([Google's documentation](https://developers.google.com/identity/protocols/oauth2#expiration)). After that, Jig's Gmail tools fail with a clear "reconnect" message and you run `jig connect gmail` again. Publishing the app to "In production" removes that limit but, for Gmail's restricted scopes, needs Google's verification, which a personal project does not need.

### 3. Create the OAuth client

1. Go to **Menu > Google Auth platform > Clients** > **Create client**.
2. **Application type: Desktop app**. Name: "Jig desktop". **Create**.
3. In the dialogue that appears, click **Download JSON** and save the file somewhere private, for example your Downloads folder. (For a desktop app Google calls the second value a "client secret", but it is not truly secret: Google's documentation says installed apps cannot keep it confidential. Jig still keeps it in the vault.)

Jig uses the desktop "loopback" flow with PKCE: it opens Google's sign-in page in your browser and listens on `http://127.0.0.1:<random port>` for the answer, which only works on this computer. You do not need to add a redirect URI for a desktop client.

### 4. Give the client to Jig and connect Gmail

```powershell
.\.venv\Scripts\jig --config jig.local.toml connect gmail --client-json "$env:USERPROFILE\Downloads\client_secret_XXXX.json"
```

Jig stores the client ID and client secret in the vault as `connector.google.client` (shared by the Google connectors) and tells you when it is safe to delete the downloaded file. Then it opens your browser at Google's consent page. Sign in, check the permissions listed, and allow them. The browser shows "Jig is connected" and the command prints the address it connected and the scopes Google granted. Delete the JSON file afterwards.

If you'd rather not use the file, run `jig connect gmail` without `--client-json`: it prompts for the client ID and the client secret, without echoing the secret.

**Access levels** (`--access`, default `send`):

| Level | Scopes | Lets Jig |
| --- | --- | --- |
| `read` | `gmail.readonly` | search and read mail, list labels |
| `send` (default) | `gmail.readonly`, `gmail.compose` | also create drafts, send and reply (each send needs your approval) |
| `manage` | `gmail.modify` | also add and remove labels and archive (each needs your approval). Gmail's `modify` scope includes reading and sending, but not permanent deletion. |

Jig never deletes mail: there is no delete tool, and adding the `TRASH` label is refused.

### Gmail in tests and development

While Gmail is being developed and tested, Jig may send only to addresses you list, and only with subjects that start with a fixed marker. Put this in the config used for testing (not in `jig.toml`):

```toml
[connectors.gmail]
allowed_recipients = ["you@example.com"]   # your own address(es), exactly
required_prefix = "[Jig test]"             # every outgoing subject must start with this (after "Re: ")
```

Anything else is blocked by the gate before it reaches the safety checker or the approval queue. The live tests (`tests/test_connector_gmail_live.py`) create, label, archive and read back only messages they sent themselves, and never touch other mail. They run only when you set `JIG_LIVE_GMAIL_CONFIG` (the config of a Jig data directory where Gmail is connected) and `JIG_LIVE_GMAIL_ADDRESS` (your address, which must be the only allowed recipient); otherwise they are skipped with that reason.

### Disconnecting

`jig disconnect gmail` revokes the grant at Google (`https://oauth2.googleapis.com/revoke`) and deletes the tokens from the vault. `jig disconnect google-client` also removes the stored client. You can check or remove the app's access yourself at [myaccount.google.com/connections](https://myaccount.google.com/connections).

---

## Microsoft: Outlook / Microsoft 365 calendar (and later OneDrive)

**Not built yet.** These steps prepare the app registration it will use.

Official documentation: [Register an application](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app), [Redirect URIs and the localhost exception](https://learn.microsoft.com/en-us/entra/identity-platform/reply-url), [Add a redirect URI](https://learn.microsoft.com/en-us/entra/identity-platform/how-to-add-redirect-uri), [Microsoft Graph permissions](https://learn.microsoft.com/en-us/graph/permissions-reference).

1. You need a Microsoft Entra tenant to register an app. Microsoft's quickstart asks for an Azure account (a [free one](https://azure.microsoft.com/free/) works) and you can use its **Default Directory**. Sign in to the [Microsoft Entra admin center](https://entra.microsoft.com/).
2. **Entra ID > App registrations > New registration.** Name: "Jig (personal)".
3. **Supported account types:** for a personal Outlook.com / Hotmail calendar choose **Personal accounts only** (or **Any Entra ID tenant + Personal Microsoft accounts** if you want work accounts too). For a work or school Microsoft 365 account, choose **Single tenant only**.
4. Leave Redirect URI empty for now and click **Register**. Copy the **Application (client) ID** and, for single tenant, the **Directory (tenant) ID** from the Overview page. These are identifiers, not secrets.
5. **Authentication > Add redirect URI > Mobile and desktop applications**, enter `http://localhost` and save. (Microsoft ignores the port for localhost, so Jig's random port works.) Under **Advanced settings**, set **Allow public client flows** to **Yes** (needed for the device-code fallback). Do **not** create a client secret: Jig is a public client and uses PKCE.
6. **API permissions > Add a permission > Microsoft Graph > Delegated permissions:** `Calendars.ReadWrite` (read and change your calendar), `User.Read` (your name and address, to show which account is connected) and `offline_access` (a refresh token, so you don't sign in every hour). For OneDrive later: `Files.ReadWrite`. None of these needs admin consent.
7. When the connector is built, you will run `jig connect outlook-calendar` and be asked for the client ID (and the tenant: `consumers`, `common` or your tenant ID).

Microsoft has no endpoint to revoke a single app's refresh token, so `jig disconnect` will delete the tokens locally and tell you to remove the app's access at [account.live.com/consent/Manage](https://account.live.com/consent/Manage) (personal accounts) or [myapps.microsoft.com](https://myapps.microsoft.com) (work accounts).

---

## GitHub

**Not built yet.**

Official documentation: [Managing your personal access tokens](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens).

1. Create a throwaway repository for testing, for example `jig-connector-test` (private is fine). Jig's tests will only use that repository.
2. Go to **GitHub > Settings > Developer settings > Personal access tokens > Fine-grained tokens > Generate new token**.
3. Name "Jig", an expiry (the shortest you are comfortable with), **Resource owner:** you, **Repository access: Only select repositories** and pick the repositories Jig may use (start with just the test repository).
4. **Repository permissions:** Issues: **Read and write**, Pull requests: **Read and write**, Contents: **Read-only**, Metadata: **Read-only** (required). Nothing else.
5. **Generate token.** Don't copy it anywhere except into Jig's prompt when the connector exists: `jig connect github` will ask for it without echoing it.

Revoke it any time on the same page; `jig disconnect github` deletes it from the vault.

---

## Slack

**Not built yet.**

Official documentation: [Create an app](https://api.slack.com/quickstart), [Token types](https://api.slack.com/concepts/token-types), [Scopes](https://api.slack.com/scopes).

1. Go to [api.slack.com/apps](https://api.slack.com/apps) > **Create New App > From scratch**, name "Jig", pick your workspace.
2. **OAuth & Permissions > User Token Scopes** (Jig acts as you, so it uses a user token): `channels:read`, `channels:history`, `groups:read`, `groups:history` (private channels you are in), `chat:write` (post as you; each post needs your approval). Add `im:history` and `im:read` only if you want Jig to read direct messages.
3. **Install to Workspace** and allow. Copy nothing yet: `jig connect slack` will ask for the **User OAuth Token** (it starts with `xoxp-`) without echoing it.
4. You will also tell Jig which channels it may read; anything else is refused.

---

## Discord

**Not built yet.**

Official documentation: [Building your first Discord app](https://discord.com/developers/docs/quick-start/getting-started), [Gateway intents](https://discord.com/developers/docs/events/gateway#gateway-intents).

Discord does not allow automating a normal user account, so Jig uses a bot that you add to your own server.

1. [discord.com/developers/applications](https://discord.com/developers/applications) > **New Application**, name "Jig".
2. **Bot** > **Reset Token** to reveal the bot token (keep it for `jig connect discord`, which prompts without echoing). Turn on **Message Content Intent** (needed to read message text).
3. **OAuth2 > URL Generator:** scopes `bot`; bot permissions: View Channels, Read Message History, Send Messages. Open the generated URL and add the bot to your server.
4. You will tell Jig which channel IDs it may read and post in.

---

## Matrix

**Not built yet.**

Official documentation: [Client-server API: login](https://spec.matrix.org/latest/client-server-api/#login).

1. Use your own Matrix account (for example on matrix.org, or your homeserver). Ideally create a separate device for Jig.
2. `jig connect matrix` will ask for your homeserver URL and either an access token (Element: **Settings > Help & About > Advanced > Access Token**; treat it like a password) or your password once, which it uses to log in as a new device called "Jig" and then forgets; only the resulting access token is stored.
3. End-to-end encrypted rooms need encryption keys that Jig will not have at first, so it will start with unencrypted rooms you choose.

---

## Signal

**Not built yet.**

Official documentation: [signal-cli](https://github.com/AsamK/signal-cli) and its [linking guide](https://github.com/AsamK/signal-cli/wiki/Linking-other-devices-(Provisioning)).

1. Install signal-cli (it needs Java 21 or newer; see its README) on this computer.
2. Link it to your phone as a secondary device: run `signal-cli link -n "Jig"`, which prints a `sgnl://linkdevice?...` address; turn it into a QR code (signal-cli's guide shows how, for example with `qrencode`), then on your phone go to **Signal > Settings > Linked devices > Link new device** and scan it.
3. signal-cli keeps its own keys in its data folder (on Windows under `%LOCALAPPDATA%`), not in Jig's vault. Jig will run it as you and only needs to know your number and the path to `signal-cli`.

---

## Checklist

A one-page version of what to do is in the project's working notes (`setup-checklist.md`); the full details are above.
