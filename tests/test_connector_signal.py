"""Signal, tested without a phone or a linked signal-cli: the real gate and Sentinel, real processes for
the program checks and the runner, and the real 'not found' when signal-cli isn't installed. The tests that
need signal-cli itself skip, saying so, until it is installed. The live tests are in
test_connector_signal_live.py."""

from __future__ import annotations

import json
import shutil
import sys

import pytest

from jig.config import ConnectorLimits
from jig.connectors import PROVIDERS, connect, signal
from jig.connectors.base import Connectors, Grant, grant_secret
from jig.constants import Mode
from jig.errors import ConnectorError, ToolArgumentError
from jig.model import ToolCall
from jig.policy.core import _CREDENTIAL_NAME, evaluate_core
from jig.policy.gate import CallContext
from jig.tools.builtin import http_client

from .conftest import audit_kinds
from .test_connectors import store  # noqa: F401  (fixture)

TOOLS = {"signal_send_message", "signal_receive"}
OWN = "+447700900123"
OTHER = "+447700900456"
BAT = r"C:\signal-cli\bin\signal-cli.bat"
INSTALLED = shutil.which("signal-cli")
needs_signal_cli = pytest.mark.skipif(not INSTALLED, reason="signal-cli is not installed on this computer (not on "
                                      "PATH); install it to run this test (docs/connectors-setup.md)")
LIMITS = type("C", (), {"connectors": {"signal": ConnectorLimits(allowed_targets=[OWN],
                                                                 required_prefix="[Jig test]")}})()


def _grant(scopes=(signal.S_SEND,)) -> Grant:
    return Grant(access_token="", scopes=list(scopes), extra={"number": OWN, "signal_cli": BAT, "version": "x"})


def test_tools_are_declared_safely(jig):
    specs = {t.name: t for t in jig.registry.all() if t.name.startswith("signal_")}
    assert set(specs) == TOOLS
    assert all(s.category.value == "messages" and not _CREDENTIAL_NAME.search(n) for n, s in specs.items())
    send, receive = specs["signal_send_message"], specs["signal_receive"]
    assert send.effect.value == "side_effect" and send.outbound and send.human_only
    assert send.resolve is signal.resolve_recipient and send.precheck is signal.limits_problem
    assert receive.effect.value == "private_write" and not receive.outbound
    spec = PROVIDERS["signal"]
    assert spec.kind == "token" and not spec.needs_client and spec.api_hosts == frozenset()
    assert [(i.name, i.secret) for i in spec.inputs] == [("number", False), ("signal_cli", False)]


def test_tools_are_offered_only_with_the_matching_access(jig):
    def offered(mode: Mode) -> set[str]:
        return {s["function"]["name"] for s in jig.registry.schemas_for_mode(mode)} & TOOLS

    assert offered(Mode.ACTION) == set()
    jig.connections.save(signal.NAME, _grant(), account=OWN, access="send", via="test")
    assert offered(Mode.ACTION) == {"signal_send_message"} and offered(Mode.RESEARCH) == set()
    jig.connections.save(signal.NAME, _grant((signal.S_SEND, signal.S_RECEIVE)), account=OWN, access="receive",
                         via="test")
    assert offered(Mode.ACTION) == TOOLS and offered(Mode.RESEARCH) == {"signal_receive"}


# The program --------------------------------------------------------------------------------------------------
@pytest.mark.skipif(bool(INSTALLED), reason="signal-cli is installed here, so it can't be 'not found'")
def test_signal_cli_not_installed_is_a_clear_error():
    with pytest.raises(ConnectorError, match="signal-cli was not found: 'signal-cli' is not a program"):
        signal.find_binary("signal-cli")


def test_a_missing_path_is_a_clear_error(tmp_path):
    with pytest.raises(ConnectorError, match="signal-cli was not found"):
        signal.find_binary(str(tmp_path / "signal-cli.bat"))


async def test_a_program_that_is_not_signal_cli_is_refused():
    """A real program (this Python) answers '--version' with something else."""
    with pytest.raises(ConnectorError, match="is not signal-cli: '--version' printed 'Python"):
        await signal.check_binary(sys.executable)


async def test_connecting_without_signal_cli_stores_nothing(store, tmp_path):  # noqa: F811
    async with http_client() as http:
        with pytest.raises(ConnectorError, match="signal-cli was not found.*nothing was connected"):
            await connect(signal.NAME, access=None, store=store, http=http, via="test",
                          values={"number": OWN, "signal_cli": str(tmp_path / "signal-cli.bat")})
        with pytest.raises(ConnectorError, match="international format.*nothing was connected"):
            await connect(signal.NAME, access=None, store=store, http=http, via="test",
                          values={"number": "07700900123", "signal_cli": sys.executable})
    assert store.get(signal.NAME) is None
    assert all(s["name"] != grant_secret(signal.NAME) for s in store.vault.list())


@needs_signal_cli
async def test_installed_signal_cli_reports_its_version():
    binary, version = await signal.check_binary(INSTALLED)
    assert version.lower().startswith("signal-cli") and binary.lower().endswith(("signal-cli", ".bat", ".exe"))


@needs_signal_cli
async def test_installed_signal_cli_refuses_a_number_it_has_no_account_for():
    binary, _ = await signal.check_binary(INSTALLED)
    with pytest.raises(ConnectorError, match="has no account for \\+447700900999"):
        await signal.check_account(binary, "+447700900999")


# The runner, with real processes -------------------------------------------------------------------------
async def test_the_runner_reports_the_exit_code_and_stderr():
    with pytest.raises(ConnectorError, match=r"exit code 3\): User error: boom"):
        await signal.run([sys.executable, "-c", "import sys; sys.stderr.write('User error: boom'); sys.exit(3)"])


async def test_the_runner_passes_text_on_stdin_unchanged():
    out = await signal.run([sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"],
                           stdin="[Jig test] café & \"quotes\" | ^ 👍".encode("utf-8"))
    assert out == "[Jig test] café & \"quotes\" | ^ 👍"


async def test_the_runner_stops_a_hung_process():
    with pytest.raises(ConnectorError, match="did not finish within 1s and was stopped"):
        await signal.run([sys.executable, "-c", "import time; time.sleep(60)"], timeout=1)


# Command lines -------------------------------------------------------------------------------------------
def test_send_keeps_the_message_off_the_command_line():
    assert signal.send_argv(BAT, OWN, OTHER) == [BAT, "-a", OWN, "-o", "json", "send", "--message-from-stdin", OTHER]


def test_receive_downloads_nothing_and_sends_no_read_receipts():
    cmd = signal.receive_argv(BAT, OWN, 5, 20)
    assert cmd[:6] == [BAT, "-a", OWN, "-o", "json", "receive"]
    assert cmd[6:10] == ["--timeout", "5", "--max-messages", "20"]
    assert {"--ignore-attachments", "--ignore-stories", "--ignore-avatars", "--ignore-stickers"} <= set(cmd)
    assert "--send-read-receipts" not in cmd


@pytest.mark.parametrize("bad", ["a&b", "x|y", "\"quoted\"", "50%", "a b", "^c", "<in", "!v!"])
def test_a_bat_wrapper_gets_no_characters_cmd_would_read_as_commands(bad):
    with pytest.raises(ConnectorError, match="cmd.exe"):
        signal.argv(BAT, "send", bad)
    assert signal.argv("/usr/local/bin/signal-cli", "send", bad)[-1] == bad


@pytest.mark.parametrize("good", ["+447700900123", "+12025550123", "+4915112345678"])
def test_numbers_must_be_e164(good):
    assert signal.e164(good) == good


@pytest.mark.parametrize("bad", ["07700900123", "447700900123", "+44 7700 900123", "+0447700900", "+44770",
                                 "+4477009001234567", "+44&calc", "", None, 447700900123])
def test_numbers_that_are_not_e164_are_refused(bad):
    with pytest.raises(ToolArgumentError):
        signal.e164(bad)
    with pytest.raises(ToolArgumentError):
        signal.send_argv(BAT, OWN, bad)


@pytest.mark.parametrize("bad", ["", "  ", "x" * (signal.MAX_TEXT + 1), None])
def test_message_text_is_checked(bad):
    with pytest.raises(ToolArgumentError):
        signal.check_text(bad)


def test_received_messages_are_text_only_and_size_limited():
    lines = "\n".join(json.dumps(x) for x in [
        {"envelope": {"sourceNumber": OTHER, "sourceName": "Alex", "timestamp": 1_790_000_000_000,
                      "dataMessage": {"message": "hello there", "timestamp": 1_790_000_000_000}}},
        {"envelope": {"sourceNumber": OTHER, "timestamp": 1_790_000_000_001, "receiptMessage": {"isDelivery": True}}},
        {"envelope": {"sourceNumber": OTHER, "timestamp": 1_790_000_000_002,
                      "dataMessage": {"message": None, "attachments": [{"contentType": "image/jpeg"}]}}},
        {"envelope": {"sourceNumber": OWN, "timestamp": 1_790_000_000_003,
                      "syncMessage": {"sentMessage": {"destinationNumber": OWN, "message": "note"}}}},
    ])
    messages, other = signal.received(lines, limit=5)
    assert other == 1 and len(messages) == 3
    assert messages[0]["text"] == "hello" and messages[0]["truncated"] and messages[0]["name"] == "Alex"
    assert messages[1]["text"] is None and "text only" in messages[1]["note"]
    assert messages[2]["from"] == "you, from another device" and messages[2]["to"] == OWN
    with pytest.raises(ConnectorError, match="isn't JSON"):
        signal.received("Envelope from: +44...")


# Limits, the gate and disconnecting ----------------------------------------------------------------------
@pytest.mark.parametrize("args, why", [
    ({"recipient": OTHER, "text": "[Jig test] x"}, "allowed_targets"),
    ({"recipient": OWN, "text": "hello"}, "required_prefix"),
])
def test_limits_keep_sends_to_your_own_number_and_test_messages(args, why):
    assert why in signal.limits_problem(LIMITS, args, None)


def test_limits_allow_a_test_message_to_yourself():
    assert signal.limits_problem(LIMITS, {"recipient": OWN, "text": "[Jig test] x"}, None) is None


async def test_read_only_mode_refuses_sending(jig):
    call = ToolCall(id="s1", name="signal_send_message", arguments_raw=json.dumps({"recipient": OWN, "text": "hi"}))
    outcome = await jig.executor.execute(call, CallContext(run_id="r_s1", task_id=None, mode=Mode.RESEARCH,
                                                           intent="look only"))
    assert outcome.error_type == "ModeViolation"
    assert "sentinel.verdict" not in audit_kinds(jig, run_id="r_s1")


async def test_a_send_without_a_connection_fails_clearly_before_review(jig):
    call = ToolCall(id="s2", name="signal_send_message", arguments_raw=json.dumps(
        {"recipient": OWN, "text": "[Jig test] hello"}))
    outcome = await jig.executor.execute(call, CallContext(run_id="r_s2", task_id=None, mode=Mode.ACTION,
                                                           intent="send a note to self"))
    assert outcome.error_type == "ToolError" and "not connected" in outcome.error
    assert "jig connect signal" in outcome.error
    kinds = audit_kinds(jig, run_id="r_s2")
    assert "sentinel.verdict" not in kinds and "approval.requested" not in kinds


async def test_receiving_without_a_connection_fails_clearly(jig):
    call = ToolCall(id="s3", name="signal_receive", arguments_raw="{}")
    outcome = await jig.executor.execute(call, CallContext(run_id="r_s3", task_id=None, mode=Mode.RESEARCH,
                                                           intent="check messages"))
    assert outcome.error_type == "ConnectorNotConnected" and "jig connect signal" in outcome.error


async def test_an_allowed_send_still_needs_a_human(jig):
    jig.rules.create(tool="signal_*", decision="allow", note="test: try to make sending automatic")
    findings = await evaluate_core(jig.registry.get("signal_send_message"), {"recipient": OWN, "text": "x"}, jig.vault)
    assert any(f.rule_id == "human-only-actions" and f.decision.value == "ask" for f in findings)


async def test_the_connection_holds_no_secret_and_disconnect_says_how_to_unlink(store):  # noqa: F811
    store.save(signal.NAME, _grant(), account=OWN, access="send", via="test")
    assert store.grant(signal.NAME).values() == [] and store.grant(signal.NAME).extra["number"] == OWN
    async with http_client() as http:
        result = await Connectors(store, http, {}).disconnect(signal.NAME, via="test")
    assert result["removed"] and "Settings > Linked devices" in result["at_provider"]
    assert store.get(signal.NAME) is None
