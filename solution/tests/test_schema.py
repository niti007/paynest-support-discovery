import pytest

from solution.a7 import db

pytestmark = pytest.mark.db

PII_VAULT_CONTRACT = [
    ("ticket_id", "text"), ("placeholder", "text"), ("pii_type", "text"),
    ("ciphertext", "bytea"), ("key_ref", "text"), ("created_at", "timestamp with time zone"),
]


def test_vector_extension(owner):
    assert owner.execute("SELECT 1 FROM pg_extension WHERE extname='vector'").fetchone()


def test_schema_is_idempotent(owner):
    db.apply_schema(owner)
    db.apply_schema(owner)


def test_pii_vault_contract(owner):
    cols = owner.execute("SELECT column_name, data_type FROM information_schema.columns "
                         "WHERE table_name='pii_vault' ORDER BY ordinal_position").fetchall()
    assert cols == PII_VAULT_CONTRACT
    pk = owner.execute("SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey) "
                       "WHERE i.indrelid='pii_vault'::regclass AND i.indisprimary ORDER BY array_position(i.indkey::int[], a.attnum::int)").fetchall()
    assert [r[0] for r in pk] == ["ticket_id", "placeholder"]


def test_rls_enabled_and_forced(owner):
    for t in ("customers", "kb_chunks", "outbox", "audit_log", "incidents", "answer_cache"):
        en, forced = owner.execute("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname=%s", (t,)).fetchone()
        assert en and forced, t


def test_indexes(owner):
    defs = " ".join(r[0] for r in owner.execute("SELECT indexdef FROM pg_indexes WHERE tablename='kb_chunks'"))
    assert "hnsw" in defs and "vector_cosine_ops" in defs and "gin (tsv)" in defs


def test_rls_tenant_isolation(owner, app):
    owner.execute("DELETE FROM outbox WHERE ticket_id LIKE 'TEST-%'")
    owner.execute("DELETE FROM kb_chunks WHERE source_id LIKE 'TEST-%'")
    owner.execute("INSERT INTO outbox(tenant_id,ticket_id,masked_body) VALUES ('C-1001','TEST-1','x'),('C-1004','TEST-2','y')")
    owner.execute("INSERT INTO kb_chunks(source,source_id,region,topic,body,tenant_scope) VALUES "
                  "('kb','TEST-G','ALL','t','global',NULL),('kb','TEST-1','ALL','t','t1','C-1001'),('kb','TEST-4','ALL','t','t4','C-1004')")
    try:
        with db.tenant(app, "C-1001"):
            assert [r[0] for r in app.execute("SELECT ticket_id FROM outbox WHERE ticket_id LIKE 'TEST-%'")] == ["TEST-1"]
            ids = {r[0] for r in app.execute("SELECT source_id FROM kb_chunks WHERE source_id LIKE 'TEST-%'")}
            assert ids == {"TEST-G", "TEST-1"}
            # cross-tenant lookup returns 0
            assert app.execute("SELECT count(*) FROM outbox WHERE tenant_id='C-1004'").fetchone()[0] == 0
            assert app.execute("SELECT count(*) FROM kb_chunks WHERE tenant_scope='C-1004'").fetchone()[0] == 0
            assert app.execute("SELECT count(*) FROM customers WHERE customer_id='C-1004'").fetchone()[0] == 0
        # no tenant set -> fail closed (only global chunks, no tenant rows)
        assert app.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0
        assert app.execute("SELECT count(*) FROM kb_chunks WHERE tenant_scope IS NOT NULL").fetchone()[0] == 0
        # writing to another tenant is rejected
        with db.tenant(app, "C-1001"):
            with pytest.raises(Exception):
                app.execute("INSERT INTO outbox(tenant_id,ticket_id,masked_body) VALUES ('C-1004','TEST-3','z')")
    finally:
        owner.execute("DELETE FROM outbox WHERE ticket_id LIKE 'TEST-%'")
        owner.execute("DELETE FROM kb_chunks WHERE source_id LIKE 'TEST-%'")
