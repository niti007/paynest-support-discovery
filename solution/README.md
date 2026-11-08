# Data layer (Phase 1A)

```bash
docker compose up -d                       # postgres :55432 + valkey :56379 (schema auto-applies on first start)
uv sync
uv run python -m solution.a7.db apply      # re-apply schema to an existing volume (idempotent)
uv run python -m solution.ingest.load_reference
uv run python -m solution.ingest.load_kb --embed      # Bedrock eu.cohere.embed-v4:0 (default); --no-embed for offline
uv run pytest solution/tests -m "db or not aws"       # needs compose up + loaders run
uv run pytest solution/tests -m aws                   # calls Bedrock (profile paynest, eu-north-1)
docker compose --profile langfuse up -d    # optional Langfuse v3 (web :53000); copy .env.example to .env first
```

DSNs: `A7_DATABASE_URL` (owner, bypasses RLS; default `postgresql://postgres:postgres@localhost:55432/a7`),
`A7_APP_DATABASE_URL` (`app_user`, RLS enforced). Scope a connection with `solution.a7.db.tenant(conn, "C-1001")`
or `SELECT a7.set_tenant('C-1001')`. Unset tenant sees only global KB chunks.

# PII gate, vault, leak scan (Phase 1B)

- `solution/a7/nodes/pii_gate.py` is box 1: regex layer (guarantee) + Presidio/spaCy (names), masks to `<IBAN_1>` style
  placeholders, rescans its own output and **fails closed** (status `blocked`, no text returned) if anything remains.
  `normalise.py` is box 2 (pure; uses an index built from `customers.csv`).
- Vault (`solution/a7/vault.py`): envelope encryption behind a `KeyProvider`. `LocalKeyProvider` reads the master key from
  env or the gitignored file `.a7_vault_key` (generated on first use, never commit). `KmsKeyProvider` is for AWS (Phase 6).
  Rows go to `pii_vault` (ciphertext only). The vault DB test starts its own container on host port 55433 (compose uses 55432).
- Leak scan: `uv run python solution/evals/pii_leak_scan.py` runs the gate over tickets, WhatsApp and call notes and rescans the
  output. Writes `solution/evals/reports/pii_leak_scan.json` (ids and counts only). Exit 1 on any leak.
- Python: `[tool.uv] python-preference = "only-managed"` makes plain `uv run` use uv's own Python 3.12 (Anaconda's Python
  breaks spaCy via h5py). If you pass `--managed-python` explicitly, it is equivalent.
- All imports are `solution.a7...` from the repo root (`pythonpath = ["."]`). Full run: `uv run pytest solution/tests -m "not aws"`.
