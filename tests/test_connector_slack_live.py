"""Slack, live, against the user's own workspace. Opt-in: skipped unless both are set:

  JIG_LIVE_SLACK_CONFIG    a Jig config whose data directory has Slack connected with 'write' access
                           ('jig --config <it> connect slack --access write'), and which sets
                               [connectors.slack]
                               allowed_targets = ["<the test channel id>"]
                               required_prefix = "[Jig test]"
  JIG_LIVE_SLACK_CHANNEL   the test channel's id (C...), for example #jig-test, with the bot invited to it

The test posts only "[Jig test]" messages, only in that channel. The workspace needs one other public
channel (every workspace has one, such as #general), which the test tries to post in and must be refused.
"""

from __future__ import annotations

import os
import uuid

import pytest

from .connector_live import call, live_runtime

CONFIG = os.environ.get("JIG_LIVE_SLACK_CONFIG", "")
CHANNEL = os.environ.get("JIG_LIVE_SLACK_CHANNEL", "")
pytestmark = pytest.mark.skipif(not (CONFIG and CHANNEL), reason="live Slack: set JIG_LIVE_SLACK_CONFIG and "
                                "JIG_LIVE_SLACK_CHANNEL after connecting Slack and inviting the bot to the test "
                                "channel (docs/connectors-setup.md)")
PREFIX = "[Jig test]"


async def test_slack_end_to_end_in_the_test_channel_only(capabilities):
    async with live_runtime(CONFIG, "slack") as live:
        limits = live.config.connectors.get("slack")
        assert limits and limits.allowed_targets == [CHANNEL] and limits.required_prefix == PREFIX
        intent = f"Test Jig's Slack connector with a '{PREFIX}' message in the Slack channel {CHANNEL}"
        tag = uuid.uuid4().hex[:8]
        text = f"{PREFIX} live {tag}"

        posted = await call(live, "slack_post_message", {"channel_id": CHANNEL, "text": text}, intent=intent)
        assert posted.ok, posted.error
        assert posted.policy["approval"]["status"] == "approved"
        assert posted.policy["resolved"]["channel_id"] == CHANNEL and posted.policy["resolved"]["bot_is_member"]
        ts = posted.result["ts"]

        replied = await call(live, "slack_reply_in_thread",
                             {"channel_id": CHANNEL, "thread_ts": ts, "text": f"{PREFIX} reply {tag}"}, intent=intent)
        assert replied.ok, replied.error
        assert replied.policy["resolved"]["thread_start"] == text

        read = await call(live, "slack_read_channel", {"channel_id": CHANNEL, "limit": 10}, intent=intent)
        assert read.ok, read.error
        assert "approval" not in read.policy and "sentinel" not in read.policy
        mine = [m for m in read.result["messages"] if m["ts"] == ts]
        assert mine and mine[0]["text"] == text and mine[0]["replies"] >= 1

        listed = await call(live, "slack_list_channels", {}, intent=intent)
        assert listed.ok, listed.error
        other = next((c["channel_id"] for c in listed.result["channels"] if c["channel_id"] != CHANNEL), None)
        assert other, "the workspace needs another public channel (such as #general) for the refusal check"
        elsewhere = await call(live, "slack_post_message", {"channel_id": other, "text": f"{PREFIX} wrong {tag}"},
                               intent=intent)
        assert elsewhere.error_type == "PolicyBlocked" and "allowed_targets" in elsewhere.error

        denied = await call(live, "slack_post_message", {"channel_id": CHANNEL, "text": f"{PREFIX} denied {tag}"},
                            intent=intent, approve=False)
        assert denied.error_type == "ApprovalDenied"
        after = await call(live, "slack_read_channel", {"channel_id": CHANNEL, "limit": 20}, intent=intent)
        assert after.ok and not any(f"denied {tag}" in m["text"] for m in after.result["messages"])
