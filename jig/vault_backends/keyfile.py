"""``[vault] backend = "keyfile"``: secrets encrypted with a key that you supply as a file.

Meant for containers, where neither Windows DPAPI nor an OS keyring exists. The key arrives as a Docker
secret (``/run/secrets/jig_vault_key``) or any other file that only Jig can read. It is never generated
by Jig and never stored next to the data: whoever has the data volume but not the key cannot read the
secrets.

* **Key file.** Its contents (surrounding whitespace removed) are the key material, at least
  ``MIN_KEY_CHARS`` characters, for example the output of ``openssl rand -base64 32``.
* **KDF.** scrypt (n=2^15, r=8, p=1) with a random 16-byte salt turns it into a 256-bit key.
* **Cipher.** AES-256-GCM with a random 96-bit nonce per secret; the secret's name is authenticated as
  associated data, so a ciphertext cannot be moved to another name.
* **Key check.** The salt, the KDF parameters and an encrypted check value are kept in the
  ``vault_keyfile`` table of the same database, so they travel with the ciphertexts in a backup. A wrong
  key fails at start-up, not when a tool first needs a secret.

Every problem (missing, empty, short or wrong key; secrets without their key check) raises
``VaultUnavailable``. There is no plaintext mode and no fallback to another backend.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from ..db import Database, now_iso
from ..errors import VaultUnavailable

NAME = "keyfile"
MIN_KEY_CHARS = 32
FORMAT = 1
_KDF = {"name": "scrypt", "n": 2**15, "r": 8, "p": 1, "length": 32}
_CHECK_PLAINTEXT = b"jig vault key check v1"
_CHECK_AAD = b"jig-vault-keycheck-v1"
_NONCE = 12
_SETUP = ("See docs/container.md (Vault key): create a key with `openssl rand -base64 32`, save it as "
          "secrets/jig_vault_key next to compose.yaml, and start again. Jig never generates the key itself and "
          "never stores secrets in plaintext.")


def read_key(path: str | os.PathLike[str]) -> bytes:
    p = Path(path)
    # Docker creates a directory when a bind-mounted key file is missing on the host.
    if p.is_dir():
        raise VaultUnavailable(f"the vault key path {p} is a directory, not a key file. {_SETUP}")
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        raise VaultUnavailable(f"the vault key file {p} does not exist. {_SETUP}") from None
    except OSError as exc:
        raise VaultUnavailable(f"the vault key file {p} cannot be read: {exc}. {_SETUP}") from exc
    key = raw.strip()
    if not key:
        raise VaultUnavailable(f"the vault key file {p} is empty. {_SETUP}")
    if len(key) < MIN_KEY_CHARS:
        raise VaultUnavailable(f"the vault key in {p} has {len(key)} characters; at least {MIN_KEY_CHARS} are "
                               f"needed. {_SETUP}")
    return key


def _derive(key: bytes, salt: bytes, params: dict) -> bytes:
    if params.get("name") != "scrypt":
        raise VaultUnavailable(f"unsupported vault KDF {params.get('name')!r}")
    return Scrypt(salt=salt, length=params["length"], n=params["n"], r=params["r"], p=params["p"]).derive(key)


class KeyFileCipher:
    """AES-256-GCM keyed from the key file, bound to one database by its stored salt and key check."""

    name = NAME

    def __init__(self, db: Database, key_file: str | os.PathLike[str]):
        self.key_file = Path(key_file)
        key = read_key(self.key_file)
        db.execute("CREATE TABLE IF NOT EXISTS vault_keyfile (id INTEGER PRIMARY KEY CHECK (id = 1), "
                   "format INTEGER NOT NULL, kdf_json TEXT NOT NULL, salt BLOB NOT NULL, "
                   "check_value BLOB NOT NULL, created_at TEXT NOT NULL)")
        meta = db.one("SELECT format, kdf_json, salt, check_value FROM vault_keyfile WHERE id = 1")
        if meta is None:
            stored = db.one("SELECT COUNT(*) AS n FROM secrets WHERE backend = ?", (NAME,))
            if stored and stored["n"]:
                raise VaultUnavailable(
                    f"the database holds {stored['n']} keyfile secret(s) but no key check (table vault_keyfile); "
                    "the vault metadata is missing, so those secrets cannot be verified or read. Restore the "
                    "database from a backup made with its key check.")
            salt = os.urandom(16)
            self._aead = AESGCM(_derive(key, salt, _KDF))
            db.execute("INSERT INTO vault_keyfile(id, format, kdf_json, salt, check_value, created_at) "
                       "VALUES (1, ?, ?, ?, ?, ?)",
                       (FORMAT, json.dumps(_KDF), salt, self._seal(_CHECK_PLAINTEXT, _CHECK_AAD), now_iso()))
            return
        if meta["format"] != FORMAT:
            raise VaultUnavailable(f"unsupported keyfile vault format {meta['format']}")
        self._aead = AESGCM(_derive(key, meta["salt"], json.loads(meta["kdf_json"])))
        try:
            ok = self._open(meta["check_value"], _CHECK_AAD) == _CHECK_PLAINTEXT
        except VaultUnavailable:
            ok = False
        if not ok:
            raise VaultUnavailable(
                f"the key in {self.key_file} does not match this vault (its key check failed). Use the key the "
                "vault was created with; secrets cannot be recovered without it.")

    def _seal(self, plaintext: bytes, aad: bytes) -> bytes:
        nonce = os.urandom(_NONCE)
        return nonce + self._aead.encrypt(nonce, plaintext, aad)

    def _open(self, blob: bytes, aad: bytes) -> bytes:
        if len(blob) <= _NONCE:
            raise VaultUnavailable("keyfile ciphertext is truncated")
        try:
            return self._aead.decrypt(blob[:_NONCE], blob[_NONCE:], aad)
        except InvalidTag:
            raise VaultUnavailable("keyfile ciphertext failed authentication (wrong key, or the data was "
                                   "changed)") from None

    @staticmethod
    def _aad(secret_name: str) -> bytes:
        return b"jig-vault-v1:" + secret_name.encode("utf-8")

    def encrypt(self, secret_name: str, plaintext: bytes) -> bytes:
        return self._seal(plaintext, self._aad(secret_name))

    def decrypt(self, secret_name: str, ciphertext: bytes) -> bytes:
        return self._open(ciphertext, self._aad(secret_name))
