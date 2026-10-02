"""Matrix, live, against the user's own account. Opt-in: skipped unless both are set:

  JIG_LIVE_MATRIX_CONFIG   a Jig config whose data directory has Matrix connected with 'send' access
                           ('jig --config <it> connect matrix'), and which sets
                               [connectors.matrix]
                               allowed_targets = ["<the test room id>"]
                               required_prefix = "[Jig test]"
  JIG_LIVE_MATRIX_ROOM     the id of that test room (!abc123:example.org): one the user created and joined,
                           WITHOUT end-to-end encryption

The test posts one "[Jig test]" message to that room and reads it back. A post to any other room is
blocked by the limits, and a denied approval posts nothing.
"""

from __future__ import annotations

import os
import uuid

import pytest

from .connector_live import call, live_runtime

CONFIG = os.environ.get("JIG_LIVE_MATRIX_CONFIG", "")
ROOM = os.environ.get("JIG_LIVE_MATRIX_ROOM", "")
pytestmark = pytest.mark.skipif(not (CONFIG and ROOM), reason="live Matrix: set JIG_LIVE_MATRIX_CONFIG and "
                                "JIG_LIVE_MATRIX_ROOM after connecting Matrix and creating an unencrypted test room "
                                "(docs/connectors-setup.md)")
PREFIX = "[Jig test]"


async def test_matrix_end_to_end_in_the_test_room_only(capabilities):
    async with live_runtime(CONFIG, "matrix") as live:
        limits = live.config.connectors.get("matrix")
        assert limits and limits.allowed_targets == [ROOM] and limits.required_prefix == PREFIX
        intent = f"Test Jig's Matrix connector by posting a '{PREFIX}' message in the test room {ROOM}"
        rooms = await call(live, "matrix_list_rooms", {}, intent=intent)
        assert rooms.ok, rooms.error
        test_room = next((r for r in rooms.result["rooms"] if r["room_id"] == ROOM), None)
        assert test_room, f"the account has not joined {ROOM}"
        assert not test_room["encrypted"], f"{ROOM} is end-to-end encrypted; use an unencrypted test room"

        tag = uuid.uuid4().hex[:8]
        sent = await call(live, "matrix_send_message", {"room_id": ROOM, "text": f"{PREFIX} live {tag}"},
                          intent=intent)
        assert sent.ok, sent.error
        assert sent.policy["approval"]["status"] == "approved"
        assert sent.policy["resolved"]["room_id"] == ROOM and sent.policy["resolved"]["encrypted"] is False

        read = await call(live, "matrix_read_room", {"room_id": ROOM, "limit": 20}, intent=intent)
        assert read.ok, read.error
        assert "approval" not in read.policy and "sentinel" not in read.policy
        mine = [m for m in read.result["messages"] if m.get("event_id") == sent.result["event_id"]]
        assert mine and mine[0]["text"] == f"{PREFIX} live {tag}"

        other = next((r["room_id"] for r in rooms.result["rooms"] if r["room_id"] != ROOM),
                     "!jig-not-allowed:matrix.org")
        elsewhere = await call(live, "matrix_send_message", {"room_id": other, "text": f"{PREFIX} wrong room {tag}"},
                               intent=intent)
        assert elsewhere.error_type == "PolicyBlocked" and "allowed_targets" in elsewhere.error

        unprefixed = await call(live, "matrix_send_message", {"room_id": ROOM, "text": f"no prefix {tag}"},
                                intent=intent)
        assert unprefixed.error_type == "PolicyBlocked" and "required_prefix" in unprefixed.error

        denied = await call(live, "matrix_send_message", {"room_id": ROOM, "text": f"{PREFIX} denied {tag}"},
                            intent=intent, approve=False)
        assert denied.error_type == "ApprovalDenied"
        after = await call(live, "matrix_read_room", {"room_id": ROOM, "limit": 20}, intent=intent)
        assert after.ok and not any(f"denied {tag}" in (m.get("text") or "") for m in after.result["messages"])
