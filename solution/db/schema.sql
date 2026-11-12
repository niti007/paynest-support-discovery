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

-- a7_act: the ONLY role that can SELECT pii_vault (box 12 `act` reveals values for outbound replies).
-- It has no other table privileges. In AWS its credential comes from Secrets Manager (bootstrap sets it).
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'a7_act') THEN
    CREATE ROLE a7_act LOGIN PASSWORD 'a7_act_dev' NOSUPERUSER NOBYPASSRLS;
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

-- Phase 3 additions (idempotent ALTERs so databases created by earlier phases upgrade in place).
-- outbox.kind: reply | draft | assignment | notify | jira_intent | refund_intent | credit_intent | cluster
-- shadow=true: auto_answer recorded but never to be sent (AUTO_SEND=false).  Nothing in A7 sends; a sender
-- service would pick up status='queued' rows, and render PII only through act.render_outbound (a7_act).
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS kind            text NOT NULL DEFAULT 'reply';
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS lane            text;
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS shadow          boolean NOT NULL DEFAULT false;
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS restore_pii     boolean NOT NULL DEFAULT false;
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS approved_by     text;
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS payload         jsonb NOT NULL DEFAULT '{}';
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS idempotency_key text;
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS sent_at         timestamptz;   -- always NULL in this build
CREATE UNIQUE INDEX IF NOT EXISTS outbox_idem ON outbox (idempotency_key);
CREATE INDEX IF NOT EXISTS outbox_ticket ON outbox (ticket_id);

ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS node       text;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS dedupe_key text;
CREATE UNIQUE INDEX IF NOT EXISTS audit_log_dedupe ON audit_log (dedupe_key);
CREATE INDEX IF NOT EXISTS audit_log_ticket ON audit_log (ticket_id);

-- Box 11: one approval per ticket (ticket_id is also the LangGraph thread_id). Masked text only.
-- status: pending | deciding (claimed by the API while the graph resumes) | approved | rejected | edited
CREATE TABLE IF NOT EXISTS approvals (
  ticket_id             text PRIMARY KEY,
  tenant_id             text NOT NULL,
  lane                  text NOT NULL,
  priority              text,
  category              text,
  status                text NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending','deciding','approved','rejected','edited')),
  draft_masked          text,
  edited_draft_masked   text,
  reviewer              text,
  requested_at          timestamptz NOT NULL DEFAULT now(),
  decided_at            timestamptz
);
CREATE INDEX IF NOT EXISTS approvals_pending ON approvals (tenant_id, status);

-- Sourced escalation timers (SLA 3.1, ESC). due_at is absolute; run_due fires each row once.
-- kind: l1_to_l2 | privacy_dpo_security | p1_first_response
CREATE TABLE IF NOT EXISTS escalations (
  id           bigserial PRIMARY KEY,
  tenant_id    text NOT NULL,
  ticket_id    text NOT NULL,
  kind         text NOT NULL,
  level        text NOT NULL,                   -- target: L2 | DPO+Security | first_response
  due_at       timestamptz NOT NULL,
  fired_at     timestamptz,
  resolved_at  timestamptz,                     -- a human decision stops the clock (not for privacy)
  reason       text NOT NULL,
  UNIQUE (ticket_id, kind)
);
CREATE INDEX IF NOT EXISTS escalations_due ON escalations (due_at) WHERE fired_at IS NULL AND resolved_at IS NULL;

CREATE TABLE IF NOT EXISTS kb_suggestions (
  id            bigserial PRIMARY KEY,
  tenant_id     text NOT NULL,
  ticket_id     text NOT NULL,
  source_id     text,                            -- KB article / rule id, when known
  reason        text NOT NULL,                   -- stale_kb | missing_kb | reviewer_edit
  suggested_text text,                           -- masked
  status        text NOT NULL DEFAULT 'proposed',
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (ticket_id, reason, source_id)
);

-- A human resolved an ambiguous partner name -> customer. A PROPOSAL only: the code decides the customer
-- (S4 p.26) and nothing here is read by resolve_tenant until a person approves it.
CREATE TABLE IF NOT EXISTS partner_map_suggestions (
  id            bigserial PRIMARY KEY,
  tenant_id     text NOT NULL,
  ticket_id     text NOT NULL,
  partner_name  text NOT NULL,                   -- business name, not personal data
  customer_id   text NOT NULL,
  resolved_by   text,
  status        text NOT NULL DEFAULT 'proposed',
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (partner_name, customer_id)
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
ALTER TABLE approvals    ENABLE ROW LEVEL SECURITY;
ALTER TABLE approvals    FORCE ROW LEVEL SECURITY;
ALTER TABLE escalations  ENABLE ROW LEVEL SECURITY;
ALTER TABLE escalations  FORCE ROW LEVEL SECURITY;
ALTER TABLE kb_suggestions ENABLE ROW LEVEL SECURITY;
ALTER TABLE kb_suggestions FORCE ROW LEVEL SECURITY;
ALTER TABLE partner_map_suggestions ENABLE ROW LEVEL SECURITY;
ALTER TABLE partner_map_suggestions FORCE ROW LEVEL SECURITY;

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

DROP POLICY IF EXISTS tenant_isolation ON approvals;
CREATE POLICY tenant_isolation ON approvals
  USING (tenant_id = current_setting('app.tenant_id', true))
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

-- escalations: the timer runner polls across tenants, so it sets app.runner='escalation' (transaction-local)
-- instead of a tenant. Like app.tenant_id this is an application-level scope, not a privilege boundary.
DROP POLICY IF EXISTS tenant_isolation ON escalations;
CREATE POLICY tenant_isolation ON escalations
  USING (tenant_id = current_setting('app.tenant_id', true)
         OR current_setting('app.runner', true) = 'escalation')
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true)
         OR current_setting('app.runner', true) = 'escalation');

DROP POLICY IF EXISTS tenant_isolation ON kb_suggestions;
CREATE POLICY tenant_isolation ON kb_suggestions
  USING (tenant_id = current_setting('app.tenant_id', true))
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS tenant_isolation ON partner_map_suggestions;
CREATE POLICY tenant_isolation ON partner_map_suggestions
  USING (tenant_id = current_setting('app.tenant_id', true))
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

-- ---------------------------------------------------------------- grants (app_user: non-owner)
GRANT USAGE ON SCHEMA public, a7 TO app_user;
GRANT EXECUTE ON FUNCTION a7.set_tenant(text, boolean) TO app_user;
GRANT SELECT ON customers, partner_customer_map, agents, statutory_rules, kb_chunks TO app_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON outbox, audit_log, incidents, answer_cache,
      approvals, escalations, kb_suggestions, partner_map_suggestions TO app_user;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO app_user;

-- pii_vault: app_user (the gate) may only write. Only a7_act may read (ciphertext; the KMS key policy
-- additionally limits kms:Decrypt to the act task role). REVOKEs are explicit so re-runs fix older databases.
REVOKE ALL ON pii_vault FROM PUBLIC;
REVOKE ALL ON pii_vault FROM app_user;
REVOKE ALL ON pii_vault FROM a7_act;
GRANT INSERT, UPDATE, DELETE ON pii_vault TO app_user;
GRANT USAGE ON SCHEMA public TO a7_act;
GRANT SELECT ON pii_vault TO a7_act;

-- ---------------------------------------------------------------- Phase 4B: answer cache (box 7), kill switch
-- answer_cache holds ONLY human-approved, masked drafts, keyed by (tenant, topic, region) + embedding.
-- Hit rule (solution/a7/cache.py): cosine >= 0.92 AND same topic AND same region AND not expired. RLS = tenant filter.
ALTER TABLE answer_cache ADD COLUMN IF NOT EXISTS region           text;
ALTER TABLE answer_cache ADD COLUMN IF NOT EXISTS citations        jsonb NOT NULL DEFAULT '[]';
ALTER TABLE answer_cache ADD COLUMN IF NOT EXISTS source_ticket_id text;
ALTER TABLE answer_cache ADD COLUMN IF NOT EXISTS approved_by      text;
ALTER TABLE answer_cache ADD COLUMN IF NOT EXISTS expires_at       timestamptz;   -- 7 days default (ours, unsourced)
CREATE INDEX IF NOT EXISTS answer_cache_lookup ON answer_cache (tenant_id, topic, region);
CREATE UNIQUE INDEX IF NOT EXISTS answer_cache_ticket ON answer_cache (tenant_id, source_ticket_id);

-- kb_chunks.updated_at moves only when content/validity actually changes, so a no-op load_kb re-run flushes nothing.
ALTER TABLE kb_chunks ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

CREATE OR REPLACE FUNCTION a7.flush_answer_cache(p_topic text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE prev text := current_setting('app.runner', true);
BEGIN
  PERFORM set_config('app.runner', 'kb_flush', true);      -- lets the kb_flush policy below see every tenant's rows
  DELETE FROM answer_cache WHERE topic = p_topic;
  PERFORM set_config('app.runner', coalesce(prev, ''), true);
END $$;

CREATE OR REPLACE FUNCTION a7.kb_chunks_touch() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    PERFORM a7.flush_answer_cache(OLD.topic);
    RETURN OLD;
  ELSIF TG_OP = 'INSERT' THEN
    NEW.updated_at := now();
    PERFORM a7.flush_answer_cache(NEW.topic);
    RETURN NEW;
  END IF;
  IF (OLD.topic, OLD.region, OLD.title, OLD.body, OLD.effective_from, OLD.effective_to, OLD.superseded_by, OLD.last_reviewed, OLD.tenant_scope)
     IS DISTINCT FROM
     (NEW.topic, NEW.region, NEW.title, NEW.body, NEW.effective_from, NEW.effective_to, NEW.superseded_by, NEW.last_reviewed, NEW.tenant_scope) THEN
    NEW.updated_at := now();
    PERFORM a7.flush_answer_cache(OLD.topic);
    IF NEW.topic IS DISTINCT FROM OLD.topic THEN PERFORM a7.flush_answer_cache(NEW.topic); END IF;
  ELSE
    NEW.updated_at := OLD.updated_at;
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS kb_chunks_touch ON kb_chunks;
CREATE TRIGGER kb_chunks_touch BEFORE INSERT OR UPDATE OR DELETE ON kb_chunks
  FOR EACH ROW EXECUTE FUNCTION a7.kb_chunks_touch();

-- only the flush function (app.runner = 'kb_flush', transaction-local) sees all tenants' cache rows for DELETE
DROP POLICY IF EXISTS kb_flush ON answer_cache;
CREATE POLICY kb_flush ON answer_cache FOR DELETE USING (current_setting('app.runner', true) = 'kb_flush');

-- Kill switch (box 11-12): `auto_send_enabled` row. Missing row or any read error = OFF (fail closed).
-- Flipped only through the reviewer API POST /admin/flags (admin token). Env A7_AUTO_SEND=false can force OFF, never ON.
CREATE TABLE IF NOT EXISTS feature_flags (
  name        text PRIMARY KEY,
  enabled     boolean NOT NULL DEFAULT false,
  updated_by  text,
  updated_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO feature_flags (name, enabled, updated_by) VALUES ('auto_send_enabled', false, 'schema-default')
  ON CONFLICT (name) DO NOTHING;
GRANT SELECT, INSERT, UPDATE ON feature_flags TO app_user;
