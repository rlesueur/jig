"""The keyfile vault backend, with real key files and a real SQLite database. Nothing is mocked."""

from __future__ import annotations

import dataclasses
import secrets

import pytest

from jig.config import VaultConfig, load_config
from jig.db import Database
from jig.errors import ConfigError, VaultUnavailable
from jig.runtime import Jig
from jig.vault import Vault


def make_key(path, value: str | None = None):
    path.write_text((value if value is not None else secrets.token_urlsafe(32)) + "\n", encoding="ascii")
    return path


@pytest.fixture
def key_file(tmp_path):
    return make_key(tmp_path / "jig_vault_key")


def open_vault(db_path, key_path) -> tuple[Vault, Database]:
    db = Database(db_path)
    return Vault(db, VaultConfig(backend="keyfile", key_file=str(key_path))), db


def test_round_trip_is_encrypted_at_rest(tmp_path, key_file):
    vault, db = open_vault(tmp_path / "jig.db", key_file)
    value = f"pw-{secrets.token_hex(12)}"
    vault.set("SITE_PASSWORD", value, allowed_tools=["browser_login"])
    assert vault.backend == "keyfile"
    assert vault.reveal("SITE_PASSWORD") == value
    row = db.one("SELECT backend, ciphertext FROM secrets WHERE name = 'SITE_PASSWORD'")
    assert row["backend"] == "keyfile"
    assert value.encode() not in row["ciphertext"]
    db.close()
    raw = (tmp_path / "jig.db").read_bytes()
    assert value.encode() not in raw, "the plaintext must not appear anywhere in the database file"


def test_secrets_survive_a_restart_with_the_same_key(tmp_path, key_file):
    vault, db = open_vault(tmp_path / "jig.db", key_file)
    vault.set("TOKEN", "first-value")
    db.close()
    vault, db = open_vault(tmp_path / "jig.db", key_file)
    assert vault.reveal("TOKEN") == "first-value"
    db.close()


def test_a_wrong_key_refuses_to_start(tmp_path, key_file):
    vault, db = open_vault(tmp_path / "jig.db", key_file)
    vault.set("TOKEN", "value")
    db.close()
    other = make_key(tmp_path / "other_key")
    with pytest.raises(VaultUnavailable, match="does not match this vault"):
        open_vault(tmp_path / "jig.db", other)


@pytest.mark.parametrize("content, message", [(None, "does not exist"), ("", "is empty"), ("   \n", "is empty"),
                                              ("short-key", "at least 32 are needed")])
def test_missing_or_weak_keys_fail_loudly_with_instructions(tmp_path, content, message):
    path = tmp_path / "jig_vault_key"
    if content is not None:
        path.write_text(content, encoding="ascii")
    with pytest.raises(VaultUnavailable, match=message) as exc:
        open_vault(tmp_path / "jig.db", path)
    assert "openssl rand -base64 32" in str(exc.value) and "never generates" in str(exc.value)
    assert not path.exists() or path.read_text(encoding="ascii") == content, "Jig must never write a key"


def test_a_directory_is_not_a_key(tmp_path):
    with pytest.raises(VaultUnavailable, match="is a directory"):
        open_vault(tmp_path / "jig.db", tmp_path)


def test_ciphertext_is_bound_to_its_name_and_tamper_evident(tmp_path, key_file):
    vault, db = open_vault(tmp_path / "jig.db", key_file)
    vault.set("A", "alpha-secret")
    vault.set("B", "bravo-secret")
    a = db.one("SELECT ciphertext FROM secrets WHERE name = 'A'")["ciphertext"]
    db.execute("UPDATE secrets SET ciphertext = ? WHERE name = 'B'", (a,))
    with pytest.raises(VaultUnavailable, match="failed authentication"):
        vault.reveal("B")
    flipped = bytearray(a)
    flipped[-1] ^= 0x01
    db.execute("UPDATE secrets SET ciphertext = ? WHERE name = 'A'", (bytes(flipped),))
    with pytest.raises(VaultUnavailable, match="failed authentication"):
        vault.reveal("A")
    db.close()


def test_secrets_without_their_key_check_refuse_to_start(tmp_path, key_file):
    vault, db = open_vault(tmp_path / "jig.db", key_file)
    vault.set("TOKEN", "value")
    db.execute("DROP TABLE vault_keyfile")
    db.close()
    with pytest.raises(VaultUnavailable, match="no key check"):
        open_vault(tmp_path / "jig.db", key_file)


def test_secrets_from_another_backend_are_not_read_silently(tmp_path, key_file):
    vault, db = open_vault(tmp_path / "jig.db", key_file)
    db.execute("INSERT INTO secrets(name, backend, ciphertext, allowed_tools_json, created_at, updated_at) "
               "VALUES ('OLD', 'dpapi', x'00', '[]', '', '')")
    with pytest.raises(VaultUnavailable, match="stored with the dpapi backend"):
        vault.reveal("OLD")
    db.close()


def test_config_selects_the_backend_explicitly(tmp_path, monkeypatch, key_file):
    assert load_config(data_dir=tmp_path / "d").vault.backend in ("auto", "keyfile")
    monkeypatch.setenv("JIG_VAULT_BACKEND", "keyfile")
    monkeypatch.delenv("JIG_VAULT_KEY_FILE", raising=False)
    with pytest.raises(ConfigError, match="needs key_file"):
        load_config(data_dir=tmp_path / "d")
    monkeypatch.setenv("JIG_VAULT_KEY_FILE", str(key_file))
    cfg = load_config(data_dir=tmp_path / "d").vault
    assert (cfg.backend, cfg.key_file) == ("keyfile", str(key_file))
    monkeypatch.setenv("JIG_VAULT_BACKEND", "plaintext")
    with pytest.raises(ConfigError, match="must be 'auto'"):
        load_config(data_dir=tmp_path / "d")


def test_the_runtime_refuses_to_start_without_a_key(tmp_path):
    config = load_config(data_dir=tmp_path / "data", sandbox_dir=tmp_path / "sandbox")
    missing = tmp_path / "no-such-key"
    config = dataclasses.replace(config, vault=VaultConfig(backend="keyfile", key_file=str(missing)))
    with pytest.raises(VaultUnavailable, match="does not exist"):
        Jig(config)
    assert not missing.exists()
    # The failed start released the instance lock, so a correctly configured runtime can open the same data.
    make_key(missing)
    runtime = Jig(dataclasses.replace(config, vault=VaultConfig(backend="keyfile", key_file=str(missing))))
    assert runtime.vault.backend == "keyfile"
    runtime.db.close()
    runtime.instance_lock.release()
