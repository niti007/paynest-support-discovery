-- PayNest A7 schema. Idempotent: safe to re-run (CREATE ... IF NOT EXISTS, DROP POLICY IF EXISTS).
-- Applied automatically on first start of an empty postgres volume (docker-compose initdb.d),
-- or any time with:  uv run python -m solution.a7.db apply
--
-- Data rule: no raw PII in any table. customers/agents hold synthetic business/roster names only.
-- Only pii_vault holds sensitive values, and only as ciphertext.
--
-- Tenant isolation: tables that carry a tenant id (customers.customer_id, kb_chunks.tenant_scope,
-- outbox/audit_log/incidents/answer_cache.tenant_id) have ENABLE + FORCE ROW LEVEL SECURITY with a policy
-- on current_setting('app.tenant_id', true). Unset tenant => NULL => no rows (fail closed).
-- NOTE: Postgres superusers (the local docker 'postgres' user) always bypass RLS, even with FORCE.
-- Applications must connect as app_user (non-owner, non-superuser) for isolation to apply.

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------- roles
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_user') THEN
    -- Dev-only password. In AWS the credential comes from Secrets Manager.
    CREATE ROLE app_user LOGIN PASSWORD 'app_user_dev' NOSUPERUSER NOBYPASSRLS;
  END IF;
END $$;

-- ---------------------------------------------------------------- tenant helper
CREATE SCHEMA IF NOT EXISTS a7;

-- a7.set_tenant('C-1001')        -> scopes the rest of the transaction (is_local => true, default)
-- a7.set_tenant('C-1001', false) -> scopes the session.
CREATE OR REPLACE FUNCTION a7.set_tenant(t text, is_local boolean DEFAULT true) RETURNS text
LANGUAGE sql AS $$ SELECT set_config('app.tenant_id', t, is_local) $$;

-- ---------------------------------------------------------------- reference data
CREATE TABLE IF NOT EXISTS customers (            -- the tenants
  customer_id       text PRIMARY KEY,             -- tenant id, e.g. C-1004
  name              text NOT NULL UNIQUE,         -- canonical business name
  tier              text NOT NULL,
  region            text NOT NULL CHECK (region IN ('IN','UAE')),
  monthly_fee_usd   numeric(10,2) NOT NULL,
  employees_paid    integer NOT NULL
);

-- Partner-reported company name -> customer id. EXACT name match only (never substring:
-- C-1004 Falcon Trading LLC vs C-1050 Falcon Contracting LLC). Not under RLS: it is used by
-- resolve_tenant before a tenant is known.
CREATE TABLE IF NOT EXISTS partner_customer_map (
  partner_name   text PRIMARY KEY,
  customer_id    text NOT NULL REFERENCES customers(customer_id),
  match_type     text NOT NULL CHECK (match_type IN ('exact','manual')),
  confidence     numeric(3,2) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  partner_ids    text[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS agents (
  agent_id     text PRIMARY KEY,
  agent_name   text NOT NULL,        -- synthetic roster name; do not copy into logs/prompts. No email/phone loaded.
  tier         text NOT NULL,
  desk         text NOT NULL,
  languages    text[] NOT NULL,
  hire_date    date,
  status       text NOT NULL,
  left_date    date
);

CREATE TABLE IF NOT EXISTS statutory_rules (
  rule_id           text PRIMARY KEY,
  region            text NOT NULL,
  topic             text NOT NULL,
  title             text NOT NULL,
  effective_from    date NOT NULL,
  old_value         text,
  new_value         text,
  affects_kb_topics text[] NOT NULL DEFAULT '{}',
  source            text,
  status            text NOT NULL
);

-- ---------------------------------------------------------------- retrieval corpus
CREATE TABLE IF NOT EXISTS kb_chunks (
  id             bigserial PRIMARY KEY,
  source         text NOT NULL CHECK (source IN ('kb','rule')),
  source_id      text NOT NULL,                 -- KB-0201 / R-UAE-WPS-2026-07
  chunk_index    integer NOT NULL DEFAULT 0,
  region         text NOT NULL CHECK (region IN ('ALL','IN','UAE')),
  language       text NOT NULL DEFAULT 'en',
  tenant_scope   text,                          -- NULL = global; else a customer_id
  topic          text NOT NULL,
  title          text,
  body           text NOT NULL,
  tsv            tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(title,'') || ' ' || body)) STORED,
  embedding      vector(1024),                  -- NULL when loaded with --no-embed
  last_reviewed  date,
  effective_from date,                          -- rules: rule date. KB: NULL (valid from review, no explicit start)
  effective_to   date,                          -- set when a newer rule supersedes the chunk
  superseded_by  text,                          -- rule_id
  UNIQUE (source, source_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS kb_chunks_embedding_hnsw ON kb_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS kb_chunks_tsv_gin ON kb_chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS kb_chunks_region_topic ON kb_chunks (region, topic);

-- ---------------------------------------------------------------- PII vault (contract with Builder B: do not change)
CREATE TABLE IF NOT EXISTS pii_vault (
  ticket_id    text NOT NULL,
  placeholder  text NOT NULL,
  pii_type     text NOT NULL,
  ciphertext   bytea NOT NULL,
  key_ref      text NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (ticket_id, placeholder)
);

-- ---------------------------------------------------------------- later-phase placeholders (empty)
CREATE TABLE IF NOT EXISTS outbox (
  id           bigserial PRIMARY KEY,
  tenant_id    text NOT NULL,
  ticket_id    text NOT NULL,
  channel      text,
  masked_body  text NOT NULL,                   -- masked text only; real values restored by act at send time
  status       text NOT NULL DEFAULT 'pending',
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_log (
  id           bigserial PRIMARY KEY,
  tenant_id    text NOT NULL,
  ticket_id    text,
  event        text NOT NULL,
  masked_text  text,                            -- masked text only
  signals      jsonb NOT NULL DEFAULT '{}',
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS incidents (
  id           bigserial PRIMARY KEY,
  tenant_id    text NOT NULL,
  topic        text,
  ticket_ids   text[] NOT NULL DEFAULT '{}',
  status       text NOT NULL DEFAULT 'open',
  opened_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS answer_cache (
  id           bigserial PRIMARY KEY,
  tenant_id    text NOT NULL,
  topic        text,
  question     text,                            -- masked
  answer       text,                            -- masked
  embedding    vector(1024),
  approved     boolean NOT NULL DEFAULT false,
  created_at   timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- RLS
ALTER TABLE customers    ENABLE ROW LEVEL SECURITY;
ALTER TABLE customers    FORCE ROW LEVEL SECURITY;
ALTER TABLE kb_chunks    ENABLE ROW LEVEL SECURITY;
ALTER TABLE kb_chunks    FORCE ROW LEVEL SECURITY;
ALTER TABLE outbox       ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbox       FORCE ROW LEVEL SECURITY;
ALTER TABLE audit_log    ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log    FORCE ROW LEVEL SECURITY;
ALTER TABLE incidents    ENABLE ROW LEVEL SECURITY;
ALTER TABLE incidents    FORCE ROW LEVEL SECURITY;
ALTER TABLE answer_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE answer_cache FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON customers;
CREATE POLICY tenant_isolation ON customers
  USING (customer_id = current_setting('app.tenant_id', true))
  WITH CHECK (customer_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS tenant_isolation ON kb_chunks;
CREATE POLICY tenant_isolation ON kb_chunks            -- NULL tenant_scope = global, visible to every tenant
  USING (tenant_scope IS NULL OR tenant_scope = current_setting('app.tenant_id', true))
  WITH CHECK (tenant_scope IS NULL OR tenant_scope = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS tenant_isolation ON outbox;
CREATE POLICY tenant_isolation ON outbox
  USING (tenant_id = current_setting('app.tenant_id', true))
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS tenant_isolation ON audit_log;
CREATE POLICY tenant_isolation ON audit_log
  USING (tenant_id = current_setting('app.tenant_id', true))
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS tenant_isolation ON incidents;
CREATE POLICY tenant_isolation ON incidents
  USING (tenant_id = current_setting('app.tenant_id', true))
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS tenant_isolation ON answer_cache;
CREATE POLICY tenant_isolation ON answer_cache
  USING (tenant_id = current_setting('app.tenant_id', true))
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

-- ---------------------------------------------------------------- grants (app_user: non-owner)
GRANT USAGE ON SCHEMA public, a7 TO app_user;
GRANT EXECUTE ON FUNCTION a7.set_tenant(text, boolean) TO app_user;
GRANT SELECT ON customers, partner_customer_map, agents, statutory_rules, kb_chunks TO app_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON outbox, audit_log, incidents, answer_cache TO app_user;
GRANT SELECT, INSERT, DELETE ON pii_vault TO app_user;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO app_user;
