"""WhatsApp, live, against the user's own Cloud API app. Skipped, with the reason, when the vault has no
WhatsApp token. Nothing in this file is a credential.

It looks at JIG_LIVE_WHATSAPP_CONFIG, or at ~/.jig-connectors-test/jig.toml when that file exists. The
token is only checked by name (connector.whatsapp.grant); its value is never read here. When a token is
present, the test sends one "[Jig test]" message to the number in allowed_targets, and only if that number
is not the placeholder +440000000000. A Jig that is already serving that config is left running; the test
skips instead.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from pathlib import Path

import pytest

from jig.config import load_config
from jig.connectors.base import grant_secret

from .connector_live import call, live_runtime

PREFIX = "[Jig test]"
PLACEHOLDER = "+440000000000"
OTHER = "+447700900001"


def _config_path() -> Path | None:
    chosen = os.environ.get("JIG_LIVE_WHATSAPP_CONFIG", "").strip()
    if chosen:
        return Path(chosen)
    home = Path.home() / ".jig-connectors-test" / "jig.toml"
    return home if home.is_file() else None


def _token_in_vault(config_path: Path) -> tuple[bool, str]:
    """Whether connector.whatsapp.grant is stored. The ciphertext is never selected."""
    try:
        config = load_config(config_path)
    except Exception as exc:
        return False, f"could not read {config_path} ({type(exc).__name__}). Nothing was sent."
    db = config.db_path
    if not db.is_file():
        return False, (f"no WhatsApp token in the vault (no database yet for {config_path}). Connect WhatsApp in "
                       "Settings > Connections (docs/connectors-setup.md). Nothing was sent.")
    try:
        conn = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return False, f"could not read the vault ({type(exc).__name__}). Nothing was sent."
    try:
        try:
            row = conn.execute("SELECT 1 FROM secrets WHERE name = ? LIMIT 1",
                               (grant_secret("whatsapp"),)).fetchone()
        except sqlite3.Error as exc:
            return False, f"could not read the vault ({type(exc).__name__}). Nothing was sent."
    finally:
        conn.close()
    if row is None:
        return False, ("no WhatsApp token in the vault. Connect WhatsApp in Settings > Connections "
                       "(docs/connectors-setup.md). Nothing was sent.")
    return True, ""


async def test_whatsapp_live_send_only_when_the_vault_has_a_token():
    path = _config_path()
    if path is None or not path.is_file():
        pytest.skip("no Jig config to check for a WhatsApp token (set JIG_LIVE_WHATSAPP_CONFIG, or create "
                    "~/.jig-connectors-test/jig.toml). Nothing was sent.")
    present, reason = _token_in_vault(path)
    if not present:
        pytest.skip(reason)
    async with live_runtime(str(path), "whatsapp") as live:
        limits = live.config.connectors.get("whatsapp")
        assert limits and limits.required_prefix == PREFIX
        targets = [t for t in limits.allowed_targets if t != PLACEHOLDER]
        if not targets:
            pytest.skip(f"WhatsApp is connected, but [connectors.whatsapp] allowed_targets is still the "
                        f"placeholder {PLACEHOLDER}. Set it to the number Jig may message, in international form. "
                        "Nothing was sent.")
        target = targets[0]
        intent = f"Test Jig's WhatsApp connector with a '{PREFIX}' message to {target}"
        tag = uuid.uuid4().hex[:8]

        account = await call(live, "whatsapp_account", {}, intent=intent)
        assert account.ok, account.error
        assert account.result["incoming_messages"] == "unavailable"
        assert "messages" not in account.result
        assert "phone_number_id" not in account.result and "waba_id" not in account.result

        sent = await call(live, "whatsapp_send_message", {"recipient": target, "text": f"{PREFIX} live {tag}"},
                          intent=intent)
        assert sent.ok, sent.error
        assert sent.policy["approval"]["status"] == "approved"
        assert sent.result["sent"] is True and sent.result["recipient"] == target and sent.result["message_id"]
        assert "text" not in sent.result and tag not in json_text(sent.result)

        blocked = OTHER if OTHER != target else "+447700900002"
        elsewhere = await call(live, "whatsapp_send_message",
                               {"recipient": blocked, "text": f"{PREFIX} wrong {tag}"}, intent=intent)
        assert elsewhere.error_type == "PolicyBlocked" and "allowed_targets" in elsewhere.error

        unprefixed = await call(live, "whatsapp_send_message", {"recipient": target, "text": f"no prefix {tag}"},
                                intent=intent)
        assert unprefixed.error_type == "PolicyBlocked" and "required_prefix" in unprefixed.error

        denied = await call(live, "whatsapp_send_message", {"recipient": target, "text": f"{PREFIX} denied {tag}"},
                            intent=intent, approve=False)
        assert denied.error_type == "ApprovalDenied"
        assert denied.policy["approval"]["status"] != "approved"


def json_text(result: dict) -> str:
    return " ".join(str(v) for v in result.values())
