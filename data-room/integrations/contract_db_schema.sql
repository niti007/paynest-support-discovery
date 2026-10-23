-- Ramco Payce Contracts DB (PostgreSQL 14). Read replica available to Support Ops.
-- Source of truth for tiers, fees and SLA terms. Owner: Finance Systems.

CREATE TABLE customers (
  customer_id        VARCHAR(10) PRIMARY KEY,         -- e.g. C-1001
  legal_name         TEXT NOT NULL,
  region             CHAR(3) NOT NULL CHECK (region IN ('IN','UAE')),
  csm_email          TEXT,
  onboarded_on       DATE,
  status             VARCHAR(12) DEFAULT 'active'     -- active | trial | churned
);

CREATE TABLE contracts (
  contract_id        SERIAL PRIMARY KEY,
  customer_id        VARCHAR(10) REFERENCES customers(customer_id),
  tier               VARCHAR(12) NOT NULL,            -- Enterprise | Business | Standard
  template_version   VARCHAR(8) NOT NULL,             -- v2.x templates carry a 25% monthly credit cap; v3.x have none
  monthly_fee_usd    NUMERIC(10,2) NOT NULL,
  employees_licensed INT,
  starts_on          DATE NOT NULL,
  ends_on            DATE,
  sla_override_json  JSONB                            -- negotiated deviations from the template, free-form
);

CREATE TABLE sla_terms (
  tier               VARCHAR(12),
  priority           CHAR(2),
  first_response_h   NUMERIC(5,1),
  resolution_h       NUMERIC(5,1),
  credit_pct         NUMERIC(4,1),
  PRIMARY KEY (tier, priority)
);

CREATE TABLE sla_credits (
  credit_id          SERIAL PRIMARY KEY,
  customer_id        VARCHAR(10) REFERENCES customers(customer_id),
  ticket_ref         VARCHAR(12),                     -- PN-xxxxxx, text, not enforced
  invoice_month      CHAR(7),
  credit_usd         NUMERIC(10,2),
  approved_by        TEXT,
  created_at         TIMESTAMP                        -- stored in server time (IST), no timezone
);
-- NOTE: ~3% of customers have rows in `contracts` with overlapping date ranges after mid-term upgrades.
-- Use the row with the latest starts_on.
