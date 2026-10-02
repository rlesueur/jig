"""Paired devices: how your other devices sign in without ever holding the master API token.

* On the host, "Add a device" creates a short one-time **pairing code** (8 characters, valid for
  5 minutes, single use). It is shown with a QR code of the pairing URL ``<origin>/#pair=<code>``;
  the code travels in the URL fragment, which browsers never send to a server.
* The new device opens that URL, confirms the code and gives itself a name. It gets its own
  **device session**: a random 256-bit secret in an HttpOnly, SameSite=Strict cookie (Secure over
  HTTPS). Only a SHA-256 hash of the secret is stored, in ``<data_dir>/devices.db``.
* Every device has a name, created and last-used times and an optional expiry, and can be revoked on
  its own. Each device session is bound to the current master token: rotating the token revokes every
  device. A device paired over Tailscale is also bound to the Tailscale login that paired it.
* After five wrong codes in a row, every outstanding pairing code is cancelled.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .db import iso, new_id, now, now_iso
from .errors import JigError, NotFound

DEVICE_COOKIE = "jig_device"
DB_FILENAME = "devices.db"
PAIRING_TTL_S = 300
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"  # no 0/O, 1/I/L or U, so codes are easy to read and type
CODE_LENGTH = 8
MAX_FAILED_ATTEMPTS = 5
MAX_EXPIRY_DAYS = 365
COOKIE_MAX_AGE_S = 400 * 86400  # browsers cap cookie lifetimes at 400 days
LAST_USED_RESOLUTION_S = 60.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    secret_hash TEXT NOT NULL,
    token_fp TEXT NOT NULL,
    paired_via TEXT NOT NULL,
    tailscale_login TEXT,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    expires_at TEXT,
    revoked_at TEXT,
    revoked_reason TEXT
);
"""


class PairingError(JigError):
    """A pairing code was wrong, expired or already used."""


def normalise_code(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


def display_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(f"jig-device-binding:{token}".encode()).hexdigest()[:32]


class DeviceStore:
    def __init__(self, data_dir: Path, current_token: Any, *, pairing_ttl_s: float = PAIRING_TTL_S):
        """``current_token`` returns the master token (TokenStore.get), read on every check so a rotation
        made by 'jig token rotate' while Jig runs takes effect at once."""
        self.pairing_ttl_s = pairing_ttl_s
        self.path = Path(data_dir) / DB_FILENAME
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._current_token = current_token
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA)
        self._codes: dict[str, dict[str, Any]] = {}
        self._failures = 0
        self._last_used_written: dict[str, float] = {}

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # Pairing codes -------------------------------------------------------------------------------
    def new_pairing(self, *, expires_in_days: int | None = None, created_via: str = "local") -> dict[str, Any]:
        if expires_in_days is not None and not 1 <= expires_in_days <= MAX_EXPIRY_DAYS:
            raise ValueError(f"expires_in_days must be between 1 and {MAX_EXPIRY_DAYS}, or left out for no expiry")
        with self._lock:
            t = time.time()
            self._codes = {c: v for c, v in self._codes.items() if v["expires"] > t}
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
            self._codes[code] = {"expires": t + self.pairing_ttl_s, "device_expires_in_days": expires_in_days,
                                 "created_via": created_via}
        return {"code": code, "display_code": display_code(code), "expires_in": int(self.pairing_ttl_s),
                "device_expires_in_days": expires_in_days}

    def pending_codes(self) -> int:
        t = time.time()
        return sum(1 for v in self._codes.values() if v["expires"] > t)

    def redeem(self, code: str, *, name: str, paired_via: str, tailscale_login: str | None) -> tuple[dict[str, Any], str]:
        """Use a pairing code (once, within 5 minutes) to create a device. Returns the device and the
        cookie value for its session."""
        name = name.strip()
        if not 1 <= len(name) <= 60:
            raise ValueError("give this device a name of 1 to 60 characters, such as 'Robyn's phone'")
        with self._lock:
            pending = self._codes.pop(normalise_code(code), None)
            if pending is None or pending["expires"] <= time.time():
                self._failures += 1
                if self._failures >= MAX_FAILED_ATTEMPTS:
                    self._codes.clear()
                    self._failures = 0
                    raise PairingError("That code is not valid, and there have been too many wrong codes, so every "
                                       "pairing code has been cancelled. Create a new one on the host.")
                raise PairingError("That pairing code is not valid. Codes work once and expire after 5 minutes; "
                                   "create a new one on the host if you need to.")
            self._failures = 0
            secret = secrets.token_urlsafe(32)
            device_id = new_id("dev")
            days = pending["device_expires_in_days"]
            expires_at = iso(now() + timedelta(days=days)) if days else None
            self._conn.execute(
                "INSERT INTO devices (id, name, secret_hash, token_fp, paired_via, tailscale_login, created_at, "
                "expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (device_id, name, _hash(secret), token_fingerprint(self._current_token()), paired_via,
                 tailscale_login, now_iso(), expires_at))
        return self.get(device_id), f"{device_id}.{secret}"

    # Device sessions -----------------------------------------------------------------------------
    def authenticate(self, cookie_value: str, *, tailscale_login: str | None) -> dict[str, Any] | None:
        device_id, _, secret = cookie_value.partition(".")
        if not device_id.startswith("dev_") or not secret:
            return None
        with self._lock:
            row = self._conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
            if row is None or row["revoked_at"] or not hmac.compare_digest(row["secret_hash"], _hash(secret)):
                return None
            if row["token_fp"] != token_fingerprint(self._current_token()):
                self._revoke(device_id, "the master API token was rotated")
                return None
            if row["expires_at"] and datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC):
                return None
            if row["tailscale_login"] and tailscale_login is not None and tailscale_login != row["tailscale_login"]:
                return None  # paired by one tailnet user, presented by another
            t = time.monotonic()
            if t - self._last_used_written.get(device_id, -LAST_USED_RESOLUTION_S) >= LAST_USED_RESOLUTION_S:
                self._conn.execute("UPDATE devices SET last_used_at = ? WHERE id = ?", (now_iso(), device_id))
                self._last_used_written[device_id] = t
            return self._public(row)

    def cookie_max_age(self, device: dict[str, Any]) -> int:
        if not device["expires_at"]:
            return COOKIE_MAX_AGE_S
        left = (datetime.fromisoformat(device["expires_at"]) - datetime.now(UTC)).total_seconds()
        return max(0, min(COOKIE_MAX_AGE_S, int(left)))

    # Listing and revoking ------------------------------------------------------------------------
    def _sweep_rotated(self) -> None:
        fp = token_fingerprint(self._current_token())
        self._conn.execute("UPDATE devices SET revoked_at = ?, revoked_reason = ? WHERE revoked_at IS NULL AND "
                           "token_fp != ?", (now_iso(), "the master API token was rotated", fp))

    def list(self, *, include_revoked: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            self._sweep_rotated()
            sql = "SELECT * FROM devices" + ("" if include_revoked else " WHERE revoked_at IS NULL")
            return [self._public(r) for r in self._conn.execute(sql + " ORDER BY created_at").fetchall()]

    def get(self, device_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        if row is None:
            raise NotFound(f"no paired device {device_id}")
        return self._public(row)

    def _revoke(self, device_id: str, reason: str) -> None:
        self._conn.execute("UPDATE devices SET revoked_at = ?, revoked_reason = ? WHERE id = ? AND revoked_at IS NULL",
                           (now_iso(), reason, device_id))

    def revoke(self, device_id: str, reason: str = "revoked by the user") -> dict[str, Any]:
        device = self.get(device_id)
        if device["revoked_at"]:
            raise ValueError(f"device {device['name']!r} was already revoked ({device['revoked_reason']})")
        with self._lock:
            self._revoke(device_id, reason)
        return self.get(device_id)

    def revoke_all(self, reason: str) -> int:
        with self._lock:
            self._codes.clear()
            cur = self._conn.execute("UPDATE devices SET revoked_at = ?, revoked_reason = ? WHERE revoked_at IS NULL",
                                     (now_iso(), reason))
            return cur.rowcount

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        d = {k: row[k] for k in row.keys() if k not in ("secret_hash", "token_fp")}
        expired = bool(d["expires_at"]) and datetime.fromisoformat(d["expires_at"]) <= datetime.now(UTC)
        d["active"] = not d["revoked_at"] and not expired
        d["expired"] = expired
        return d


def revoke_all_offline(data_dir: Path, reason: str) -> int:
    """For 'jig token rotate': revoke every device, whether or not Jig is running."""
    if not (Path(data_dir) / DB_FILENAME).exists():
        return 0
    store = DeviceStore(data_dir, current_token=lambda: "")
    try:
        return store.revoke_all(reason)
    finally:
        store.close()
