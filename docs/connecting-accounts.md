# Connecting your accounts

Connectors let Jig work with your own accounts. Jig talks to each provider from your computer. There is no Jig server in between. Tokens stay in the vault on this computer.

The steps for each provider, with the permissions and why, are in [connectors-setup.md](connectors-setup.md). This page is how to start, and where each account is documented. Settings shows the same steps.

This is a beta (version 0.1.0b2). Each connector is built and has been tested against the provider's real service without an account. A live test against a connected account is recorded per connector in [connectors-setup.md](connectors-setup.md#status). Discord's live test is still waiting for a connected account. WhatsApp's live test stays skipped until a token is in the vault.

## Where you connect

1. Open **Settings > Connections** on the computer Jig runs on. Connecting from another device is refused.
2. Choose **Set up step by step** on the account (or open `#settings/connections/<name>`, for example `#settings/connections/gmail`). Jig walks the provider's pages one step at a time and checks the step before Next lights up.
3. Or open **How to connect** under that account and use **Connect**.

You can also use the terminal, with the same config as the Jig you run:

```powershell
.\.venv\Scripts\jig connections
.\.venv\Scripts\jig connect <provider>
.\.venv\Scripts\jig disconnect <provider>
```

Ask in chat, for example "help me connect Gmail", and Jig walks the same steps. It never asks you to paste a token, password or client file into the chat. Those only go into Settings or into `jig connect`.

Reading is just reading. Every send, post, write, label change or cancellation is reviewed by the Sentinel and needs your approval. See [Approvals and safety](approvals-and-safety.md).

## Which account is which

| Account | `jig connect` name | What you do first | Full steps |
| --- | --- | --- | --- |
| Gmail | `gmail` | Make your own small Google app, once, then sign in | [Google](connectors-setup.md#google-gmail-google-calendar-and-google-drive) |
| Google Calendar | `google-calendar` | The same Google app | [Google](connectors-setup.md#google-gmail-google-calendar-and-google-drive) |
| Google Drive | `google-drive` | The same Google app | [Google](connectors-setup.md#google-gmail-google-calendar-and-google-drive) |
| Outlook calendar and OneDrive | `microsoft` | Sign in. Jig has its own Microsoft app (personal, work or school). An organisation can use its own. | [Microsoft](connectors-setup.md#microsoft-outlook-calendar-and-onedrive) |
| GitHub | `github` | Sign in with the Jig GitHub App (a code shown in Jig) or a fine-grained token | [GitHub](connectors-setup.md#github) |
| Discord | `discord` | Your own bot's token, on a server you add the bot to | [Discord](connectors-setup.md#discord) |
| WhatsApp | `whatsapp` | Meta's WhatsApp Business Cloud API only: a token, phone number ID and WhatsApp Business Account ID | [WhatsApp](connectors-setup.md#whatsapp) |

**WhatsApp** is not a personal WhatsApp account. Jig can see the business number and, with your approval each time, send a text message inside Meta's 24-hour window after that person last messaged the business number. It cannot read incoming messages, and it does not switch to a template message when Meta refuses a send. The exact limits are in [connectors-setup.md](connectors-setup.md#whatsapp).

**Paying and booking** in the browser is not an account you connect. When the container browser is on, Jig asks you before a checkout, payment or booking, and it never types card details. See [connectors-setup.md](connectors-setup.md) and the README.

## Disconnect

**Disconnect…** in Settings, or `jig disconnect <provider>`, removes the tokens from the vault. Google's grant is also revoked at Google. Where Jig cannot revoke it (Microsoft, GitHub, Discord, WhatsApp), the guide says where to remove the access yourself.
