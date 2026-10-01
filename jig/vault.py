"""Credential vault. Tools use secrets by reference; values never reach the model.

Tool arguments may contain ``{{secret:NAME}}``. The executor resolves the
reference only at the moment the tool function is called (after the policy
gate and Sentinel have reviewed the *reference*), and any secret value that
appears in a tool result is redacted before the result goes back to the model.

Backends (``[vault] backend``): ``auto`` picks Windows DPAPI (ciphertext stored
in SQLite, bound to the current Windows user) or the ``keyring`` library
elsewhere; ``dpapi`` and ``keyring`` force one of them; ``keyfile`` encrypts
with a key supplied as a file (``jig.vault_backends.keyfile``, for containers).
If the selected backend is unavailable, the vault refuses to start.
"""

from __future__ import annotations

import ctypes
import json
import re
import sys
from typing import Any

from .config import VaultConfig
from .db import Database, dumps, now_iso
from .errors import SecretNotFound, VaultUnavailable

SECRET_REF = re.compile(r"\{\{secret:([A-Za-z0-9_.-]{1,64})\}\}")
_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_KEYRING_SERVICE = "jig-vault"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


class _DPAPI:
    name = "dpapi"
    _UI_FORBIDDEN = 0x1
    _ENTROPY = b"jig-vault-v1"

    def __init__(self) -> None:
        self._crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
        self._kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

    @staticmethod
    def _blob(data: bytes) -> tuple[_DataBlob, Any]:
        buf = ctypes.create_string_buffer(data, len(data))
        return _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    def _call(self, fn: Any, data: bytes) -> bytes:
        inp, _keep = self._blob(data)
        ent, _keep2 = self._blob(self._ENTROPY)
        out = _DataBlob()
        ok = fn(ctypes.byref(inp), None, ctypes.byref(ent), None, None, self._UI_FORBIDDEN, ctypes.byref(out))
        if not ok:
            raise VaultUnavailable(f"DPAPI call failed (Windows error {ctypes.GetLastError()})")  # type: ignore[attr-defined]
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            self._kernel32.LocalFree(out.pbData)

    def encrypt(self, plaintext: bytes) -> bytes:
        return self._call(self._crypt32.CryptProtectData, plaintext)

    def decrypt(self, ciphertext: bytes) -> bytes:
        return self._call(self._crypt32.CryptUnprotectData, ciphertext)


class Vault:
    def __init__(self, db: Database, config: VaultConfig | None = None):
        self.db = db
        self._dpapi: _DPAPI | None = None
        self._keyring: Any = None
        self._keyfile: Any = None
        choice = (config or VaultConfig()).backend
        if choice == "keyfile":
            from .vault_backends.keyfile import KeyFileCipher

            self._keyfile = KeyFileCipher(db, config.key_file)
            self.backend = "keyfile"
        elif choice == "dpapi" and sys.platform != "win32":
            raise VaultUnavailable("[vault] backend = 'dpapi' needs Windows")
        elif choice == "dpapi" or (choice == "auto" and sys.platform == "win32"):
            self._dpapi = _DPAPI()
            probe = b"jig-probe"
            if self._dpapi.decrypt(self._dpapi.encrypt(probe)) != probe:
                raise VaultUnavailable("DPAPI round-trip check failed")
            self.backend = "dpapi"
        else:
            try:
                import keyring
                from keyring.backends.fail import Keyring as FailKeyring
            except ImportError as exc:
                raise VaultUnavailable("Neither DPAPI nor the 'keyring' library is available") from exc
            if isinstance(keyring.get_keyring(), FailKeyring):
                raise VaultUnavailable("'keyring' has no usable backend on this system; in a container, set "
                                       "[vault] backend = \"keyfile\" (see docs/container.md)")
            self._keyring = keyring
            self.backend = "keyring"

    def set(self, name: str, value: str, *, allowed_tools: list[str] | None = None) -> dict[str, Any]:
        if not _NAME.match(name):
            raise ValueError("secret names may only contain letters, digits, '.', '_' and '-'")
        if not value:
            raise ValueError("secret value must not be empty")
        ts = now_iso()
        cipher: bytes | None = None
        if self._keyfile:
            cipher = self._keyfile.encrypt(name, value.encode("utf-8"))
        elif self._dpapi:
            cipher = self._dpapi.encrypt(value.encode("utf-8"))
        else:
            self._keyring.set_password(_KEYRING_SERVICE, name, value)
        self.db.execute(
            "INSERT INTO secrets(name, backend, ciphertext, allowed_tools_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET backend=excluded.backend, "
            "ciphertext=excluded.ciphertext, allowed_tools_json=excluded.allowed_tools_json, "
            "updated_at=excluded.updated_at",
            (name, self.backend, cipher, dumps(allowed_tools or []), ts, ts),
        )
        return self.describe(name)

    def describe(self, name: str) -> dict[str, Any]:
        row = self.db.one(
            "SELECT name, backend, allowed_tools_json, created_at, updated_at FROM secrets WHERE name = ?",
            (name,),
        )
        if row is None:
            raise SecretNotFound(f"secret {name!r} does not exist")
        row["allowed_tools"] = json.loads(row.pop("allowed_tools_json"))
        return row

    def list(self) -> list[dict[str, Any]]:
        return [self.describe(r["name"]) for r in self.db.query("SELECT name FROM secrets ORDER BY name")]

    def delete(self, name: str) -> None:
        row = self.db.one("SELECT backend FROM secrets WHERE name = ?", (name,))
        if row is None:
            raise SecretNotFound(f"secret {name!r} does not exist")
        if row["backend"] == "keyring":
            self._keyring.delete_password(_KEYRING_SERVICE, name)
        self.db.execute("DELETE FROM secrets WHERE name = ?", (name,))

    def reveal(self, name: str) -> str:
        """Only for the tool executor. Never expose through the API or to the model."""
        row = self.db.one("SELECT backend, ciphertext FROM secrets WHERE name = ?", (name,))
        if row is None:
            raise SecretNotFound(f"secret {name!r} does not exist")
        if row["backend"] != self.backend:
            raise VaultUnavailable(f"secret {name!r} was stored with the {row['backend']} backend, but this vault "
                                   f"uses {self.backend}; set it again with the current backend")
        if row["backend"] == "keyfile":
            return self._keyfile.decrypt(name, row["ciphertext"]).decode("utf-8")
        if row["backend"] == "dpapi":
            if not self._dpapi:
                raise VaultUnavailable("secret was stored with DPAPI, which is unavailable here")
            return self._dpapi.decrypt(row["ciphertext"]).decode("utf-8")
        value = self._keyring.get_password(_KEYRING_SERVICE, name)
        if value is None:
            raise SecretNotFound(f"secret {name!r} is missing from the keyring")
        return value

    @staticmethod
    def references(value: Any) -> set[str]:
        """All secret names referenced anywhere inside a JSON-like value."""
        found: set[str] = set()
        if isinstance(value, str):
            found.update(SECRET_REF.findall(value))
        elif isinstance(value, dict):
            for v in value.values():
                found |= Vault.references(v)
        elif isinstance(value, list):
            for v in value:
                found |= Vault.references(v)
        return found

    def resolve(self, value: Any, used: dict[str, str]) -> Any:
        """Replace references with real values, recording them in ``used`` for redaction."""
        if isinstance(value, str):
            def sub(m: re.Match[str]) -> str:
                secret = self.reveal(m.group(1))
                used[m.group(1)] = secret
                return secret
            return SECRET_REF.sub(sub, value)
        if isinstance(value, dict):
            return {k: self.resolve(v, used) for k, v in value.items()}
        if isinstance(value, list):
            return [self.resolve(v, used) for v in value]
        return value

    @staticmethod
    def redact(value: Any, used: dict[str, str]) -> Any:
        if not used:
            return value
        if isinstance(value, str):
            for name, secret in used.items():
                value = value.replace(secret, f"[secret:{name}]")
            return value
        if isinstance(value, dict):
            return {k: Vault.redact(v, used) for k, v in value.items()}
        if isinstance(value, list):
            return [Vault.redact(v, used) for v in value]
        return value
