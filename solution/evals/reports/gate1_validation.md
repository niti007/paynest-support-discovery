# Gate 1 validation (independent), branch aws-a7 @ 6cfd2bc

| Check | Result | Evidence | Notes |
|---|---|---|---|
| G1.1 Compose fresh | PASS | postgres + valkey healthy (`up -d --wait`); `--profile langfuse config -q` exit 0; plain `config --services` = postgres, valkey | no pre-existing volume for this project |
| G1.2 Ingest counts | PASS | expected vs DB: customers 60/60, agents 49/49, statutory_rules 4/4, kb source_ids 24 articles + 4 rules = 28/28, partner_customer_map 35 distinct end_customer / 35; kb_chunks 28, all 28 with vector_dims=1024 | expected computed from data-room with own script |
| G1.3 Tests | PASS | not aws: 55 passed, 0 failed (2 deselected); aws: 2 passed, 0 failed | |
| G1.4 PN-101333 vault | PASS | `<IBAN_1>` and `<EID_1>` present; own-regex residue none; reveal(act) round-trips both (True); agent and normalise raise PermissionError; plaintext not in ciphertext (False) | temp key via env, MemoryVaultStore |
| G1.5 Timestamps | PASS | 10,879 non-null ticket timestamps, 0 mismatches vs trainer parse_ts. Formats: ISO offset 4743, epoch ms 3339, ISO naive T 1890, naive space 483, DD/MM/YYYY HH:MM IST/GST 219, ISO Z 205 (all 6; 106 nulls). call_center called_at: 61/61 parse, 3 sampled OK | normalise.parse_ts is a verbatim copy, so equality is expected |
| G1.6 Customer ref | PASS | PN-100802 and PN-101504 both -> C-1031; "Falcon Trading LLC" -> C-1004; "Falcon" -> None | |
| G1.7 Leaks | PASS | records: tickets 1367, whatsapp 112, calls 61; blocked 0; leaks after gate 0 (trainer PII dict + own email/phone/IBAN/EID/PAN/Aadhaar regexes, all string fields). Tickets with PII before (unique ids, minus "duplicate of" resubmits, text fields): 101 (1327 real). Raw 1367 records give 104 because of 2 resubmits plus duplicate-id rows. pii_leak_scan.py agrees: 101 before, 0 leaks, exit 0 | PERSON placeholders by language: ar 79, en 111, hi 8, hinglish 20 (total 218, matches repo's 218) |
| G1.8 RLS + dated retrieval | PASS | app_user, tenant C-1001: C-1004 rows in customers 0, outbox 0 (owner confirmed a seeded C-1004 outbox row exists; own-tenant rows visible 1/1; seeded rows deleted after). No tenant: customers 0, outbox 0, kb global 28. Supersede links: KB-0201/0202 -> R-UAE-WPS-2026-07, KB-0142/0143 -> R-IN-PF-2026-08. UAE as-of 2026-08-10: R-UAE-WPS, KB-0160, KB-0203 (KB-0201/0202 excluded); as-of 2026-06-01 they return | |
| G1.9 Hygiene | PASS | diff e0de09b..aws-a7: 0 IBAN/EID/PAN/Aadhaar/phone hits; email-regex hits in 8 files are all false positives (decorators, DSN user@localhost, example.invalid). .env and .a7_vault_key are in .gitignore and untracked. pii_leak_scan.json: 0 hits. DB: 10 tables, 0 PII-pattern cells; only pii_vault has a bytea (ciphertext) column | |
| G1.10 Teardown | PASS | `down -v` done; no a7-b-pg container; git status clean | leftover (not deleted): volume `agent-ab626fdfee9e770d2_a7_pgdata` |

GATE 1: PASS
