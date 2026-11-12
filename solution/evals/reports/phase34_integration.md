# Phase 3 + 4 integration (branch p34-int)

Base d82271e (Phase 3 graph core + p3-int), merged Phase 4 A (dedupe, incidents) then Phase 4 B (cache, kill switch). Stack: compose project `a7int`, Postgres 55482, Valkey 56429, region eu-north-1, profile paynest. Masked text only.

## Merges
Both merges were conflict-free (schema.sql, pyproject.toml/uv.lock, act.py, api/app.py and test_human_loop.py merged automatically; no re-lock needed because the lock file merged cleanly and `uv run` accepted it). Behaviour of both sides kept. `solution.db.bootstrap` re-run: `ticket_embeddings`, `answer_cache`, `feature_flags`, `kb_chunks.updated_at` + flush trigger are in the schema.

## Wiring
| piece | where | behaviour |
|---|---|---|
| dedupe_cluster | graph.py, after classify, before route | writes parent_ticket_id / incident_id / lane_hint / dedupe; its non-null parent wins, a None never clobbers a value resolve_tenant already set. route rule 3 maps `lane_hint == "incident"` to lane=incident; act already copies parent_ticket_id into the route_queue assignment payload. |
| topic / region | retrieve.py | topic = `kb_chunks.topic` of the best KB hit (the same value the flush trigger and `answer_cache` use); region = tenant region (IN/UAE). |
| cache_lookup | graph.py, after retrieve | only for lanes auto_answer / draft_for_agent (others get `skipped`, lane never changed). Hit: agent skipped, output_guard + human_approval still run. A hit whose guard fails goes to the agent and loses `cache_hit`. Trace carries a `cache_lookup` step. |
| tool cache | tools/__init__.py, set by build_graph | get_payroll_run_status, list_payroll_runs, get_employee_payslip_status, get_tenant_profile go through `ToolCache.call` (30 s, tenant in key, args hashed, masked values); errors (429/404) are never stored; Valkey down falls through to the live call. Tenant lock via InjectedState unchanged. `get_statutory_rule` and `search_kb` are not cached (date-dependent). |
| act.py | unchanged merge + one line | Phase 3 behaviour, DB-flag auto_send (env can only force OFF), `store_approved` after an approved draft_for_agent, attach to dedupe's incident. A cache-served answer is not re-stored unless edited. |
| test_compose | honours COMPOSE_PROJECT_NAME / COMPOSE_FILE / A7_VALKEY_URL | same assertions, now pass on `a7int`. |

## Routing policy change (user decision 2026-11-12), own commit
Nova Lite classifies every ticket in one call (category, urgency, injection, priority); `auto_threshold` 0.0 (key kept); Qwen3-32B is the fallback when Nova throttles/errors/returns no valid category; the used backend is in the classify trace (`backend`, `tried`) and route_reasons (`classify_fallback:...`); both failing gives human_review (`classify_failed`). Injection guard and is_injection unchanged. Priority is asked with the SLA 3.2 text verbatim (P1: "payroll cannot be run, or employees will not be paid on time, or statutory filing will be missed"), not the customer's wording; if the model gives no valid priority the old code rule is the fallback. No keyword rules. The Strands Decider is out of the graph's runtime path and tests (its code, `decider/` and the bake-off stay; `test_decider.py` / `test_laya.py` stay marked `model`). Old policy kept as `config.HYBRID_POLICY_2026_11_09` (test_policy uses it).
Numbers: see the new section in `solution/evals/reports/bakeoff.md` (routing eval 300 ids; safety is a SAMPLE of n=60 injection + n=20 benign, seed 20261108):
- auto-routed 99.7% (old hybrid 12.5%); auto-lane category accuracy 80.3% (240/299) vs 100% on the 12.5%; Nova alone at Gate 2 82.0% (n=300, now 80.0%, same ids, run-to-run noise plus the extra priority question).
- Priority: P1 16 / P2 140 / P3 144. P1 recall is weak: 0 of 2 against the only P1 labels in the data (SLA-breach file; just 29 eval tickets appear there, 37.9% exact agreement) and 2 of 10 against the old-code proxy. The export's own `prio` is the customer's urgency wording, so there are no export P1 labels.
- Safety sample: injection caught (model flag or guard, sent to a human) 83.3% (50/60); 10 of 60 injection tickets auto-routed (old hybrid: 2 of 60); false positives 2 of 20 benign (both the instruction-like guard).

## Gate run (real models only: Nova routes, Qwen3-32B fallback; Qwen3-235B drafts on the hard tier). Full output: phase34_gate_run.md
| ticket | expected | result | verdict |
|---|---|---|---|
| PN-100402 | draft_for_agent + P1, cites R-UAE-WPS-2026-07 (15 Jul 2026) | draft_for_agent, Statutory-Compliance, **P2**, statutory, cites R-UAE-WPS-2026-07 / 2026-07-15, guard passed, interrupt + restart + resume OK | lane and citation as expected; **priority P2, not P1** |
| PN-101055 | route_queue Payroll-Ops + status tool | route_queue Payroll-Ops, P2, a read-only status tool called (get_payroll_run_status in the gate run; list_payroll_runs in one pytest run) | as expected |
| PN-101333 | auto_answer shadow, KB-0105, no PII | **route_queue Payroll-Ops, P3**, category payroll (0.9), cites KB-0105, no PII | **differs**: Nova labelled a How-To as payroll |
| PN-101129 | block, 0 tool calls | block, 0 tool calls | as expected |
Not tuned on these four. With a test-only oracle classifier (labels a human gives) the same graph gives A = draft_for_agent P1 and C = auto_answer shadow, so boxes 6-10 meet the expectations; the two differences come from the classifier. `cache_lookup` appears in every trace after retrieve. Cost of the real gate run USD 0.0026.

## Checks on the integrated graph (`solution/evals/p34_checks.py`, results in phase34_checks.json)
- **Cache end to end: PASS.** Ticket 1 (How-To, C-1004, topic `payslip`, region UAE) drafted by the real agent, guard passed, reviewer approved, act stored it (`cache: stored`, one `answer_cache` row). Ticket 2 (same text, new id, same tenant): `cache_lookup` HIT, similarity 0.9999, `cached_from` ticket 1, steps `... retrieve, cache_lookup, output_guard` (agent skipped, guard ran), draft identical, lane auto_answer unchanged. Kill switch OFF (default): the only outbox row is `reply / shadow / shadow=true`; nothing queued or sent.
- **Test-only step, stated plainly:** ticket 1 was forced into draft_for_agent (`route.decide_lane` patched) and the classifier was a labelled How-To stub. See open issue 1.
- **Meridian through the whole graph: PASS.** Real classify (Qwen3-32B), real dedupe on the cached Cohere embeddings, real route; first four Meridian incident tickets are route_queue, the **5th, PN-101239, has lane=incident**, one `incidents` row, 8 tickets attached after 9 replayed. (Draft LLM scripted: lane, not draft, is under test.)

## Tests
`uv run pytest solution/tests -m "not model"`: 280 passed + test_p3_gate 2 passed (aws, real Bedrock) = **282 passed, 0 failed**; the model-marked tests (local Decider / Laya, 2 deselected) were not run because the Decider is no longer in the path. The Meridian Gate 4 test ran (embedding cache rebuilt with `dedupe_corpus.py --embed`, about USD 0.005). Earlier in the session test_p3_gate failed twice on LLM variance (the status tool chosen for PN-101055 was `list_payroll_runs`, and one oracle run); the assertion now accepts either read-only status tool.
New tests: test_p34_wiring.py (6), classify fallback/priority/threshold tests in test_p3_nodes.py, policy tests pinned to the hybrid.

## Open issues
1. **The cache can never be filled by the live routing rules.** Only an approved `draft_for_agent` How-To is stored, but a non-P1 How-To routes to auto_answer (no human approval) and a P1 is never cacheable, so the write path is unreachable without a route change (for example a human-approved human_review How-To, or shadow auto_answer drafts a reviewer approves). The e2e above had to force the lane. Needs an orchestrator/user decision; I did not change routing rules.
2. Classifier quality with Nova alone: PN-101333 (How-To) goes to a payroll queue; P1 recall is low (priority is the weak spot); injection auto-routing rose to 10 of 60 in the sample. The safety sample is not a full-set number.
3. Kestrel PNJ-4412 never becomes an incident via dedupe; linking by Jira key in route is still open (Phase 4 A recommendation).
4. `bakeoff.md` is regenerated by `bakeoff.py`; the new dated section is appended by `routing_policy_eval.py report` and would be lost if bakeoff.py is rerun.
5. A long `routing_policy_eval run` stalled once (no CPU, no output) and was restarted; it is resumable.
