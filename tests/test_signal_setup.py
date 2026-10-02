"""Signal without a terminal (jig.connectors.signal_setup), with real programs and services only: Java's own
version strings, GitHub's and Adoptium's real release lists, and a real signal-cli and Java for the start script
and the link step.

The tests that run signal-cli need a folder laid out like Jig's own tools folder (``java/<a Java 25 runtime>``
and ``signal-cli/signal-cli-<version>``), as 'Download for me' makes it: set JIG_TEST_SIGNAL_TOOLS to it.
They never link to a phone: the link test stops once signal-cli has shown its code. JIG_TEST_SIGNAL_DOWNLOAD=1
also runs the real download (about 180 MB) into a temporary folder."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from jig.api.app import _qr_data_uri
from jig.connectors import signal_setup as ss
from jig.errors import ConnectorError

TOOLS = Path(os.environ.get("JIG_TEST_SIGNAL_TOOLS", ""))
needs_tools = pytest.mark.skipif(not (os.environ.get("JIG_TEST_SIGNAL_TOOLS") and ss.managed_signal_cli(TOOLS)),
                                 reason="set JIG_TEST_SIGNAL_TOOLS to a folder with java/ and signal-cli/ (as Jig's "
                                        "'Download for me' makes it)")
URI = re.compile(r"^sgnl://linkdevice\?uuid=[^&\s]+&pub_key=\S+$")


class Audit:
    def __init__(self):
        self.records: list[tuple] = []

    def record(self, kind, summary, **data):
        self.records.append((kind, summary, data))


def java_processes() -> int:
    if sys.platform != "win32":
        return 0
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq java.exe", "/FO", "CSV", "/NH"], capture_output=True,
                         text=True).stdout
    return out.count('"java.exe"')


@pytest.mark.parametrize("printed, major, version", [
    ('openjdk version "25.0.4.1" 2026-07-21 LTS\nOpenJDK Runtime Environment Temurin-25.0.4.1+1', 25, "25.0.4.1"),
    ('openjdk version "21.0.5" 2024-10-15 LTS', 21, "21.0.5"),
    ('java version "1.8.0_421"\nJava(TM) SE Runtime Environment', 8, "1.8.0_421"),
    ('openjdk version "26-ea" 2026-03-17', 26, "26-ea"),
])
def test_java_versions_are_read_as_java_prints_them(printed, major, version):
    assert ss.parse_java_version(printed) == (major, version)


def test_something_that_isnt_java_has_no_version():
    assert ss.parse_java_version("Python 3.13.5") is None


@pytest.mark.network
async def test_the_latest_signal_cli_and_java_come_with_their_publishers_checksums():
    import httpx

    async with httpx.AsyncClient(follow_redirects=True) as http:
        cli = await ss.signal_cli_release(http)
        java = await ss.java_release(http)
    print(cli, java, sep="\n")
    assert re.fullmatch(r"\d+\.\d+\.\d+", cli["version"]) and cli["name"] == f"signal-cli-{cli['version']}.tar.gz"
    assert cli["url"].startswith("https://github.com/AsamK/signal-cli/releases/download/")
    assert java["url"].startswith("https://") and java["name"].endswith((".zip", ".tar.gz"))
    for item in (cli, java):
        assert re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) and item["size"] > 20 * 2**20


def test_a_folder_without_java_or_signal_cli_says_what_is_missing(tmp_path):
    if asyncio.run(ss.find_java(None))["ok"]:
        pytest.skip("this computer has Java 25 installed, so 'no Java' can't be shown here")
    st = asyncio.run(ss.status(tmp_path))
    assert st["java"] == {"ok": False, "needed": ss.JAVA_NEEDED, "older": []}
    assert st["signal_cli"]["managed"] is False
    assert st["java_page"].startswith("https://adoptium.net/")


@needs_tools
def test_the_windows_start_script_is_read_for_java_to_run_directly():
    binary = ss.managed_signal_cli(TOOLS)
    script = ss.start_script(binary)
    if sys.platform != "win32":
        assert script is None
        return
    classpath, jvm, main = script
    assert main == "org.asamk.signal.Main" and "--enable-native-access=ALL-UNNAMED" in jvm
    assert len(classpath) > 20 and all(Path(p).is_file() for p in classpath)
    assert Path(classpath[0]).name.startswith("signal-cli-")


@needs_tools
async def test_signal_cli_runs_with_the_java_jig_found_however_long_its_path(tmp_path):
    """Gradle's start script puts every jar's full path on one cmd.exe line; from a folder this deep it is
    longer than cmd allows, so Jig starts Java itself."""
    deep = tmp_path / ("very-long-folder-name-" * 4) / "tools"
    deep.mkdir(parents=True)
    if sys.platform == "win32":
        for name in ("java", "signal-cli"):
            subprocess.run(["cmd", "/c", "mklink", "/J", str(deep / name), str(TOOLS / name)], check=True,
                           capture_output=True)
    else:
        for name in ("java", "signal-cli"):
            (deep / name).symlink_to(TOOLS / name)
    binary = ss.managed_signal_cli(deep)
    assert len(str(Path(binary).parent.parent)) * 79 > 8191  # the script alone would be too long for cmd.exe
    path, version = await ss.check_ready(binary, deep)
    assert path == binary and version.startswith("signal-cli 0.")
    command, env = await ss.launch([binary, "--version"], deep)
    assert Path(command[0]).name.lower().startswith("java") and "org.asamk.signal.Main" in command
    assert env["JAVA_HOME"].startswith(str(deep / "java"))


@needs_tools
async def test_linking_shows_signal_clis_own_link_as_a_qr_code_and_stops_cleanly(tmp_path):
    """The real link step up to the code, never scanned: signal-cli's sgnl:// link becomes a QR code, and
    stopping ends signal-cli and its Java. No account is added to signal-cli's data folder."""
    data = tmp_path / "signal-cli-data"
    before = java_processes()
    audit = Audit()
    link = ss.Link(TOOLS, audit, _qr_data_uri)
    start = time.monotonic()
    link.start(ss.managed_signal_cli(TOOLS), extra_args=("--config", str(data)))
    while link.state["status"] == "starting":
        assert time.monotonic() - start < 90, "signal-cli showed no code"
        await asyncio.sleep(0.2)
    state = link.state
    print(f"code after {time.monotonic() - start:.1f}s: {state.get('uri', state)}")
    assert state["status"] == "waiting" and URI.fullmatch(state["uri"]), state
    assert state["qr"].startswith("data:image/svg+xml") and state["codes"] == 1
    await link.close()
    assert link.state == {"status": "cancelled"}
    for _ in range(50):
        if java_processes() <= before:
            break
        await asyncio.sleep(0.1)
    assert java_processes() <= before, "signal-cli's Java was left running"
    written = [p for p in data.rglob("*") if p.is_file()] if data.exists() else []
    # signal-cli makes its (empty) list of accounts, and nothing else.
    assert [p.name for p in written] in ([], ["accounts.json"]), written
    if written:
        assert json.loads(written[0].read_text(encoding="utf-8"))["accounts"] == []
    assert audit.records == []


@needs_tools
async def test_a_code_that_runs_out_is_replaced_by_a_fresh_one(tmp_path):
    """signal-cli drops an unscanned code after about two minutes ("Link request timed out"); Jig starts a new
    one so the person isn't left with a dead code."""
    link = ss.Link(TOOLS, Audit(), _qr_data_uri)
    link.start(ss.managed_signal_cli(TOOLS), extra_args=("--config", str(tmp_path / "data")))
    start = time.monotonic()
    first = None
    try:
        while time.monotonic() - start < 240:
            st = link.state
            assert st["status"] in ("starting", "waiting"), st
            if st["status"] == "waiting":
                first = first or st["uri"]
                if st["uri"] != first:
                    print(f"a fresh code after {time.monotonic() - start:.0f}s (code {st['codes']})")
                    assert st["codes"] == 2 and URI.fullmatch(st["uri"])
                    return
            await asyncio.sleep(1)
        pytest.fail("no fresh code within 4 minutes")
    finally:
        await link.close()


async def test_linking_without_signal_cli_fails_in_plain_words(tmp_path):
    link = ss.Link(tmp_path, Audit(), _qr_data_uri)
    link.start(str(tmp_path / "signal-cli" / "bin" / "signal-cli.bat"))
    while link.state["status"] == "starting":
        await asyncio.sleep(0.05)
    assert link.state["status"] == "failed" and link.state["error"].startswith("signal-cli was not found")


async def test_signal_cli_without_java_25_says_where_to_get_it(tmp_path):
    if (await ss.find_java(None))["ok"]:
        pytest.skip("this computer has Java 25 installed")
    if not os.environ.get("JIG_TEST_SIGNAL_TOOLS") or not ss.managed_signal_cli(TOOLS):
        pytest.skip("set JIG_TEST_SIGNAL_TOOLS")
    alone = tmp_path / "tools"
    alone.mkdir()
    subprocess.run(["cmd", "/c", "mklink", "/J", str(alone / "signal-cli"), str(TOOLS / "signal-cli")], check=True,
                   capture_output=True)
    with pytest.raises(ConnectorError, match=rf"needs Java {ss.JAVA_NEEDED} or newer.*adoptium\.net"):
        await ss.check_ready(ss.managed_signal_cli(alone), alone)


@pytest.mark.network
@pytest.mark.skipif(os.environ.get("JIG_TEST_SIGNAL_DOWNLOAD") != "1",
                    reason="set JIG_TEST_SIGNAL_DOWNLOAD=1 to download signal-cli and Java (about 180 MB)")
async def test_download_for_me_gets_signal_cli_and_java_checked_and_ready(tmp_path):
    tools = tmp_path / "tools"
    audit = Audit()
    downloads = ss.Downloads(tools, audit)
    downloads.start()
    seen = set()
    while downloads.state["status"] == "running":
        seen.add(downloads.state["step"])
        await asyncio.sleep(0.5)
    print(downloads.state, sorted(seen), sep="\n")
    assert downloads.state["status"] == "done", downloads.state
    assert downloads.state["version"].startswith("signal-cli ") and ss.tools_of(downloads.state["signal_cli"]) == tools
    assert not list((tools / ".download").glob("*")), "a downloaded archive was left behind"
    kinds = [r[2]["what"] for r in audit.records]
    assert "signal-cli" in kinds and all(re.fullmatch(r"[0-9a-f]{64}", r[2]["sha256"]) for r in audit.records)
    if "Java" in kinds:
        assert downloads.state["java"]["managed"] is True
