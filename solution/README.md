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
