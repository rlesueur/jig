"""Google Drive, live, against the user's own account. Opt-in: skipped unless this is set:

  JIG_LIVE_GDRIVE_CONFIG   a Jig config whose data directory has Google Drive connected with 'write' access
                           ('jig --config <it> connect google-drive --access write'), and which sets
                               [connectors.google-drive]
                               allowed_targets = ["root"]
                               required_prefix = "[Jig test]"

The test creates one "[Jig test]" text file in My Drive, reads and changes it, and deletes it at the end
(Google lets Jig delete only files it created). It touches nothing else.
"""

from __future__ import annotations

import os
import uuid

import pytest

from jig.connectors import google_drive as gdrive

from .connector_live import call, live_runtime

CONFIG = os.environ.get("JIG_LIVE_GDRIVE_CONFIG", "")
pytestmark = pytest.mark.skipif(not CONFIG, reason="live Google Drive: set JIG_LIVE_GDRIVE_CONFIG after connecting "
                                "Google Drive (docs/connectors-setup.md)")
PREFIX = "[Jig test]"


async def test_google_drive_end_to_end_on_a_test_file_only(capabilities):
    async with live_runtime(CONFIG, "google-drive") as live:
        limits = live.config.connectors.get("google-drive")
        assert limits and limits.allowed_targets == ["root"] and limits.required_prefix == PREFIX
        tag = uuid.uuid4().hex[:8]
        name = f"{PREFIX} live {tag}.txt"
        intent = f"Test Jig's Google Drive connector with a file named '{name}' in My Drive"
        created = await call(live, "gdrive_create_file", {"name": name, "content": f"first {tag}"}, intent=intent)
        assert created.ok, created.error
        assert created.policy["approval"]["status"] == "approved"
        file_id = created.result["file_id"]
        try:
            read = await call(live, "gdrive_read_file", {"file_id": file_id}, intent=intent)
            assert read.ok and read.result["text"] == f"first {tag}" and read.result["untrusted"]
            assert "approval" not in read.policy
            updated = await call(live, "gdrive_update_file", {"file_id": file_id, "content": f"second {tag}"},
                                 intent=intent)
            assert updated.ok, updated.error
            assert updated.policy["resolved"]["file"] == name
            again = await call(live, "gdrive_read_file", {"file_id": file_id}, intent=intent)
            assert again.result["text"] == f"second {tag}"
            unmarked = await call(live, "gdrive_create_file", {"name": f"notes {tag}.txt", "content": "x"},
                                  intent=intent)
            assert unmarked.error_type == "PolicyBlocked" and "required_prefix" in unmarked.error
            denied = await call(live, "gdrive_create_file", {"name": f"{PREFIX} denied {tag}.txt", "content": "x"},
                                intent=intent, approve=False)
            assert denied.error_type == "ApprovalDenied"
        finally:
            await live.connectors.request(gdrive.NAME, "DELETE", f"{gdrive.API}/files/{file_id}")
