"""Discord, live, against the user's own server. Opt-in: skipped unless both are set:

  JIG_LIVE_DISCORD_CONFIG    a Jig config whose data directory has Discord connected with 'write' access
                             ('jig --config <it> connect discord --access write'), and which sets
                                 [connectors.discord]
                                 allowed_targets = ["<the test channel id>"]
                                 required_prefix = "[Jig test]"
  JIG_LIVE_DISCORD_CHANNEL   the test channel's id (Developer Mode > Copy Channel ID), for example #jig-test

The test posts only "[Jig test]" messages, only in that channel. The server needs one other text channel
the bot can see (such as #general), which the test tries to post in and must be refused.
"""

from __future__ import annotations

import os
import uuid

import pytest

from .connector_live import call, live_runtime

CONFIG = os.environ.get("JIG_LIVE_DISCORD_CONFIG", "")
CHANNEL = os.environ.get("JIG_LIVE_DISCORD_CHANNEL", "")
pytestmark = pytest.mark.skipif(not (CONFIG and CHANNEL), reason="live Discord: set JIG_LIVE_DISCORD_CONFIG and "
                                "JIG_LIVE_DISCORD_CHANNEL after connecting Discord and inviting the bot to your "
                                "server (docs/connectors-setup.md)")
PREFIX = "[Jig test]"


async def test_discord_end_to_end_in_the_test_channel_only(capabilities):
    async with live_runtime(CONFIG, "discord") as live:
        limits = live.config.connectors.get("discord")
        assert limits and limits.allowed_targets == [CHANNEL] and limits.required_prefix == PREFIX
        intent = f"Test Jig's Discord connector with a '{PREFIX}' message in the Discord channel {CHANNEL}"
        tag = uuid.uuid4().hex[:8]
        content = f"{PREFIX} live {tag}"

        posted = await call(live, "discord_post_message", {"channel_id": CHANNEL, "content": content}, intent=intent)
        assert posted.ok, posted.error
        assert posted.policy["approval"]["status"] == "approved"
        assert posted.policy["resolved"]["channel_id"] == CHANNEL
        message_id = posted.result["message_id"]

        read = await call(live, "discord_read_channel", {"channel_id": CHANNEL, "limit": 10}, intent=intent)
        assert read.ok, read.error
        assert "approval" not in read.policy and "sentinel" not in read.policy
        mine = [m for m in read.result["messages"] if m["message_id"] == message_id]
        assert mine and mine[0]["content"] == content and mine[0]["bot"]

        page = await call(live, "discord_read_channel", {"channel_id": CHANNEL, "limit": 1}, intent=intent)
        assert page.ok and page.result["next_before"] == page.result["messages"][0]["message_id"], page.result
        older = await call(live, "discord_read_channel",
                           {"channel_id": CHANNEL, "limit": 1, "before": page.result["next_before"]}, intent=intent)
        assert older.ok and older.result["messages"], older.error
        assert int(older.result["messages"][0]["message_id"]) < int(page.result["next_before"]), "it reads on, older"

        servers = await call(live, "discord_list_channels", {}, intent=intent)
        assert servers.ok, servers.error
        other = None
        for server in servers.result["servers"]:
            listed = await call(live, "discord_list_channels", {"guild_id": server["guild_id"]}, intent=intent)
            assert listed.ok, listed.error
            ids = [c["channel_id"] for c in listed.result["channels"]]
            if CHANNEL in ids:
                other = next((i for i in ids if i != CHANNEL), None)
                break
        assert other, "the test channel's server needs another text channel (such as #general) for the refusal check"
        elsewhere = await call(live, "discord_post_message", {"channel_id": other, "content": f"{PREFIX} wrong {tag}"},
                               intent=intent)
        assert elsewhere.error_type == "PolicyBlocked" and "allowed_targets" in elsewhere.error

        denied = await call(live, "discord_post_message",
                            {"channel_id": CHANNEL, "content": f"{PREFIX} denied {tag}"}, intent=intent, approve=False)
        assert denied.error_type == "ApprovalDenied"
        after = await call(live, "discord_read_channel", {"channel_id": CHANNEL, "limit": 20}, intent=intent)
        assert after.ok and not any(f"denied {tag}" in m["content"] for m in after.result["messages"])
