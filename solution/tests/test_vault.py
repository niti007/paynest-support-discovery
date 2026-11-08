import base64
import os
import shutil
import subprocess
import time
import uuid

import pytest
from botocore.stub import Stubber

from solution.a7.pii.masker import VaultEntry
from solution.a7.vault import KmsKeyProvider, LocalKeyProvider, MemoryVaultStore, PostgresVaultStore, Vault

VALUE = "synthetic-secret-" + uuid.uuid4().hex


def _entry(ph="<IBAN_1>"):
    return VaultEntry("T-1", ph, "IBAN", VALUE)


def test_local_roundtrip_and_act_only():
    v = Vault(MemoryVaultStore(), LocalKeyProvider(master_key=os.urandom(32)))
    assert v.put("T-1", [_entry()]) == 1
    assert v.reveal("T-1", "<IBAN_1>", caller="act") == VALUE
    with pytest.raises(PermissionError):
        v.reveal("T-1", "<IBAN_1>", caller="agent")
    with pytest.raises(KeyError):
        v.reveal("T-1", "<IBAN_9>", caller="act")


def test_ciphertext_has_no_plaintext_and_key_ref():
    store = MemoryVaultStore()
    Vault(store, LocalKeyProvider(master_key=os.urandom(32))).put("T-1", [_entry()])
    row = store.get("T-1", "<IBAN_1>")
    assert VALUE.encode() not in row.ciphertext and row.key_ref.startswith("local:")


def test_wrong_key_and_tampered_aad_fail():
    store = MemoryVaultStore()
    Vault(store, LocalKeyProvider(master_key=os.urandom(32))).put("T-1", [_entry()])
    with pytest.raises(ValueError):
        Vault(store, LocalKeyProvider(master_key=os.urandom(32))).reveal("T-1", "<IBAN_1>", caller="act")
    row = store.get("T-1", "<IBAN_1>")
    store.rows[("T-2", "<IBAN_1>")] = type(row)("T-2", "<IBAN_1>", "IBAN", row.ciphertext, row.key_ref)  # moved blob
    with pytest.raises(Exception):
        Vault(store, LocalKeyProvider(master_key=bytes(32))).reveal("T-2", "<IBAN_1>", caller="act")


def test_local_key_from_env_and_file(monkeypatch, tmp_path):
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("A7_LOCAL_VAULT_KEY", key)
    assert LocalKeyProvider().key_ref == LocalKeyProvider().key_ref
    monkeypatch.delenv("A7_LOCAL_VAULT_KEY")
    f = tmp_path / ".a7_vault_key"
    a = LocalKeyProvider(key_file=f)   # generated on first use
    assert f.exists() and LocalKeyProvider(key_file=f).key_ref == a.key_ref


def test_kms_roundtrip_with_stubber():
    import boto3
    client = boto3.client("kms", region_name="eu-north-1", aws_access_key_id="x", aws_secret_access_key="y")
    dk = os.urandom(32)
    wrapped = b"wrapped-data-key"
    kid = "arn:aws:kms:eu-north-1:000000000000:key/test"
    with Stubber(client) as st:
        st.add_response("generate_data_key", {"Plaintext": dk, "CiphertextBlob": wrapped, "KeyId": kid},
                        {"KeyId": kid, "KeySpec": "AES_256", "EncryptionContext": {"ticket_id": "T-1"}})
        st.add_response("decrypt", {"Plaintext": dk, "KeyId": kid},
                        {"CiphertextBlob": wrapped, "KeyId": kid, "EncryptionContext": {"ticket_id": "T-1"}})
        v = Vault(MemoryVaultStore(), KmsKeyProvider(key_id=kid, client=client))
        v.put("T-1", [_entry()])
        assert v.store.get("T-1", "<IBAN_1>").key_ref == "kms:" + kid
        assert v.reveal("T-1", "<IBAN_1>", caller="act") == VALUE
        st.assert_no_pending_responses()


def test_kms_requires_key_id(monkeypatch):
    monkeypatch.delenv("A7_KMS_KEY_ID", raising=False)
    with pytest.raises(ValueError):
        KmsKeyProvider(client=object())


DDL = """CREATE TABLE pii_vault (ticket_id text, placeholder text, pii_type text, ciphertext bytea, key_ref text,
 created_at timestamptz DEFAULT now(), PRIMARY KEY (ticket_id, placeholder))"""


@pytest.fixture()
def pg():
    if not shutil.which("docker"):
        pytest.skip("docker not available")
    name = "a7-b-pg"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--name", name, "-e", "POSTGRES_PASSWORD=pw", "-p", "55433:5432",
                    "pgvector/pgvector:pg17"], check=True, capture_output=True)
    try:
        import psycopg
        conn = None
        for _ in range(60):
            try:
                conn = psycopg.connect("host=localhost port=55433 user=postgres password=pw dbname=postgres", connect_timeout=2)
                break
            except Exception:
                time.sleep(1)
        assert conn, "postgres did not come up"
        conn.execute(DDL)
        conn.commit()
        yield conn
        conn.close()
    finally:
        subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True)


@pytest.mark.db
def test_postgres_store_roundtrip(pg):
    v = Vault(PostgresVaultStore(pg), LocalKeyProvider(master_key=os.urandom(32)))
    v.put("T-1", [_entry("<IBAN_1>"), _entry("<IBAN_2>")])
    v.put("T-1", [_entry("<IBAN_1>")])  # idempotent upsert
    assert v.reveal("T-1", "<IBAN_2>", caller="act") == VALUE
    with pytest.raises(PermissionError):
        v.reveal("T-1", "<IBAN_1>", caller="agent")
    assert pg.execute("select count(*) from pii_vault").fetchone()[0] == 2
    row = pg.execute("select pii_type, key_ref, created_at from pii_vault limit 1").fetchone()
    assert row[0] == "IBAN" and row[1].startswith("local:") and row[2] is not None
