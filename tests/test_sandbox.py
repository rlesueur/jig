"""Every file tool is confined to the agent's workspace."""

from __future__ import annotations

import json
import sys

import pytest

from jig.constants import Mode
from jig.errors import SandboxViolation
from jig.model import ToolCall
from jig.policy.gate import CallContext

ESCAPES = [
    "../secret.txt",
    "..\\secret.txt",
    "notes/../../secret.txt",
    "C:\\Windows\\win.ini",
    "C:secret.txt",
    "/etc/passwd",
    "\\\\server\\share\\file.txt",
    "file.txt:hidden",
    "NUL",
    "con.txt",
]


@pytest.mark.parametrize("path", ESCAPES)
def test_sandbox_rejects_escape(jig, path):
    with pytest.raises(SandboxViolation):
        jig.sandbox.resolve(path)


def test_sandbox_accepts_inner_paths(jig):
    assert jig.sandbox.resolve("notes/today.md") == jig.sandbox.root / "notes" / "today.md"
    assert jig.sandbox.resolve(".") == jig.sandbox.root


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows-only")
def test_sandbox_rejects_junction_out_of_jail(jig, tmp_path):
    import _winapi

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("top secret", encoding="utf-8")
    _winapi.CreateJunction(str(outside), str(jig.sandbox.root / "link"))
    with pytest.raises(SandboxViolation):
        jig.sandbox.resolve("link/secret.txt")


async def test_read_file_tool_refuses_traversal(jig, tmp_path):
    (tmp_path / "secret.txt").write_text("top secret", encoding="utf-8")
    call = ToolCall(id="c1", name="read_file", arguments_raw=json.dumps({"path": "../../secret.txt"}))
    outcome = await jig.executor.execute(call, CallContext("r_sb", None, Mode.ACTION, "read a file"))
    assert not outcome.ok
    assert outcome.error_type == "SandboxViolation"
    assert "top secret" not in outcome.message_content()
