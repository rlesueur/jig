"""The apps Jig itself signs in with, so most people only have to sign in.

These are public identifiers, not secrets: a desktop app can't keep a secret, so both sign-ins work
without one (Microsoft with PKCE and a loopback redirect, GitHub with the device flow). The Jig project
owner registers each app once (docs/connectors-setup.md, "For the Jig project owner") and puts its IDs
here. While one is empty, that sign-in says plainly that the built-in app isn't set up and how to use your
own app instead; it never signs in with anything else.
"""

from __future__ import annotations

# Microsoft: the Application (client) ID of Jig's app registration ("Accounts in any organizational
# directory and personal Microsoft accounts", public client, redirect http://localhost).
MICROSOFT_CLIENT_ID = ""

# GitHub: the client ID of the Jig GitHub App (owned by the personal account rlesueur, device flow
# enabled) and its URL name, as in https://github.com/apps/<slug>.
GITHUB_APP_CLIENT_ID = ""
GITHUB_APP_SLUG = ""
