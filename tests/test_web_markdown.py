"""The web UI's reply formatter (jig/web/markdown.js), tested with Node's built-in test runner."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

TESTS = Path(__file__).parent / "web"


def test_markdown_formatter() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed, so the web UI's JavaScript tests cannot run")
    result = subprocess.run([node, "--test", str(TESTS / "markdown.test.mjs")], capture_output=True, text=True,
                            encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
