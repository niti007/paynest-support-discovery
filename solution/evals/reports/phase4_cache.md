# Phase 4B: box 7 cache_lookup and kill switch

Base p3-int. Tests: `solution/tests/test_cache.py` plus the updated `test_d_auto_answer_shadow`.
Full suite on stack a7p4b (postgres 55472, valkey 56419, reference data and embedded KB loaded): 201 passed. The one failure, `test_compose.py::test_services_healthy`, looks for the default compose project name; it fails only because this run used project `a7p4b`.

## Design
| Piece | Behaviour |
|---|---|
| Approved-answer cache | pgvector table `answer_cache`. Written only by `act` after a human approved or edited a `draft_for_agent` How-To draft. Masked question and answer. Key = tenant + topic + region + embedding; RLS enforces tenant. |
| Hit rule | cosine >= 0.92 AND same topic AND same region AND not expired. |
| Never cached | not approved; P1; category statutory, any `R-` rule citation or `rule:` route reason; category other than How-To; guard failed; PII or placeholder in the draft; PII in the question; tenant-specific figures (numeric check). |
| Numeric check | amounts and currency, percentages, dates, decimals, any number of 3+ digits. Ids (KB-, PN-, R-) and small integers (step numbers, "24 hours") are allowed. |
| Topic flush | trigger on `kb_chunks` deletes that topic's cache rows (all tenants) when content or validity changes. `updated_at` moves only on a real change, so an unchanged `load_kb` re-run flushes nothing. Python `flush_topic` and `flush_stale` also exist. |
| Status/tool cache | Valkey, 30 s. Key `a7:tool:{tenant}:{tool}:{sha256(args)[:32]}`; args hashed so no PII in keys; values pass `mask_obj`. |
| auto_answer | How-To only; shadow while the kill switch is OFF. |
| Kill switch | `feature_flags.auto_send_enabled`, default false. Env `A7_AUTO_SEND=false/0/off` or `A7_KILL_SWITCH=1` forces OFF; no env value can force ON. No row, no connection or a DB error means OFF. Flipped by `POST /admin/flags` with `A7_ADMIN_TOKEN` (separate from the reviewer token). OFF: act writes `shadow` outbox rows only. |

## Sourced vs ours
Sourced (Session 4 p.7-8, plan box 7 and Gate 4): approved-answers-only semantic cache, topic flush on KB edit, 30 s status cache, never cache P1 statutory or tenant-specific figures, kill switch as a feature flag, nothing auto-sends with it off, shadow mode.
Ours (unsourced): cosine 0.92; 7-day TTL; same-region rule; numeric-check definition; Valkey key shape; Head of Support as the admin-token holder; the separate admin token; env override semantics.

## Test evidence
- Thresholds: 1.0, 0.95, 0.93 hit; 0.91, 0.5 miss. Topic or region mismatch misses.
- Tenant B never gets tenant A's answer (explicit filter and RLS alone).
- 14 never-cache cases, plus small numbers and ids still cacheable, plus edited drafts checked.
- TTL: 6d23h hit, past 7d miss, purge.
- Topic flush: no-op upsert keeps entries; a real edit flushes every tenant for that topic only.
- Valkey: TTL <= 30 s, hashed key, tenant in key, masked values, expiry.
- Kill switch: OFF gives shadow rows; flag ON gives queued; env false/0/KILL_SWITCH forces OFF; env true cannot force ON; fail closed.
- Admin API: no token, reviewer token and wrong token all 401; admin flips and it takes effect; unknown flag and blank actor 422.
