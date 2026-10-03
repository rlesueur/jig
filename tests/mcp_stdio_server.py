"""A small MCP server over stdio, speaking newline-delimited JSON-RPC 2.0.

Used by tests/test_mcp.py. It is a real server: Jig starts it as a process and talks the protocol.
Stdout is only JSON lines. A secret in the environment is never written out, only whether it is set.
"""

from __future__ import annotations

import json
import os
import sys

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

TOOLS = [
    {
        "name": "add",
        "description": "Add two integers.",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
            "additionalProperties": False,
        },
        "_meta": {"jig": {"effect": "read", "outbound": False, "category": "files"}},
    },
    {
        "name": "stamp",
        "description": "A declared side effect that stays on this computer.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "_meta": {"jig": {"effect": "side_effect", "outbound": False, "category": "files"}},
    },
    {
        "name": "shout",
        "description": "Says the text back. It does not declare an effect.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "label",
        "description": "Returns how long a label is. It does not return the label.",
        "inputSchema": {
            "type": "object",
            "properties": {"label": {"type": "string"}},
            "required": ["label"],
            "additionalProperties": False,
        },
        "_meta": {"jig": {"effect": "read", "outbound": False, "category": "files"}},
    },
    {
        "name": "env_flag",
        "description": "Reports whether JIG_MCP_SECRET is set. It never returns the value.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "_meta": {"jig": {"effect": "read", "outbound": False, "category": "files"}},
    },
]


def _calls_path() -> str | None:
    args = sys.argv[1:]
    if "--calls" not in args:
        return None
    index = args.index("--calls")
    if index + 1 >= len(args):
        return None
    return args[index + 1]


CALLS = _calls_path()


def _send(message: dict) -> None:
    sys.stdout.buffer.write(json.dumps(message).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def _reply(msg_id: object, result: object) -> None:
    _send({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _error(msg_id: object, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})


def _note(name: str) -> None:
    if not CALLS:
        return
    with open(CALLS, "a", encoding="utf-8") as handle:
        handle.write(name + "\n")


def _call(params: dict) -> str:
    name = params.get("name")
    arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
    if not isinstance(name, str):
        raise ValueError("missing tool name")
    _note(name)
    if name == "add":
        return str(int(arguments["a"]) + int(arguments["b"]))
    if name == "stamp":
        return "stamped"
    if name == "shout":
        return str(arguments.get("text", ""))
    if name == "label":
        return str(len(str(arguments.get("label", ""))))
    if name == "env_flag":
        return "set" if os.environ.get("JIG_MCP_SECRET") else "unset"
    raise ValueError("unknown tool")


def _handle(message: dict) -> None:
    method = message.get("method")
    msg_id = message.get("id")
    if method and "id" not in message:
        return
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    if method == "initialize":
        version = params.get("protocolVersion")
        if version not in PROTOCOL_VERSIONS:
            version = PROTOCOL_VERSIONS[-1]
        _reply(msg_id, {"protocolVersion": version, "capabilities": {"tools": {}},
                        "serverInfo": {"name": "jig-mcp-test", "version": "0"}})
    elif method == "ping":
        _reply(msg_id, {})
    elif method == "tools/list":
        _reply(msg_id, {"tools": TOOLS})
    elif method == "tools/call":
        try:
            text = _call(params)
        except (ValueError, KeyError, TypeError):
            _error(msg_id, -32602, "the tool could not run")
            return
        _reply(msg_id, {"content": [{"type": "text", "text": text}], "isError": False})
    else:
        _error(msg_id, -32601, "method not found")


def main() -> None:
    while True:
        raw = sys.stdin.buffer.readline()
        if not raw:
            return
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(message, dict):
            _handle(message)


if __name__ == "__main__":
    main()
