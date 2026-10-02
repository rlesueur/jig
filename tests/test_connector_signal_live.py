"""Signal, live, through the user's own linked signal-cli. Opt-in: skipped unless both are set:

  JIG_LIVE_SIGNAL_CONFIG   a Jig config whose data directory has Signal connected
                           ('jig --config <it> connect signal --option number=+44... --option signal_cli=<path>'),
                           and which sets
                               [connectors.signal]
                               allowed_targets = ["<your own number>"]
                               required_prefix = "[Jig test]"
  JIG_LIVE_SIGNAL_NUMBER   your own number (+44...): the only number the test sends to, so the message lands
                           in Note to Self

The test sends one "[Jig test]" message to Note to Self. A send to any other number is blocked by the
limits, and a denied approval sends nothing.
"""

from __future__ import annotations

import os
import uuid

import pytest

from .connector_live import call, live_runtime

CONFIG = os.environ.get("JIG_LIVE_SIGNAL_CONFIG", "")
NUMBER = os.environ.get("JIG_LIVE_SIGNAL_NUMBER", "")
pytestmark = pytest.mark.skipif(not (CONFIG and NUMBER), reason="live Signal: set JIG_LIVE_SIGNAL_CONFIG and "
                                "JIG_LIVE_SIGNAL_NUMBER (your own number) after linking signal-cli and connecting "
                                "Signal (docs/connectors-setup.md)")
PREFIX = "[Jig test]"
# A number from Ofcom's range for drama, which no one has.
NOT_ALLOWED = "+447700900999"


async def test_signal_note_to_self_only(capabilities):
    async with live_runtime(CONFIG, "signal") as live:
        assert live.connections.get("signal")["account"] == NUMBER, "test with the connected account's own number"
        limits = live.config.connectors.get("signal")
        assert limits and limits.allowed_targets == [NUMBER] and limits.required_prefix == PREFIX
        intent = f"Test Jig's Signal connector by sending a '{PREFIX}' message to my own Note to Self ({NUMBER})"
        tag = uuid.uuid4().hex[:8]

        sent = await call(live, "signal_send_message", {"recipient": NUMBER, "text": f"{PREFIX} live {tag}"},
                          intent=intent)
        assert sent.ok, sent.error
        assert sent.result["sent"] and sent.result["note_to_self"]
        assert sent.policy["approval"]["status"] == "approved" and sent.policy["resolved"]["note_to_self"]

        other = await call(live, "signal_send_message", {"recipient": NOT_ALLOWED, "text": f"{PREFIX} wrong {tag}"},
                           intent=intent)
        assert other.error_type == "PolicyBlocked" and "allowed_targets" in other.error

        unprefixed = await call(live, "signal_send_message", {"recipient": NUMBER, "text": f"no prefix {tag}"},
                                intent=intent)
        assert unprefixed.error_type == "PolicyBlocked" and "required_prefix" in unprefixed.error

        denied = await call(live, "signal_send_message", {"recipient": NUMBER, "text": f"{PREFIX} denied {tag}"},
                            intent=intent, approve=False)
        assert denied.error_type == "ApprovalDenied" and denied.result is None
        assert denied.policy["approval"]["status"] != "approved", "the gate stopped it before signal-cli ran"
