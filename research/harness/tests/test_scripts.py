"""The scheduled-task wrappers must not log to the same file as the Python logger they launch.

On Windows the wrapper's Out-File holds its log open, so a Python FileHandler on the same path fails with
PermissionError before the command runs (this silently disabled the restore watchdog on 2 Oct 2026).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
CLI = Path(__file__).resolve().parents[1] / "jigbench" / "cli.py"


def _python_log_prefixes() -> set[str]:
    return set(re.findall(r'_setup_logging\("([^"]+)"\)', CLI.read_text(encoding="utf-8")))


@pytest.mark.parametrize("script", ["overnight.ps1", "restore-watchdog.ps1"])
def test_wrapper_log_differs_from_python_log(script: str) -> None:
    text = (SCRIPTS / script).read_text(encoding="utf-8")
    m = re.search(r'\$log = Join-Path \$logs \("([^"{]+)-\{0:yyyyMMdd\}\.log"', text)
    assert m, f"{script}: log path pattern not found"
    prefixes = _python_log_prefixes()
    assert prefixes, "no _setup_logging names found in cli.py"
    assert m.group(1) not in prefixes, f"{script} logs to {m.group(1)}-<date>.log, which jigbench also opens"
