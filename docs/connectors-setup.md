# Connecting Jig to your accounts

Jig's connectors let it work with your own accounts: your mailbox, calendars, files, code and messages. Jig talks directly from your computer to each provider. Nothing goes through a Jig server, because there isn't one.

The easiest way to connect is **Settings > Connections** in Jig's web page, on the computer Jig runs on. Each account there has plain step-by-step instructions, links to the right page on the provider's site, and a Connect button. Everything below can also be done with `jig connect` in a terminal.

**Set up step by step.** Each account in Settings > Connections also has a guided set-up (the **Set up step by step** button, or `#settings/connections/<name>`, for example `#settings/connections/gmail`). Jig takes you through it one step at a time: what you'll see on the provider's page, what to click, and a check that the step worked before Next lights up. The checks are real: Jig asks Google whether it knows your client file, asks Discord whether your Application ID is a real app, finds your Matrix homeserver, runs your signal-cli, lists the Slack channels, Discord channels or Matrix rooms it can see so you pick one rather than typing an ID, and at the end reads something from the account to show it works. For Google, if you type your project ID, the Cloud Console links open in that project, and the "Google hasn't verified this app" warning is explained before you meet it. Microsoft and GitHub are one click and an approval; for GitHub the sign-in code is shown large with a Copy button, and the page updates by itself when you approve.

**Help in chat.** Ask Jig "help me connect Gmail" (or any account) and it walks you through the same steps in the conversation, using a read-only tool that gives it the guide and whether the account is connected now. It never asks for a token, password or client file in the chat: those only go into the Settings form, straight to the vault, and Jig gives you the link to that step.

How much setup each one needs:

- **Microsoft and GitHub: just sign in.** Jig has its own app with each, so you sign in and choose what to share. (Organisations that need their own app can still use one.)
- **Google (Gmail, Calendar, Drive): a one-off setup first.** You make your own small, free Google app, then sign in. The steps are below and in Settings. A shared Google app, checked by Google, so that nobody has to do this, is being looked into for after launch.
- **Slack and Discord:** you add a Jig bot to your own workspace or server, and paste its token.
- **Matrix:** your own account, with your password (once) or a token.
- **Signal:** signal-cli linked to your phone. Jig can download signal-cli (and Java, if needed) for you, and shows the QR code to scan in Signal on your phone.
- **WhatsApp:** Meta's WhatsApp Business Cloud API only (a business number and a token you create). Not a personal WhatsApp account. Jig can see the business number and, with your approval each time, send a text message. It cannot read incoming messages.

## Status

"Tested against the real service" means the connector has been tested without an account against the provider's real service: a made-up token, app or code gets the provider's real refusal, and the gate, limits and approvals are tested with Jig's real policy engine. Each also has a live test that runs against your own account once it is connected; until then it is skipped and says why.

A live test opens your test config's own data directory, where the connections are kept. While a Jig is serving that config (`jig --config <it> serve`), it holds the directory, so the live tests are skipped and name the Jig to stop: run `jig --config <it> stop`, run the live tests, then start it again.

| Connector | `jig connect` name | How you connect | What it can do | Status |
| --- | --- | --- | --- | --- |
| Gmail | `gmail` | your own Google app, then sign in | search and read threads, list labels, draft, send, reply, label and archive | built; tested against the real service; live test passed against a real connected account (2 October 2026) |
| Google Calendar | `google-calendar` | the same Google app, then sign in | list calendars, read events; create, change and cancel events | built; tested against the real service; live test passed against a real connected account (2 October 2026) |
| Google Drive | `google-drive` | the same Google app, then sign in | search and read files (Docs as text, Sheets as CSV); create files, change files Jig created | built; tested against the real service; live test passed against a real connected account (2 October 2026) |
| Outlook calendar and OneDrive | `microsoft` | sign in with Jig's Microsoft app (personal, work or school account), or your organisation's own app | the same for your Outlook calendar; search, list, read and save OneDrive files | built; tested against the real service; Jig's Microsoft app is registered; live test passed against a real connected account (2 October 2026) |
| GitHub | `github` | sign in with the Jig GitHub App (a code you type in at GitHub), or a fine-grained token | see repositories, read issues, pull requests and files; comment and open issues | built; tested against the real service; the Jig GitHub App is created; live test passed against a real connected account (2 October 2026) |
| Slack | `slack` | your own bot's token | list and read channels the bot is in; post and reply in threads | built; tested against the real service; live test waiting for a connected account |
| Discord | `discord` | your own bot's token | list and read channels the bot can see; post | built; tested against the real service; live test waiting for a connected account |
| Matrix | `matrix` | your account (password once, or a token) | list joined rooms, read unencrypted messages; post in unencrypted rooms | built; tested against the real service; live test waiting for a connected account |
| Signal | `signal` | signal-cli linked to your phone | send messages (to yourself while testing); receive new messages if you allow it | built; tested with a real signal-cli where one is installed (skipped otherwise); live test waiting for a linked device |
| WhatsApp | `whatsapp` | a token, phone number ID and WhatsApp Business Account ID from Meta's Cloud API | see the business number; send a text message | built; tested against Meta's real API with a made-up token (it is refused); live test skipped until a token is in the vault |
| Paying and booking in the browser | (built in) | nothing to connect | detects checkouts, payments and bookings; you always decide; Jig never types card details | built; tested against real public demo shops (needs Docker) |

Every connector can be connected from Settings > Connections or with `jig connect`. Every send, post, write, change or cancellation needs your approval (see below). None of the connectors can delete mail or files, share files, close or merge on GitHub, or ping a whole channel.

## How credentials are handled

- **Never paste a secret into a chat, an issue, a config file or this repository.** Jig only takes credentials in two places: Settings > Connections, and its own command line (which prompts without echoing what you type, or reads from standard input with `--stdin`). A file you downloaded from a provider (Google's client file) can be chosen in Settings or passed to `jig connect`, and deleted afterwards.
- **Settings > Connections only works on the computer Jig runs on.** Jig refuses connection requests from your other devices. What you type there goes straight to Jig and into its vault: it is never shown again, never written to Jig's logs or audit log, and never put in an error message. Jig asks your browser not to save or fill in these fields, and clears them once you choose Connect.
- **Everything goes into Jig's vault** (Windows DPAPI on Windows, the OS keyring elsewhere, or the key-file backend in a container). That covers app client secrets, access tokens and refresh tokens. The vault entries are named `connector.<provider>.<what>`.
- **The model never sees them.** Connector credentials are used only inside the connector code, after the policy gate has approved the action. A core rule blocks any tool from referencing a `connector.*` secret with `{{secret:...}}`, the same way model API keys are protected, and token values are redacted from every tool result, error and audit entry.
- **Tokens only go to the provider.** Each connector has a fixed list of hosts (for example `gmail.googleapis.com`, `graph.microsoft.com`, `api.github.com`), and Jig refuses to send its token anywhere else, over anything but HTTPS.
- **Least privilege.** Each connector asks only for the scopes of the access level you choose (`--access`, or "What Jig may do" in Settings).
- **Disconnect at any time.** `jig disconnect <provider>`, or Disconnect in Settings, revokes the grant at the provider where the provider supports that (Google, Slack and Matrix do), and deletes the tokens from the vault. Where it can't (Microsoft, GitHub, Discord, Signal, WhatsApp), it says exactly where to remove the access yourself.

The commands that are the same for every connector:

```powershell
.\.venv\Scripts\jig connections                 # every connector: connected or not, account, scopes, last error
.\.venv\Scripts\jig connect <provider>          # connect (opens your browser, or shows a code, for providers you sign in to)
.\.venv\Scripts\jig disconnect <provider>       # revoke where possible and delete the tokens from the vault
```

Run them with the same `--config` (and so the same data directory) as the Jig you use, for example `.\.venv\Scripts\jig --config jig.local.toml connect gmail`. They work whether or not Jig is running; a running Jig picks up a new connection on its next tool call.

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

Google doesn't have a shared Jig app yet, so the first time you connect a Google account you make your **own small Google app**. It's free, only you use it, and you do it once for Gmail, Calendar and Drive together. Settings > Connections shows the same steps with a link to each page. (A shared Google app, checked by Google, so that nobody has to do this, is being looked into for after launch.)

Sign in to Google with the account you want Jig to use, then:

1. **Create a project.** Open [Create a project](https://console.cloud.google.com/projectcreate), call it `Jig`, and choose **Create**. Make sure it's the selected project at the top of the page.
2. **Switch on the parts of Google Jig uses.** Open each link and choose **Enable** (only the ones you want): [Gmail](https://console.cloud.google.com/apis/library/gmail.googleapis.com), [Google Calendar](https://console.cloud.google.com/apis/library/calendar-json.googleapis.com), [Google Drive](https://console.cloud.google.com/apis/library/drive.googleapis.com).
3. **Set up the sign-in page.** Open [Google Auth Platform](https://console.cloud.google.com/auth/overview) and choose **Get started**. App name: `Jig`. User support email: your address. Audience: **External**. Contact information: your address. Agree to the policy, then **Create**.
4. **Publish it.** Open [Audience](https://console.cloud.google.com/auth/audience), choose **Publish app**, then **Confirm**. The publishing status should now say **In production**. This matters: while an app is in "Testing", Google disconnects it every 7 days. You don't need to send it to Google for verification: it's your own app.
5. **Make the key Jig signs in with.** Open [Clients](https://console.cloud.google.com/auth/clients), choose **Create client**, pick **Desktop app** as the application type, call it `Jig`, and choose **Create**. In the box that appears, choose **Download JSON**.
6. **Give the file to Jig.** In Settings > Connections, under Gmail, choose **Choose the downloaded file**, pick the file, and choose **Use this file**. (Or run `jig connect gmail --client-json <the file>`.) Jig keeps it in its vault; you can delete the download afterwards.
7. **Connect.** Choose **Connect** next to Gmail, Google Calendar or Google Drive, and sign in. Google shows a warning that it **"hasn't verified this app"**: that's expected, because it's your own app and you haven't asked Google to check it. Choose **Advanced**, then **Go to Jig (unsafe)**. You only see this the first time. Then tick what Jig may do and choose **Continue**.

Once your app is "In production", Google doesn't end Jig's access after 7 days: it stays connected until you disconnect it, remove its access in your Google account, change your Google password (for Gmail), or leave it unused for six months. An unverified app can be used by up to 100 Google accounts, which is plenty for your own.

### What each Google permission lets Jig do

You don't need to add these anywhere in Google's console: Jig asks for only the ones of the connector and access level you choose, and Google lists each one on its sign-in page.

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

None of these lets Jig delete calendars or Drive files permanently, and Jig has no tool that does.

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

### Technical details

Official documentation: [Create a Google Cloud project](https://developers.google.com/workspace/guides/create-project), [Enable Google Workspace APIs](https://developers.google.com/workspace/guides/enable-apis), [Configure the OAuth consent screen](https://developers.google.com/workspace/guides/configure-oauth-consent), [Create access credentials](https://developers.google.com/workspace/guides/create-credentials), [OAuth 2.0 for installed apps](https://developers.google.com/identity/protocols/oauth2/native-app), [Refresh token expiration](https://developers.google.com/identity/protocols/oauth2#expiration), [Unverified apps](https://support.google.com/cloud/answer/7454865).

- Jig uses the desktop "loopback" flow with PKCE: it opens Google's sign-in page and listens on `http://127.0.0.1:<random port>` for the answer, which only works on this computer. A Desktop app client needs no redirect URI.
- Google calls the second value in the client file a "client secret", but its own documentation says installed apps can't keep it confidential. Jig still keeps it in the vault as `connector.google.client` (shared by the three Google connectors).
- With a Google Workspace account you can choose **Internal** as the audience in step 3 instead; then there is no warning and no publishing step.
- If your app is still in **Testing**, Google's refresh tokens expire after 7 days. The Google tools then fail with a message that says so, and you reconnect after publishing the app (step 4).
- After signing in, Jig makes one real call to that API, so if you forgot to enable it in step 2 you find out straight away (and the new grant is revoked again).
- From a terminal: `jig connect gmail --client-json "$env:USERPROFILE\Downloads\client_secret_XXXX.json"`, then `jig connect google-calendar --access write` and `jig connect google-drive --access write`. Without `--client-json`, `jig connect gmail` asks for the client ID and secret, without echoing the secret.
- For the live Calendar tests, create a separate calendar in [Google Calendar](https://calendar.google.com/): **Other calendars > + > Create new calendar**, named exactly `[Jig test]`.

---

## Microsoft: Outlook calendar and OneDrive

One Microsoft sign-in covers both your Outlook calendar and your OneDrive, through Microsoft Graph. It works with a **personal** Microsoft account (Outlook.com, Hotmail, Live) and with a **work or school** account.

1. In Settings > Connections, choose what Jig may do next to Microsoft, and choose **Connect**. (Or run `jig connect microsoft`, with `--access write` to let Jig change events and save files.)
2. Microsoft's sign-in page opens. Sign in, check what Jig asks for, and choose **Accept**.
3. Come back to Jig: it connects by itself, and shows the account it connected.

**Work or school accounts.** Your organisation decides which apps its people may use. Microsoft shows the Jig app as **unverified** until its publisher is verified (see below). Depending on your organisation's settings you may see:

- the normal consent page: choose **Accept** and you're connected;
- **"Need admin approval"**: your organisation lets only its IT team approve apps, or only apps from verified publishers. You can send a request from that page; once an administrator approves Jig, connect again;
- a message that you can't use the app at all: ask your IT team. If they would rather use their own app registration, see "Your organisation's own app" below.

**Access levels:** `read` (default) and `write`:

| Permission | Lets Jig |
| --- | --- |
| `User.Read` | see your name and address, to show which account is connected |
| `offline_access` | stay connected without signing in again every hour |
| `Calendars.Read` | read your Outlook calendar (`read` access) |
| `Calendars.ReadWrite` | also create, change and cancel events, each with your approval (`write` access) |
| `Files.Read` | search and read your OneDrive files (`read` access) |
| `Files.ReadWrite` | also save files, each with your approval (`write` access) |

None of these needs an administrator's consent by Microsoft's rules (an organisation can still require it). Creating, changing and cancelling events are human-only; if you organised an event with guests, Outlook emails them about changes and cancellations. Saving a file asks by default and never replaces an existing file unless the call says so; the approval card says whether it would. Jig has no delete-file or share tool. OneDrive text files are read as text; Word, PDF and other files return their details and link only.

```toml
[connectors.microsoft]
allowed_targets = ["[Jig test]", "Jig test"]   # calendars (exact name or id) and OneDrive folders (path) Jig may change
required_prefix = "[Jig test]"                 # every event title and file name must start with this
```

Live test: in [Outlook calendar](https://outlook.live.com/calendar/), create a calendar named exactly `[Jig test]` (**Add calendar > Create blank calendar**), and in [OneDrive](https://onedrive.live.com/) a top-level folder named `Jig test`. Connect with `--access write`, add the section above, set `JIG_LIVE_MICROSOFT_CONFIG` and run `tests\test_connector_microsoft_live.py`. It creates, reads, changes and cancels one `[Jig test]` event with no guests in the `[Jig test]` calendar, and saves, reads, replaces and deletes one `[Jig test]` file in the `Jig test` folder. It checks that a rename without the prefix, your main calendar, the top of OneDrive and a denied approval are all stopped.

Microsoft has no endpoint to revoke a single app's refresh token, so disconnecting deletes the tokens from the vault and tells you where to remove Jig's access: [account.live.com/consent/Manage](https://account.live.com/consent/Manage) for a personal account, [myapplications.microsoft.com](https://myapplications.microsoft.com) for a work or school account.

### Your organisation's own app

Some organisations require every app to use a registration in their own directory. Then Jig signs in with that instead of its own app:

- in Settings > Connections, under Microsoft, open **Advanced: your organisation's own app**, paste the **Application (client) ID**, and the tenant if the app only works for your organisation; or
- `jig connect microsoft --client-id <Application (client) ID> [--tenant <tenant ID or domain>]` (stored in the vault; `jig disconnect microsoft-client` goes back to Jig's app); or
- in the config:

  ```toml
  [connectors.microsoft]
  client_id = "00000000-0000-0000-0000-000000000000"
  tenant = "contoso.onmicrosoft.com"   # optional: default "common" (personal and work or school accounts)
  ```

If more than one is set, Jig uses the one stored in the vault, then the config, then its own app. Each connection remembers which app and tenant it signed in with, and always renews its access with that one. The registration needs the same settings as Jig's own (below): the `http://localhost` redirect on the **Mobile and desktop applications** platform, no client secret, and the delegated permissions above.

If Jig's own Microsoft app isn't set up in your copy of Jig (its client ID is empty), connecting says so plainly, and only your organisation's own app can be used.

### For the Jig project owner: registering Jig's Microsoft app

This is done once, by the Jig project owner, not by people using Jig. It creates the one public app registration that every copy of Jig signs in with. Its Application (client) ID is an identifier, not a secret, and ships in `jig/connectors/apps.py`.

Official documentation: [Register an application](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app), [Supported account types](https://learn.microsoft.com/en-us/entra/identity-platform/supported-accounts-validation), [Redirect URIs and the localhost exception](https://learn.microsoft.com/en-us/entra/identity-platform/reply-url), [Public client apps](https://learn.microsoft.com/en-us/entra/identity-platform/msal-client-applications), [Publisher domain](https://learn.microsoft.com/en-us/entra/identity-platform/howto-configure-publisher-domain), [Publisher verification](https://learn.microsoft.com/en-us/entra/identity-platform/publisher-verification-overview), [User consent settings](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/configure-user-consent).

1. **Get a Microsoft Entra directory (tenant).** App registrations live in a directory. If you don't have one, create a free one: sign up for a [free Azure account](https://azure.microsoft.com/free/) with your Microsoft account; it comes with a **Default Directory**.
2. **Register the app as a work account in that directory, not as your personal Microsoft account.** Microsoft can only verify the publisher of an app that was registered by a work or school account. In the [Microsoft Entra admin center](https://entra.microsoft.com/), go to **Entra ID > Users > New user > Create new user**, make one such as `jig-admin@<your directory>.onmicrosoft.com`, and give it the **Application Administrator** role (**Roles and administrators**). Sign out, and sign in to the admin center as that user. (You can register as your personal account instead, but the app then can never be publisher verified, and work and school users are more likely to see "Need admin approval".)
3. **Entra ID > App registrations > New registration:**
   - **Name:** `Jig`. This is what people see on Microsoft's sign-in and consent pages.
   - **Supported account types:** **Accounts in any organizational directory (Any Microsoft Entra ID tenant - Multitenant) and personal Microsoft accounts (e.g. Skype, Xbox)**.
   - **Redirect URI:** platform **Public client/native (mobile & desktop)**, value `http://localhost`.
   - Choose **Register**.
4. **Copy the Application (client) ID** from the app's **Overview** page.
5. **Authentication:** check that **Mobile and desktop applications** lists `http://localhost` (exactly that: no port, no path; Microsoft ignores the port for `localhost`, so Jig's random loopback port works). This platform is what makes Jig a public client that uses PKCE instead of a secret. Leave **Allow public client flows** at **No**: it only enables the device code and password flows, which Jig doesn't use.
6. **Certificates & secrets:** add nothing. Jig must never have a client secret or certificate.
7. **API permissions > Add a permission > Microsoft Graph > Delegated permissions:** add `offline_access`, `Calendars.Read`, `Calendars.ReadWrite`, `Files.Read` and `Files.ReadWrite` (`User.Read` is there already). Don't choose "Grant admin consent": that only covers your own directory, and each person consents for themselves when they sign in.
8. **Branding & properties:** set the name, a logo, the home page URL (for example the Jig site), and the terms of service and privacy statement URLs. People see these on the consent page, and verification needs them.
9. **Publisher domain** (Branding & properties > Publisher domain > **Verify a new domain**): enter a domain you control, such as the Jig site's domain. Microsoft asks you to publish a file at `https://<domain>/.well-known/microsoft-identity-association.json` containing `{"associatedApplications": [{"applicationId": "<the Application (client) ID>"}]}`, then choose **Verify and save domain**. For apps registered since November 2020 this alone doesn't remove the "unverified" label, but it is needed for publisher verification, and the domain can't be `*.onmicrosoft.com` for that.
10. **Publisher verification** (optional, and can be done after launch). It replaces "unverified" with a blue "verified" badge, and lets organisations that allow only verified publishers' apps approve Jig without an administrator. It needs:
    - a [Microsoft AI Cloud Partner Program](https://partner.microsoft.com/) account (its Partner One ID, the "partner global account", not a location ID) that has completed Microsoft's verification of your organisation;
    - the directory where the app is registered associated with that partner account;
    - an email domain on the partner account that matches the app's publisher domain (step 9);
    - the person verifying signed in with multi-factor authentication and holding Application Administrator (or Cloud Application Administrator) in Entra and Partner Admin (or Account Admin) in Partner Center.

    Then: **App registration > Branding & properties > Publisher verification**, enter the Partner One ID and choose **Verify and save**.
11. **Put the ID in Jig:** set `MICROSOFT_CLIENT_ID = "<the Application (client) ID>"` in `jig/connectors/apps.py`, and run:

    ```powershell
    .\.venv\Scripts\python -m pytest -q tests\test_connector_microsoft.py -k built_in_app_is_registered
    ```

    It asks Microsoft's real sign-in service about the app with a made-up code. It passes only when Microsoft accepts the app as a public client for both personal and work or school accounts at the `common` authority, and fails with Microsoft's own error otherwise. Then connect your own account once (`jig connect microsoft --access write`) and run the live test above.

**What people see until the publisher is verified.** Personal accounts: Microsoft's consent page lists the permissions and the app name, and they choose Accept. Work and school accounts: the consent page says the app is **unverified**. If their organisation allows people to consent to apps, they choose Accept. Many organisations follow Microsoft's recommendation of allowing consent only for apps from verified publishers, and where Microsoft's risk-based step-up consent is turned on, people can't consent to unverified multi-tenant apps registered since November 2020 that ask for more than sign-in and their basic profile (Jig asks for calendar and file access). In those cases they see **"Need admin approval"** and must ask an administrator, or use their organisation's own app registration.

---

## GitHub

Jig signs in to GitHub with the **Jig GitHub App**. You choose exactly which repositories it may use, and you can change that at any time on GitHub. It can see those repositories, read issues, pull requests and files, and (with `write` access) comment and open issues. Every comment and new issue is reviewed by the Sentinel and **always needs your approval**, whatever your rules say. Jig has no tool to close, merge, delete or push.

1. **Choose the repositories.** In Settings > Connections, under GitHub, open **Choose repositories** (the Jig GitHub App's page on GitHub). Choose **Install** (or **Configure** if you've installed it before), pick your account, choose **Only select repositories**, tick the ones Jig may use, and choose **Install** or **Save**.
2. **Sign in.** Choose **Connect** in Settings (or run `jig connect github`, with `--access write` to let Jig comment and open issues). Jig shows a short code like `WDJB-MJHT` and opens [github.com/login/device](https://github.com/login/device).
3. **Type the code** on that page, check it says "Jig", and choose **Authorize**. Come back to Jig: it connects by itself. The code works for 15 minutes.

If you skip step 1, connecting stops and says so, with the link to choose repositories: without it, Jig couldn't see any of them. Jig can only ever reach repositories that you can reach and that you chose for the app, and only with the app's permissions (below). GitHub's sign-in tokens last 8 hours, and Jig renews them by itself; if Jig isn't used for six months, you sign in again.

`jig disconnect github`, or Disconnect in Settings, deletes Jig's tokens from the vault. GitHub only lets an app revoke its tokens with a secret the app's owner holds, which Jig doesn't have, so also remove Jig at [Authorized GitHub Apps](https://github.com/settings/apps/authorizations), and the app from your repositories at [Installed GitHub Apps](https://github.com/settings/installations).

### Advanced: a fine-grained personal access token

If you'd rather not use the app (or it isn't set up in your copy of Jig), you can give Jig a **fine-grained personal access token** instead: in Settings, open **Advanced: use a personal access token**, or run `jig connect github --token`. Official documentation: [Managing your personal access tokens](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens).

1. Go to [github.com/settings/personal-access-tokens/new](https://github.com/settings/personal-access-tokens/new).
2. Name "Jig", an expiry (the shortest you are comfortable with), **Resource owner:** you, **Repository access: Only select repositories**, and pick the repositories Jig may use.
3. **Repository permissions:** Metadata: **Read-only** (GitHub requires it); Contents: **Read-only**; Issues: **Read-only** for `read` access, **Read and write** for `write` access; Pull requests: **Read-only**. Nothing else. (Commenting on a pull request goes through its issue, so Issues: Read and write covers it.)
4. **Generate token**, and paste it into Settings, or into the hidden prompt of `jig connect github --token` (or standard input with `--stdin`).

Jig checks the token with GitHub and stores it only in the vault. It only accepts fine-grained tokens (`github_pat_`); classic tokens (`ghp_`) reach every repository you can, so Jig refuses them. If the token has an expiry, Jig marks the connection as needing reconnecting just before it expires. GitHub doesn't let Jig see which permissions a fine-grained token has, so `write` access in Jig means "Jig may try"; if the token lacks a permission, GitHub refuses and Jig says which. GitHub has no way for Jig to revoke a personal access token, so after disconnecting also delete it at [github.com/settings/personal-access-tokens](https://github.com/settings/personal-access-tokens).

### Limits and the live test

```toml
[connectors.github]
allowed_targets = ["<your-login>/jig-connector-test"]   # the only repositories Jig may write in (owner/name, any case)
required_prefix = "[Jig test]"                         # every comment and every issue title must start with this
allowed_recipients = ["@<your-login>"]                 # optional: the only people a comment or issue may @mention
```

A repository that has been renamed or transferred is refused (Jig doesn't follow the move), and both the name asked for and the name GitHub reports must be on the list.

Live test: create a throwaway repository such as `jig-connector-test` (private is fine) and include it when you choose repositories (or in the token). Connect with `--access write`, set `JIG_LIVE_GITHUB_CONFIG` and `JIG_LIVE_GITHUB_REPO` (`<your-login>/jig-connector-test`) and run `tests\test_connector_github_live.py`. It opens one `[Jig test]` issue in the test repository, comments on it, reads both back, checks that a write to another repository is blocked and that a denied approval stops a comment, then closes the issue.

### Your own GitHub App

To sign in with a GitHub App of your own instead of Jig's (for example one owned by your organisation, created with the steps below), name it in the config:

```toml
[connectors.github]
client_id = "Iv23li..."    # the app's Client ID
app_slug = "your-app"      # its URL name, as in https://github.com/apps/your-app
```

### For the Jig project owner: creating the Jig GitHub App

This is done once, by the Jig project owner. The app belongs to the personal GitHub account **rlesueur** (not an organisation). Its Client ID and URL name ship in `jig/connectors/apps.py`; the Client ID is an identifier, not a secret, and the device flow needs no client secret.

Official documentation: [Registering a GitHub App](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app), [Generating a user access token (device flow)](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app#using-the-device-flow-to-generate-a-user-access-token), [Refreshing user access tokens](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/refreshing-user-access-tokens), [Choosing permissions](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app).

1. Sign in to GitHub as **rlesueur**, and open **Settings > Developer settings > GitHub Apps > New GitHub App** ([github.com/settings/apps/new](https://github.com/settings/apps/new)). Check the page says it's for your personal account, not an organisation.
2. **GitHub App name:** `Jig` (names are unique across GitHub; if it's taken, pick another, such as `Jig Assistant`). People see this name when they install the app and when they type their code. **Description:** what Jig does with it. **Homepage URL:** the Jig site or repository (required).
3. **Identifying and authorizing users:**
   - **Callback URL:** leave empty. Jig doesn't use a browser redirect with GitHub.
   - **Expire user authorization tokens:** leave **ticked**. Tokens then last 8 hours and are renewed with a refresh token, which Jig keeps in the vault.
   - **Request user authorization (OAuth) during installation:** leave unticked.
   - **Enable Device Flow:** **tick it**. Without it, GitHub refuses Jig's sign-in.
4. **Post installation:** leave **Setup URL** empty.
5. **Webhook:** **untick Active**. Jig never receives webhooks.
6. **Permissions > Repository permissions:**
   - **Contents: Read-only** (read files);
   - **Issues: Read and write** (read issues, comment, open issues);
   - **Pull requests: Read and write** (read pull requests and comment on them; Jig has no tool that merges or closes);
   - **Metadata: Read-only** (GitHub sets this itself);
   - everything else: **No access**. **Organization permissions** and **Account permissions:** none.
7. **Subscribe to events:** none.
8. **Where can this GitHub App be installed?** **Any account**, so anyone using Jig can install it on their own repositories. ("Only on this account" would let only rlesueur use it.)
9. Choose **Create GitHub App**. On the app's **General** page:
   - copy the **Client ID** (it starts with `Iv`; not the numeric App ID);
   - note the app's URL name: the public page is `https://github.com/apps/<slug>`;
   - **don't** generate a client secret or a private key. Jig needs neither, and an unused secret is only something that could leak.
   - optionally upload a logo (**Display information**), which people see when they install the app.
10. **Put the IDs in Jig:** set `GITHUB_APP_CLIENT_ID = "<Client ID>"` and `GITHUB_APP_SLUG = "<slug>"` in `jig/connectors/apps.py`, and run:

    ```powershell
    .\.venv\Scripts\python -m pytest -q tests\test_connector_github.py -k built_in_app_gets_a_real_device_code
    ```

    It asks GitHub's real device sign-in for a code with the app's Client ID, and checks GitHub answers with a code for github.com/login/device. If device flow isn't enabled, it fails with GitHub's own `device_flow_disabled`. Then install the app on a test repository, connect once (`jig connect github --access write`) and run the live test above.

If you later change the app's permissions, everyone who installed it is asked by GitHub to accept the change; until they do, the app keeps the old permissions.

---

## Slack

Jig uses a bot that you create in your own workspace. Reading needs no approval; posting always needs your approval.

Official documentation: [Create an app](https://api.slack.com/quickstart), [Token types](https://api.slack.com/concepts/token-types), [Scopes](https://api.slack.com/scopes).

1. Go to [api.slack.com/apps](https://api.slack.com/apps) > **Create New App > From scratch**, name it "Jig" and pick your workspace.
2. **OAuth & Permissions > Scopes > Bot Token Scopes**, add `channels:read` (list public channels), `channels:history` (read channels the bot is in) and `chat:write` (post as the bot; for `write` access). Optionally `users:read`, to show names instead of user ids. If a required scope is missing, `jig connect slack` refuses and lists what is missing.
3. **Install to Workspace** and allow. The token you need is the **Bot User OAuth Token** (it starts with `xoxb-`; Jig refuses user tokens, `xoxp-`). If you add scopes later, reinstall the app.
4. Create a channel for testing, for example `#jig-test`, and type `/invite @Jig` in it. Copy its **Channel ID** (click the channel name; it is at the bottom, like `C0123456789`).
5. Connect: in Settings > Connections, under Slack, choose the access level, paste the token and choose **Connect**. Or run `.\.venv\Scripts\jig connect slack --access write` (or `--access read`), which asks for the token without showing it, or reads it with `--stdin`. Either way the token goes straight into the vault and is never shown again.

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
3. Copy the **Application ID** from **General Information**. In Settings > Connections, paste it into the Discord steps and Jig makes the link for you; or open `https://discord.com/oauth2/authorize?client_id=YOUR_APPLICATION_ID&scope=bot&permissions=68608`, then pick your server. `68608` is View Channels + Send Messages + Read Message History; nothing else is needed.
4. Create a channel for testing, for example `#jig-test`. Turn on **User Settings > Advanced > Developer Mode**, right-click the channel and **Copy Channel ID**.
5. Connect: in Settings > Connections, under Discord, paste the bot token and choose **Connect**. Or run `.\.venv\Scripts\jig connect discord --access write` (or `--access read`), which asks for the token without showing it, or reads it with `--stdin`.

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

Choose one way to sign in. Both work in Settings > Connections (type the homeserver, your Matrix ID or `token`, and the password or token) as well as in a terminal:

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

Everything happens in **Settings > Connections > Signal > Set up step by step**, with no terminal:

1. **Get signal-cli.** signal-cli 0.14 needs Java 25 or newer. Jig looks for both (in `JAVA_HOME`, on `PATH` and in the usual install folders, so a Java you install while Jig is running is found too) and says what it found. **Download for me** gets signal-cli from [its official GitHub release](https://github.com/AsamK/signal-cli/releases) and, if this computer has no Java 25, the Eclipse Temurin Java runtime from [Adoptium](https://adoptium.net/temurin/releases/?version=25&package=jre). Jig checks each file against the SHA-256 checksum its publisher lists (GitHub's for signal-cli, Adoptium's for Java) and deletes it if it doesn't match. Both go in the `tools` folder of Jig's data folder and are used only by Jig. They are about 120 MB and 60 MB, and neither is part of Jig or its installer: signal-cli is GPL-3.0, and Temurin is GPL-2.0 with the Classpath Exception; both are free. The history records each download with its source and checksum (`connector.signal_download`). If you'd rather install them yourself, install Java 25 or newer (Eclipse Temurin is the simplest free one; its Windows `.msi` installer adds Java to `PATH`), unpack `signal-cli-<version>.tar.gz` into a folder in your profile, and choose **Use the one I found** or type where `bin\signal-cli.bat` is.
2. **Link it to your phone.** Choose **Show the code**: Jig runs signal-cli's link step (`signal-cli link -n Jig`) and shows the `sgnl://linkdevice` link it prints as a QR code, made on your computer. In Signal on your phone, open **Settings > Linked devices > Link new device** and scan it. signal-cli drops an unscanned code after about two minutes, so Jig shows a fresh one by itself, for up to 10 minutes. When signal-cli reports the number it linked, Jig checks that signal-cli now has that account and says so.
3. **Connect.** Choose what Jig may do and **Connect**: Jig has filled in your number and where signal-cli is.

Jig starts signal-cli's Java itself, with the classpath and options from signal-cli's Windows start script: run through `cmd.exe`, the script's one long line goes over cmd's 8191-character limit when signal-cli is in a long folder path (such as Jig's data folder for many user names).

You can also connect from a terminal, once signal-cli is linked:

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

## WhatsApp

Jig uses Meta's [WhatsApp Business Cloud API](https://developers.facebook.com/documentation/business-messaging/whatsapp/get-started) only. It does not link a personal WhatsApp account, and it does not use WhatsApp Web.

Official documentation: [Get started](https://developers.facebook.com/documentation/business-messaging/whatsapp/get-started), [Text messages](https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/text-messages), [Webhooks](https://developers.facebook.com/docs/whatsapp/cloud-api/guides/set-up-webhooks/).

You create three values in Meta, then type them in **Settings > Connections > WhatsApp** (Set up step by step, or the connect form on that page). They go straight into the vault. Jig asks Meta whether the token can see that phone number on that WhatsApp Business account, and stores nothing if Meta says no.

1. [developers.facebook.com/apps](https://developers.facebook.com/apps/) > **Create app**. Name it, then choose the use case **Connect with customers through WhatsApp**, pick or create a business portfolio, and create the app.
2. **WhatsApp > API Setup**. Copy:
   - the **Phone number ID** under the From number;
   - the **WhatsApp Business Account ID** (Meta also calls this the Messaging account ID).
3. The token on API Setup expires quickly. In [Business settings > System users](https://business.facebook.com/latest/settings/system_users), add a system user, assign your app (**Manage app**) and your WhatsApp account (**Manage WhatsApp Business accounts**), then **Generate token** with these permissions: `business_management`, `whatsapp_business_messaging` and `whatsapp_business_management`. Copy the token when Meta shows it. It is shown once.
4. In Settings > Connections, under WhatsApp, choose what Jig may do. **read** sees the business number only. **send** can also send a text message, and each send needs your approval. Paste the token, the Phone number ID and the WhatsApp Business Account ID, then choose **Connect**.

   Or, on this computer, three lines on standard input, in that order (token, phone number ID, WhatsApp Business Account ID):

   ```powershell
   .\.venv\Scripts\jig connect whatsapp --access send --stdin
   ```

**Incoming messages.** Meta delivers them only to an HTTPS webhook that you host on the public internet. The Cloud API has no call that lists recent messages. Jig does not ask you to open a port, and it has no tool that reads or invents incoming WhatsApp messages.

**Sending.** A text message is `POST /<PHONE_NUMBER_ID>/messages` on `graph.facebook.com`. It only succeeds inside the 24-hour window after that person last messaged the business number (or, for Meta's test number, after you have added their number on API Setup and they have confirmed the code). Outside that window Meta refuses the send, and Jig reports Meta's error. It does not switch to a template message. Every send needs your approval. Messages must start with the prefix below, and can only go to numbers in `allowed_targets`, in international form.

```toml
[connectors.whatsapp]
allowed_targets = ["+447700900123"]   # the only numbers Jig may message
required_prefix = "[Jig test]"
```

`jig disconnect whatsapp` deletes the token and the ids from the vault. Meta has no way for Jig to revoke the token: in Business settings > System users, remove the token or the system user.

The live test (`tests\test_connector_whatsapp_live.py`) looks in the vault of `JIG_LIVE_WHATSAPP_CONFIG`, or of `%USERPROFILE%\.jig-connectors-test\jig.toml` when that file exists. It skips, and says so, when there is no WhatsApp token. It does not send while `allowed_targets` is still the placeholder `+440000000000`.

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
