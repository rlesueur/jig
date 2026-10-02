"""Gmail, live, against the user's own mailbox. Opt-in: skipped unless both are set:

  JIG_LIVE_GMAIL_CONFIG   a Jig config whose data directory has Gmail connected with 'manage' access
                          ('jig --config <it> connect gmail --access manage'), and which sets
                              [connectors.gmail]
                              allowed_recipients = ["<your address>"]
                              required_prefix = "[Jig test]"
  JIG_LIVE_GMAIL_ADDRESS  that address, which must be the only allowed recipient

Rules, from the user: send only to your own address, label every test message "[Jig test]", and act only on
messages the test itself sent. Nothing is deleted except the one draft the test creates. Each action goes
through the real gate (the Sentinel reviews it) and the test answers the approval, standing in for the user.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path

import pytest

from jig.config import load_config
from jig.constants import Mode
from jig.connectors import gmail
from jig.model import ToolCall
from jig.policy.gate import CallContext
from jig.runtime import Jig

from .connector_live import skip_if_in_use

CONFIG = os.environ.get("JIG_LIVE_GMAIL_CONFIG", "")
ADDRESS = os.environ.get("JIG_LIVE_GMAIL_ADDRESS", "").strip().lower()
pytestmark = pytest.mark.skipif(
    not (CONFIG and ADDRESS),
    reason="live Gmail: set JIG_LIVE_GMAIL_CONFIG and JIG_LIVE_GMAIL_ADDRESS after connecting Gmail "
           "(docs/connectors-setup.md)")
PREFIX = "[Jig test]"


@pytest.fixture
async def live(capabilities):
    config = load_config(Path(CONFIG))
    limits = config.connectors.get("gmail")
    assert limits and limits.allowed_recipients == [ADDRESS] and limits.required_prefix == PREFIX, (
        "the live config must allow only JIG_LIVE_GMAIL_ADDRESS and require the '[Jig test]' prefix")
    skip_if_in_use(CONFIG, config)
    runtime = Jig(config)
    await runtime.start(run_scheduler=False, check_capabilities=False)
    try:
        row = runtime.connections.get("gmail")
        assert row and row["status"] == "connected", "Gmail is not connected in JIG_LIVE_GMAIL_CONFIG's data dir"
        assert row["account"].lower() == ADDRESS, f"connected as {row['account']}, not {ADDRESS}"
        yield runtime
    finally:
        await runtime.stop()


async def _call(jig: Jig, name: str, args: dict, *, intent: str, approve: bool = True):
    """Run one call through the real gate, answering its approval (if any) like the user would."""
    run_id = f"r_live_{uuid.uuid4().hex[:8]}"
    task = asyncio.create_task(jig.executor.execute(
        ToolCall(id=uuid.uuid4().hex[:8], name=name, arguments_raw=json.dumps(args)),
        CallContext(run_id=run_id, task_id=None, mode=Mode.ACTION, intent=intent)))
    while not task.done():
        for a in jig.approvals.list(status="pending"):
            if a["run_id"] == run_id:
                jig.approvals.respond(a["id"], approve=approve, note="live test")
        await asyncio.sleep(0.2)
    return await task


async def test_gmail_end_to_end_on_test_messages_only(live):
    tag = uuid.uuid4().hex[:10]
    subject = f"{PREFIX} live {tag}"
    intent = f"Test Jig's Gmail connector on test messages sent to {ADDRESS} with subjects starting '{PREFIX}'"

    sent = await _call(live, "gmail_send", {"to": [ADDRESS], "subject": subject, "body": f"Test body {tag}"},
                       intent=intent)
    assert sent.ok, sent.error
    thread_id = sent.result["thread_id"]
    assert sent.policy["approval"]["status"] == "approved"

    async def found():
        r = await _call(live, "gmail_search", {"query": f'subject:"{tag}"', "max_results": 5}, intent=intent)
        return r.ok and any(t["thread_id"] == thread_id for t in r.result["threads"])
    for _ in range(30):
        if await found():
            break
        await asyncio.sleep(2)
    else:
        pytest.fail("the sent test message never appeared in search")

    read = await _call(live, "gmail_read_thread", {"thread_id": thread_id}, intent=intent)
    assert read.ok and read.result["untrusted"]
    assert f"Test body {tag}" in read.result["messages"][0]["text"]
    assert "sentinel" not in read.policy and "approval" not in read.policy, "reads are not reviewed or asked"

    reply = await _call(live, "gmail_reply", {"thread_id": thread_id, "to": [ADDRESS], "body": f"Reply {tag}"},
                        intent=intent)
    assert reply.ok, reply.error
    assert reply.result["thread_id"] == thread_id and reply.result["subject"] == f"Re: {subject}"
    assert reply.policy["resolved"]["thread_subject"] == subject

    draft = await _call(live, "gmail_create_draft", {"to": [ADDRESS], "subject": f"Re: {subject}",
                                                     "body": f"Draft {tag}", "thread_id": thread_id}, intent=intent)
    assert draft.ok, draft.error
    try:
        starred = await _call(live, "gmail_modify_labels", {"thread_id": thread_id, "add": ["STARRED"]}, intent=intent)
        assert starred.ok and "STARRED" in starred.result["labels"]
        unstarred = await _call(live, "gmail_modify_labels", {"thread_id": thread_id, "remove": ["STARRED"]},
                                intent=intent)
        assert unstarred.ok and "STARRED" not in unstarred.result["labels"]
        archived = await _call(live, "gmail_archive", {"thread_id": thread_id}, intent=intent)
        assert archived.ok and archived.result["archived"]
        trash = await _call(live, "gmail_modify_labels", {"thread_id": thread_id, "add": ["TRASH"]}, intent=intent)
        assert not trash.ok and trash.error_type == "ToolArgumentError"
    finally:
        await live.connectors.request("gmail", "DELETE", f"{gmail.API}/drafts/{draft.result['draft_id']}")

    outsider = await _call(live, "gmail_send", {"to": ["someone-else@example.com"], "subject": subject, "body": "x"},
                           intent=intent)
    assert outsider.error_type == "PolicyBlocked" and "allowed_recipients" in outsider.error
    denied = await _call(live, "gmail_send", {"to": [ADDRESS], "subject": f"{subject} denied", "body": "x"},
                         intent=intent, approve=False)
    assert denied.error_type == "ApprovalDenied"


async def test_a_draft_reply_is_sent_as_it_is_in_its_thread_and_within_the_limits(live):
    tag = uuid.uuid4().hex[:10]
    subject = f"{PREFIX} draft {tag}"
    intent = f"Test sending Gmail drafts on test messages to {ADDRESS} with subjects starting '{PREFIX}'"
    sent = await _call(live, "gmail_send", {"to": [ADDRESS], "subject": subject, "body": f"Question {tag}"},
                       intent=intent)
    assert sent.ok, sent.error
    thread_id = sent.result["thread_id"]
    draft = await _call(live, "gmail_create_draft", {"to": [ADDRESS], "subject": f"Re: {subject}",
                                                     "body": f"Answer {tag}", "thread_id": thread_id}, intent=intent)
    assert draft.ok, draft.error
    draft_id = draft.result["draft_id"]

    out = await _call(live, "gmail_send_draft", {"draft_id": draft_id}, intent=intent)
    assert out.ok, out.error
    assert out.policy["approval"]["status"] == "approved"
    assert out.policy["resolved"]["draft_to"] == [ADDRESS] and out.policy["resolved"]["draft_subject"] == f"Re: {subject}"
    assert f"Answer {tag}" in out.policy["resolved"]["draft_text"]
    assert out.result["thread_id"] == thread_id and out.result["subject"] == f"Re: {subject}"
    thread = await _call(live, "gmail_read_thread", {"thread_id": thread_id}, intent=intent)
    assert [m["text"].strip() for m in thread.result["messages"]] == [f"Question {tag}", f"Answer {tag}"]
    gone = await _call(live, "gmail_send_draft", {"draft_id": draft_id}, intent=intent)
    assert not gone.ok, "a sent draft is no longer in Drafts"

    # A draft the user wrote themselves, outside the limits, is refused before anyone is asked.
    raw = gmail._raw(to=[ADDRESS], cc=[], subject=f"Not a test {tag}", body="x")
    made = (await live.connectors.request("gmail", "POST", f"{gmail.API}/drafts",
                                          json_body={"message": {"raw": raw}})).json()
    try:
        refused = await _call(live, "gmail_send_draft", {"draft_id": made["id"]}, intent=intent)
        assert refused.error_type == "PolicyBlocked" and "required_prefix" in refused.error
        assert not (refused.policy or {}).get("approval")
    finally:
        await live.connectors.request("gmail", "DELETE", f"{gmail.API}/drafts/{made['id']}")
        await live.connectors.request("gmail", "POST", f"{gmail.API}/threads/{thread_id}/trash")
