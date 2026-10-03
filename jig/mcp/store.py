"""MCP servers in the database, and their secrets in the vault.

The program and its arguments are stored here because the person typed them as the way to start the
server. A token is never stored here: it is ``mcp.<id>.env.<NAME>`` in the vault, with no tool allowed
to name it. Nothing in this module writes a secret value to the audit log.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..audit import AuditLog
from ..db import Database, now_iso
from ..errors import JigError, NotFound, SecretNotFound
from ..vault import Vault

SECRET_PREFIX = "mcp."
_LABEL = re.compile(r"^.{1,60}$", re.DOTALL)
_SLUG = re.compile(r"[^a-z0-9]+")
_ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_ACCESS = {"read": "read only", "act": "read and act"}


class McpStore:
    def __init__(self, db: Database, vault: Vault, audit: AuditLog):
        self.db = db
        self.vault = vault
        self.audit = audit

    def list(self) -> list[dict[str, Any]]:
        return [self._row(r) for r in self.db.query("SELECT * FROM mcp_servers ORDER BY label")]

    def get(self, server_id: str) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM mcp_servers WHERE id = ?", (server_id,))
        if row is None:
            raise NotFound(f"there's no MCP server called {server_id!r}")
        return self._row(row)

    def add(self, *, label: str, command: str, args: list[str], access: str, via: str) -> dict[str, Any]:
        label = _label(label)
        command = _command(command)
        args = _args(args)
        if access not in _ACCESS:
            raise JigError("what Jig may do must be read or act")
        server_id = self._fresh_id(label)
        ts = now_iso()
        self.db.execute(
            "INSERT INTO mcp_servers(id, label, command, args_json, access, enabled, last_error, tool_count, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, NULL, 0, ?, ?)",
            (server_id, label, command, json.dumps(args), access, ts, ts))
        self.audit.record("mcp.added", f"MCP server {label} added ({_ACCESS[access]})", actor="user",
                          server=server_id, access=access, via=via)
        return self.get(server_id)

    def set_error(self, server_id: str, message: str) -> None:
        self.db.execute("UPDATE mcp_servers SET last_error = ?, tool_count = 0, updated_at = ? WHERE id = ?",
                        (message[:300], now_iso(), server_id))
        self.audit.record("mcp.error", f"MCP server {server_id} could not be used", actor="runtime",
                          server=server_id, error_chars=len(message))

    def set_ready(self, server_id: str, tool_names: list[str]) -> None:
        self.db.execute("UPDATE mcp_servers SET last_error = NULL, tool_count = ?, updated_at = ? WHERE id = ?",
                        (len(tool_names), now_iso(), server_id))
        self.audit.record("mcp.tools", f"MCP server {server_id} listed {len(tool_names)} tools", actor="runtime",
                          server=server_id, tools=len(tool_names), tool_names=tool_names)

    def remove(self, server_id: str, *, via: str) -> None:
        row = self.get(server_id)
        for name in self.env_names(server_id):
            self._delete_env(server_id, name)
        self.db.execute("DELETE FROM mcp_servers WHERE id = ?", (server_id,))
        self.audit.record("mcp.removed", f"MCP server {row['label']} removed", actor="user",
                          server=server_id, via=via)

    def env_names(self, server_id: str) -> list[str]:
        prefix = self._env_prefix(server_id)
        return sorted(item["name"][len(prefix):] for item in self.vault.list() if item["name"].startswith(prefix))

    def env_values(self, server_id: str) -> dict[str, str]:
        """For the process environment only. Callers must not log or return this."""
        prefix = self._env_prefix(server_id)
        return {item["name"][len(prefix):]: self.vault.reveal(item["name"])
                for item in self.vault.list() if item["name"].startswith(prefix)}

    def set_env(self, server_id: str, name: str, value: str, *, via: str) -> None:
        self.get(server_id)
        if not isinstance(name, str) or not _ENV.fullmatch(name):
            raise JigError("an environment variable's name must be letters, digits and underscores, "
                           "and not start with a digit")
        if not isinstance(value, str) or not value or "\x00" in value:
            raise JigError("the secret must be text, and it must not be empty")
        self.vault.set(self._env_prefix(server_id) + name, value, allowed_tools=[])
        self.audit.record("mcp.env_set", f"MCP server {server_id}: environment variable {name} stored in the vault",
                          actor="user", server=server_id, env=name, via=via)

    def remove_env(self, server_id: str, name: str, *, via: str) -> bool:
        self.get(server_id)
        if not isinstance(name, str) or not _ENV.fullmatch(name):
            raise JigError(f"there's no environment variable {name!r} on that server")
        deleted = self._delete_env(server_id, name)
        if deleted:
            self.audit.record("mcp.env_removed", f"MCP server {server_id}: environment variable {name} removed",
                              actor="user", server=server_id, env=name, via=via)
        return deleted

    def public(self, row: dict[str, Any]) -> dict[str, Any]:
        """What Settings and the API may show. No secret values."""
        return {"id": row["id"], "label": row["label"], "command": row["command"], "args": row["args"],
                "access": row["access"], "enabled": row["enabled"], "last_error": row["last_error"],
                "tool_count": row["tool_count"], "env": self.env_names(row["id"])}

    def _delete_env(self, server_id: str, name: str) -> bool:
        try:
            self.vault.delete(self._env_prefix(server_id) + name)
        except SecretNotFound:
            return False
        return True

    def _fresh_id(self, label: str) -> str:
        base = _SLUG.sub("-", label.lower()).strip("-")[:32].strip("-") or "server"
        server_id, n = base, 2
        while self.db.one("SELECT 1 AS x FROM mcp_servers WHERE id = ?", (server_id,)):
            suffix = f"-{n}"
            server_id = base[:32 - len(suffix)] + suffix
            n += 1
        return server_id

    @staticmethod
    def _env_prefix(server_id: str) -> str:
        return f"{SECRET_PREFIX}{server_id}.env."

    @staticmethod
    def _row(row: dict[str, Any]) -> dict[str, Any]:
        return {**row, "args": json.loads(row["args_json"]), "enabled": bool(row["enabled"])}


def _label(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or not _LABEL.fullmatch(value.strip()):
        raise JigError("give the server a name, up to 60 characters")
    return " ".join(value.split())


def _command(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 500 or "\x00" in value or "\n" in value:
        raise JigError("say which program to run, as one line")
    return value.strip()


def _args(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 40:
        raise JigError("arguments must be a list of up to 40 lines")
    out = []
    for item in value:
        if not isinstance(item, str) or not item or len(item) > 500 or "\x00" in item or "\n" in item:
            raise JigError("each argument must be one line of text")
        out.append(item)
    return out
