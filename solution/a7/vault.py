"""PII vault: envelope encryption + a store behind a protocol. Only `act` may reveal values.

Blob layout (stored in pii_vault.ciphertext):
    version(1) | len(wrapped_key)(2, big endian) | wrapped_key | nonce(12) | AES-256-GCM(ciphertext+tag)
The AAD binds each blob to (ticket_id, placeholder). `key_ref` names the provider and key:
"local:<fingerprint>" or "kms:<key id>". One data key is generated per Vault.put call (per ticket).

pii_vault(ticket_id text, placeholder text, pii_type text, ciphertext bytea, key_ref text,
          created_at timestamptz default now(), PRIMARY KEY(ticket_id, placeholder))
"""
from __future__ import annotations

import base64
import hashlib
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_VERSION = 1
REVEAL_CALLER = "act"


@dataclass(frozen=True)
class DataKey:
    plaintext: bytes = None  # type: ignore[assignment]
    wrapped: bytes = b""
    key_ref: str = ""

    def __repr__(self) -> str:  # never print key material
        return f"DataKey(key_ref={self.key_ref!r})"


class KeyProvider(Protocol):
    def generate_data_key(self, context: dict[str, str]) -> DataKey: ...
    def decrypt_data_key(self, wrapped: bytes, key_ref: str, context: dict[str, str]) -> bytes: ...


def _ctx_aad(context: dict[str, str]) -> bytes:
    return "|".join(f"{k}={context[k]}" for k in sorted(context)).encode()


class LocalKeyProvider:
    """AES-256-GCM key wrapping with a local master key. Dev and tests only."""

    KEY_FILE = ".a7_vault_key"

    def __init__(self, master_key: bytes | None = None, key_file: str | Path | None = None):
        if master_key is None:
            env = os.environ.get("A7_LOCAL_VAULT_KEY")
            if env:
                master_key = base64.b64decode(env)
            else:
                path = Path(key_file or self.KEY_FILE)
                if path.exists():
                    master_key = base64.b64decode(path.read_text().strip())
                else:
                    master_key = AESGCM.generate_key(256)
                    path.write_text(base64.b64encode(master_key).decode())
                    os.chmod(path, 0o600)
        if len(master_key) != 32:
            raise ValueError("local vault key must be 32 bytes (base64 of 256 bits)")
        self._master = AESGCM(master_key)
        self.key_ref = "local:" + hashlib.sha256(master_key).hexdigest()[:12]

    def generate_data_key(self, context: dict[str, str]) -> DataKey:
        dk = AESGCM.generate_key(256)
        nonce = os.urandom(12)
        wrapped = nonce + self._master.encrypt(nonce, dk, _ctx_aad(context))
        return DataKey(dk, wrapped, self.key_ref)

    def decrypt_data_key(self, wrapped: bytes, key_ref: str, context: dict[str, str]) -> bytes:
        if key_ref != self.key_ref:
            raise ValueError(f"key_ref {key_ref!r} does not belong to this provider")
        return self._master.decrypt(wrapped[:12], wrapped[12:], _ctx_aad(context))


class KmsKeyProvider:
    """KMS GenerateDataKey + Decrypt. Key id from argument or env A7_KMS_KEY_ID. Client injectable."""

    def __init__(self, key_id: str | None = None, client: Any = None, region: str | None = None):
        self.key_id = key_id or os.environ.get("A7_KMS_KEY_ID")
        if not self.key_id:
            raise ValueError("KMS key id required (argument or A7_KMS_KEY_ID)")
        if client is None:
            import boto3
            client = boto3.client("kms", region_name=region or os.environ.get("AWS_REGION", "eu-north-1"))
        self._kms = client
        self.key_ref = f"kms:{self.key_id}"

    def generate_data_key(self, context: dict[str, str]) -> DataKey:
        r = self._kms.generate_data_key(KeyId=self.key_id, KeySpec="AES_256", EncryptionContext=dict(context))
        return DataKey(r["Plaintext"], r["CiphertextBlob"], self.key_ref)

    def decrypt_data_key(self, wrapped: bytes, key_ref: str, context: dict[str, str]) -> bytes:
        r = self._kms.decrypt(CiphertextBlob=wrapped, KeyId=key_ref.removeprefix("kms:"), EncryptionContext=dict(context))
        return r["Plaintext"]


@dataclass(frozen=True)
class VaultRow:
    ticket_id: str
    placeholder: str
    pii_type: str
    ciphertext: bytes
    key_ref: str


class VaultStore(Protocol):
    def put_many(self, rows: Iterable[VaultRow]) -> None: ...
    def get(self, ticket_id: str, placeholder: str) -> VaultRow | None: ...


class MemoryVaultStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], VaultRow] = {}

    def put_many(self, rows: Iterable[VaultRow]) -> None:
        for r in rows:
            self.rows[(r.ticket_id, r.placeholder)] = r

    def get(self, ticket_id: str, placeholder: str) -> VaultRow | None:
        return self.rows.get((ticket_id, placeholder))


class PostgresVaultStore:
    """Writes the contract table `pii_vault` through a psycopg connection supplied by the caller."""

    def __init__(self, conn: Any, commit: bool = True):
        self.conn, self.commit = conn, commit

    def put_many(self, rows: Iterable[VaultRow]) -> None:
        data = [(r.ticket_id, r.placeholder, r.pii_type, r.ciphertext, r.key_ref) for r in rows]
        if not data:
            return
        with self.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO pii_vault (ticket_id, placeholder, pii_type, ciphertext, key_ref) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",  # no SELECT needed: app_user cannot read pii_vault
                data)
        if self.commit:
            self.conn.commit()

    def get(self, ticket_id: str, placeholder: str) -> VaultRow | None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT ticket_id, placeholder, pii_type, ciphertext, key_ref FROM pii_vault "
                        "WHERE ticket_id = %s AND placeholder = %s", (ticket_id, placeholder))
            row = cur.fetchone()
        return VaultRow(row[0], row[1], row[2], bytes(row[3]), row[4]) if row else None


def _seal(dk: DataKey, ticket_id: str, placeholder: str, value: str) -> bytes:
    nonce = os.urandom(12)
    ct = AESGCM(dk.plaintext).encrypt(nonce, value.encode(), f"{ticket_id}|{placeholder}".encode())
    return bytes([_VERSION]) + struct.pack(">H", len(dk.wrapped)) + dk.wrapped + nonce + ct


class Vault:
    def __init__(self, store: VaultStore, keys: KeyProvider):
        self.store, self.keys = store, keys

    def put(self, ticket_id: str, entries: Iterable[Any]) -> int:
        """Encrypt and store entries (objects with .placeholder, .pii_type, .value). Returns count."""
        entries = list(entries)
        if not entries:
            return 0
        dk = self.keys.generate_data_key({"ticket_id": ticket_id})
        self.store.put_many(
            VaultRow(ticket_id, e.placeholder, e.pii_type, _seal(dk, ticket_id, e.placeholder, e.value), dk.key_ref)
            for e in entries)
        return len(entries)

    def reveal(self, ticket_id: str, placeholder: str, *, caller: str) -> str:
        """Return the real value. Only the `act` node may call this."""
        if caller != REVEAL_CALLER:
            raise PermissionError(f"vault may only be opened by {REVEAL_CALLER!r}, not {caller!r}")
        row = self.store.get(ticket_id, placeholder)
        if row is None:
            raise KeyError(f"no vault entry for {ticket_id}/{placeholder}")
        blob = row.ciphertext
        if blob[0] != _VERSION:
            raise ValueError("unknown vault blob version")
        (n,) = struct.unpack(">H", blob[1:3])
        wrapped, nonce, ct = blob[3:3 + n], blob[3 + n:15 + n], blob[15 + n:]
        key = self.keys.decrypt_data_key(wrapped, row.key_ref, {"ticket_id": ticket_id})
        return AESGCM(key).decrypt(nonce, ct, f"{ticket_id}|{placeholder}".encode()).decode()
