"""GitHub, live, against the user's own account. Opt-in: skipped unless both of these are set:

  JIG_LIVE_GITHUB_CONFIG   a Jig config whose data directory has GitHub connected with 'write' access
                           ('jig --config <it> connect github --access write'), and which sets
                               [connectors.github]
                               allowed_targets = ["<owner>/jig-connector-test"]
                               required_prefix = "[Jig test]"
  JIG_LIVE_GITHUB_REPO     that same "<owner>/jig-connector-test"

The user creates the test repository first and gives the token Issues: Read and write on it. The test opens one
"[Jig test]" issue there, comments on it, reads both back, checks that a write elsewhere is blocked and that a
denied approval stops a comment, and closes the issue at the end (directly through the connection, as cleanup:
Jig has no tool to close issues). For the run it adds a rule allowing every GitHub tool, to show that writes
still need approval, and deletes that rule afterwards.
"""

from __future__ import annotations

import os
import uuid

import pytest

from jig.connectors import github as gh

from .connector_live import call, live_runtime

CONFIG = os.environ.get("JIG_LIVE_GITHUB_CONFIG", "")
REPO = os.environ.get("JIG_LIVE_GITHUB_REPO", "")
pytestmark = pytest.mark.skipif(
    not (CONFIG and REPO), reason="live GitHub: set JIG_LIVE_GITHUB_CONFIG and JIG_LIVE_GITHUB_REPO after creating a "
    "'jig-connector-test' repository and connecting GitHub with write access (docs/connectors-setup.md)")
PREFIX = "[Jig test]"
ELSEWHERE = "octocat/Hello-World"  # public, so the lookup works and the limit is what stops the write


async def test_github_end_to_end_on_the_test_repo_only(capabilities):
    async with live_runtime(CONFIG, gh.NAME) as live:
        limits = live.config.connectors.get(gh.NAME)
        assert limits and [t.lower() for t in limits.allowed_targets] == [REPO.lower()], \
            f"[connectors.github] allowed_targets must be exactly [{REPO!r}]"
        assert limits.required_prefix == PREFIX
        intent = f"Test Jig's GitHub connector with '{PREFIX}' issues in {REPO}"
        tag = uuid.uuid4().hex[:8]

        repos = await call(live, "github_list_repos", {"max_results": 100}, intent=intent)
        assert repos.ok, repos.error
        assert REPO.lower() in [r["repo"].lower() for r in repos.result["repos"]], \
            f"the token can't see {REPO}; add it to the token's repositories"
        readme = await call(live, "github_read_file", {"repo": ELSEWHERE, "path": "README"}, intent=intent)
        assert readme.ok and "Hello World" in readme.result["text"], readme.error
        assert "approval" not in readme.policy and "sentinel" not in readme.policy

        rule = live.rules.create(tool="github_*", decision="allow", note=f"live test {tag}: writes still need approval")
        number = None
        try:
            created = await call(live, "github_create_issue", {
                "repo": REPO, "title": f"{PREFIX} live {tag}", "body": f"Made by Jig's live test {tag}."},
                intent=intent)
            assert created.ok, created.error
            assert created.policy["approval"]["status"] == "approved"
            assert created.policy["rule"]["decision"] == "allow"
            assert created.policy["resolved"]["repo"].lower() == REPO.lower()
            number = created.result["number"]

            commented = await call(live, "github_comment", {
                "repo": REPO, "number": number, "body": f"{PREFIX} comment {tag}"}, intent=intent)
            assert commented.ok, commented.error
            assert commented.policy["approval"]["status"] == "approved"
            assert commented.policy["resolved"]["issue_title"] == f"{PREFIX} live {tag}"

            denied = await call(live, "github_comment", {
                "repo": REPO, "number": number, "body": f"{PREFIX} denied {tag}"}, intent=intent, approve=False)
            assert denied.error_type == "ApprovalDenied"

            unprefixed = await call(live, "github_comment", {"repo": REPO, "number": number, "body": "Not a test"},
                                    intent=intent)
            assert unprefixed.error_type == "PolicyBlocked" and "required_prefix" in unprefixed.error
            elsewhere = await call(live, "github_create_issue", {"repo": ELSEWHERE, "title": f"{PREFIX} wrong repo"},
                                   intent=intent)
            assert elsewhere.error_type == "PolicyBlocked" and "allowed_targets" in elsewhere.error

            read = await call(live, "github_read_issue", {"repo": REPO, "number": number}, intent=intent)
            assert read.ok, read.error
            assert read.result["title"] == f"{PREFIX} live {tag}" and read.result["kind"] == "issue"
            assert [c["body"] for c in read.result["comment_list"]] == [f"{PREFIX} comment {tag}"]
            assert "approval" not in read.policy and "sentinel" not in read.policy
            listed = await call(live, "github_list_issues", {"repo": REPO, "state": "open"}, intent=intent)
            assert listed.ok and number in [i["number"] for i in listed.result["items"]], listed.error
        finally:
            live.rules.delete(rule["id"])
            if number is not None:
                r = await live.connectors.request(gh.NAME, "PATCH", f"{gh.API}/repos/{REPO}/issues/{number}",
                                                  json_body={"state": "closed", "state_reason": "completed"},
                                                  headers=dict(gh.HEADERS))
                assert r.json()["state"] == "closed"
