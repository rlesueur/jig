"""An MCP session over stdio: newline-delimited JSON-RPC 2.0, the protocol's current framing.

The process is the one the person named in Settings. Jig does not run it through a shell. Messages are
not logged: a request can carry a tool argument, and the server's replies can carry whatever it read.
Stderr is drained and discarded for the same reason. A secret is only placed in the process environment,
from the vault, and is added to the gate's redaction set by the caller.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from ..errors import JigError
from ..vault import Vault
from .protocol import CLIENT_PROTOCOL, PROTOCOL_VERSIONS

_START_TIMEOUT_S = 20.0
_CALL_TIMEOUT_S = 60.0


class McpError(JigError):
    def __init__(self, message: str, *, start_failed: bool = False):
        super().__init__(message)
        self.start_failed = start_failed


class StdioSession:
    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._stderr: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._next_id = 0
        self._lock = asyncio.Lock()
        self._write = asyncio.Lock()
        self._redactions: dict[str, str] = {}

    @classmethod
    async def start(cls, command: str, args: list[str], *, env: dict[str, str],
                    redactions: dict[str, str]) -> StdioSession:
        session = cls()
        session._redactions = redactions
        merged = os.environ.copy()
        merged.update(env)
        try:
            session._proc = await asyncio.create_subprocess_exec(
                command, *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, env=merged)
        except OSError as exc:
            raise McpError(session._safe(f"could not start the MCP server ({type(exc).__name__})"),
                           start_failed=True) from None
        assert session._proc.stdout is not None and session._proc.stderr is not None
        session._reader = asyncio.create_task(session._read_stdout())
        session._stderr = asyncio.create_task(session._drain_stderr())
        try:
            result = await session.request("initialize", {
                "protocolVersion": CLIENT_PROTOCOL,
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "Jig", "version": "0"},
            }, timeout=_START_TIMEOUT_S)
        except McpError:
            await session.close()
            raise
        version = result.get("protocolVersion") if isinstance(result, dict) else None
        if version not in PROTOCOL_VERSIONS:
            await session.close()
            shown = version if isinstance(version, str) and len(version) <= 40 else "an unrecognised one"
            raise McpError(f"the MCP server speaks protocol {shown}, which Jig does not")
        await session.notify("notifications/initialized")
        return session

    async def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            params: dict[str, Any] = {"cursor": cursor} if cursor else {}
            result = await self.request("tools/list", params, timeout=_START_TIMEOUT_S)
            if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
                raise McpError("the MCP server's tool list was not a list of tools")
            for tool in result["tools"]:
                if isinstance(tool, dict) and isinstance(tool.get("name"), str) and tool["name"] not in seen:
                    seen.add(tool["name"])
                    tools.append(tool)
            nxt = result.get("nextCursor")
            if not isinstance(nxt, str) or not nxt or nxt == cursor:
                return tools
            cursor = nxt

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self.request("tools/call", {"name": name, "arguments": arguments}, timeout=_CALL_TIMEOUT_S)
        if not isinstance(result, dict):
            raise McpError("the MCP server returned something that is not a tool result")
        text, other = _content(result.get("content"))
        if len(text) > 12_000:
            shown = text[:12_000]
            note = f"Showing the first 12,000 characters of {len(text)}."
        else:
            shown, note = text, ""
        out: dict[str, Any] = {"text": shown, "is_error": bool(result.get("isError"))}
        if note:
            out["note"] = note
            out["total_chars"] = len(text)
        if other:
            out["other_content"] = other
        if out["is_error"]:
            detail = shown.strip() or "the tool reported an error and sent no text"
            raise McpError(self._safe(detail[:500]))
        return out

    async def request(self, method: str, params: dict[str, Any], *, timeout: float) -> Any:
        async with self._lock:
            call_id = self._next()
            future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
            self._pending[call_id] = future
            try:
                await self._send({"jsonrpc": "2.0", "id": call_id, "method": method, "params": params})
            except Exception:
                self._pending.pop(call_id, None)
                raise
            try:
                return await asyncio.wait_for(future, timeout)
            except TimeoutError:
                self._pending.pop(call_id, None)
                raise McpError(f"the MCP server did not answer {method}") from None

    async def notify(self, method: str) -> None:
        async with self._lock:
            await self._send({"jsonrpc": "2.0", "method": method})

    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def close(self) -> None:
        proc = self._proc
        self._proc = None
        for future in self._pending.values():
            if not future.done():
                future.set_exception(McpError("the MCP server was closed"))
        self._pending.clear()
        if proc is not None and proc.stdin is not None and not proc.stdin.is_closing():
            proc.stdin.close()
        if proc is not None and proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), 2)
            except TimeoutError:
                proc.kill()
                await proc.wait()
        for task in (self._reader, self._stderr):
            if task is not None:
                task.cancel()
        self._reader = self._stderr = None

    def _next(self) -> int:
        self._next_id += 1
        return self._next_id

    async def _send(self, message: dict[str, Any]) -> None:
        async with self._write:
            proc = self._proc
            if proc is None or proc.stdin is None or proc.returncode is not None:
                raise McpError("the MCP server is not running")
            proc.stdin.write(json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n")
            await proc.stdin.drain()

    async def _read_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            while True:
                raw = await self._proc.stdout.readline()
                if not raw:
                    self._fail_all("the MCP server closed its output")
                    return
                line = raw.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    self._fail_all("the MCP server sent a line that is not JSON")
                    return
                if not isinstance(message, dict):
                    continue
                if "method" in message and "id" in message:
                    await self._reject_server_request(message["id"])
                    continue
                call_id = message.get("id")
                future = self._pending.get(call_id) if isinstance(call_id, int) else None
                if future is None or future.done():
                    continue
                self._pending.pop(call_id, None)
                if "error" in message:
                    future.set_exception(McpError(self._error_text(message["error"])))
                else:
                    future.set_result(message.get("result"))
        except asyncio.CancelledError:
            raise
        except Exception:
            self._fail_all("the MCP server's output could not be read")

    async def _drain_stderr(self) -> None:
        """Keep the pipe from filling. The bytes are not logged and not kept."""
        assert self._proc is not None and self._proc.stderr is not None
        try:
            while await self._proc.stderr.read(4096):
                pass
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _reject_server_request(self, call_id: Any) -> None:
        """Jig does not let an MCP server ask it to do something (no sampling, no roots)."""
        try:
            await self._send({"jsonrpc": "2.0", "id": call_id, "error": {
                "code": -32601, "message": "Jig does not accept requests from an MCP server"}})
        except McpError:
            return

    def _fail_all(self, message: str) -> None:
        error = McpError(message)
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    def _error_text(self, error: Any) -> str:
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return self._safe(error["message"][:300]) or "the MCP server returned an error"
        return "the MCP server returned an error"

    def _safe(self, text: str) -> str:
        return str(Vault.redact(text, self._redactions))


def _content(content: Any) -> tuple[str, list[str]]:
    texts: list[str] = []
    other: list[str] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                texts.append(block["text"])
            else:
                kind = block.get("type")
                other.append(kind if isinstance(kind, str) else "unknown")
    return "\n".join(texts), other
