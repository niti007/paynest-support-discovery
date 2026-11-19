# Gates 3 and 4 validation, reconstructed

Reconstructed from the orchestrator's plan notes on 2026-11-18; the original validator reports were lost. Numbers are as recorded at the time.

This page covers four validator reports that were never copied into this folder: Gate 3, Gate 4, and the two held-out re-validations. Each section gives the commit that was validated, the verdict, the findings and numbers as the plan records them, and the fixes that followed (the plan entries after the verdict). Where a number comes from a different plan entry than the verdict, that source is named.

---

## Gate 3 on ff0096c

- **Commit:** ff0096c (`p34-int`, the Phase 3 and 4 integration).
- **Verdict:** PASS-with-warnings. No FAIL.
- **Context from the plan:** the run was an integration of the Phase 3 and 4 builders with Nova-only routing. The plan records these numbers for ff0096c: 282 tests pass; Nova-only routing 80.0% on category and 99.7% auto-routed; P1 recall is weak (ticket A comes out as P2); ticket C goes to the Payroll-Ops route queue; on the injection sample (60 injection, 20 benign), 10 of 60 injections were auto-routed and 2 of 20 benign tickets were false positives.
- **Decision taken with the verdict:** the user accepted Nova-only routing as is and documented three gaps as Gate 3 exceptions: P1 under-detection, the PN-101333 misroute, and 10 of 60 injections auto-routed. The mitigations stated were AUTO_SEND=false with a kill switch, read-only tenant-locked tools, and a human approving every draft.
- **Warnings:**
  - (a) Output guard false positive: a correct draft that says "16 columns instead of 14" fails the stale check twice, so PN-100402 ends in human_review in 2 of 3 runs. This also explains the relaxed test.
  - (b) `human_db.DEFAULT_ACT_DSN` hard-codes port 55432.
  - (c) The route queue gets no L1-to-L2 timer.
  - (d) No node sets `privacy_concern`, so the privacy timer is dead.
  - (e) app_user has UPDATE on feature_flags, so the kill switch could be bypassed.
  - (f) A flaky test, `test_gate_oracle_classifier`.
- **Fixes:** a fix builder on `p34-fixes` (from `p34-cachefill`), merged at da1aa00. Fixes F1 to F6 map to warnings (a) to (f):
  - F1 (a): the guard is data-driven from "Was:" values plus superseded markers (en, ar, hi). PN-100402 reached draft_for_agent in 3 of 3 strict runs (+20 tests).
  - F2 (b): act DSN fixed.
  - F3 (c): L1-to-L2 timer armed on every lane except block.
  - F4 (d): Nova privacy flag plus a code fallback; 2 of 50 flagged in the sample.
  - F5 (e): `a7_admin` role; app_user can only SELECT flags.
  - F6 (f): oracle test fixed.

---

## Gate 4 on 0a4c8a0

- **Commit:** 0a4c8a0 (`p34-cachefill`, the cache fill).
- **Verdict:** PASS-with-warnings, plus 1 defect.
- **PASS:** the Meridian incident, the resubmits (26 of 26), 0 false incidents, the never-cache rules, tenant isolation, Valkey, the kill switch, cache fill, and PII.
- **Numbers as the plan records them (from the Phase 4 entry, not the verdict itself):** the Meridian incident opened at PN-101239, the 5th ticket. All 26 resubmits were linked, 25 to the right parent. 0 false incidents. The chosen values were W = 2 h, T_inc = 0.55, T_link = 0.75. False links on the eval routing tickets were 27 of 625, mostly identical template twins.
- **Defect:** a no-op `load_kb` upsert flushed the whole cache. The BEFORE INSERT trigger fires before ON CONFLICT, so the flush ran even when nothing changed.
- **Warnings:**
  - The threshold grid was chosen after the eval results were seen (the sweep was extended downward). This is not a blind test, and it was recorded as a teaching point rather than a code fix.
  - A cosmetic `AUTO_SEND` reason in `route.py`.
  - The PN-100402 human_review comes from the guard's `numbers_not_grounded:14`, the same root cause as Gate 3 warning (a).
- **Fixes:** all findings went to the fix builder on `p34-fixes`, merged at da1aa00. The plan records:
  - G1: the flush trigger was fixed and tested with the real loader.
  - G2: the `auto_send_effective` reason was added.
  - F1 (from the Gate 3 fixes) covered the PN-100402 warning.
  - The 321 passing tests were run before 3 more tests were added; the full suite was not re-run after them.

---

## Re-validation 1 of da1aa00: FAIL on R2

- **Commit:** da1aa00 (`p34-fixes`).
- **Verdict:** FAIL on R2. Other items are recorded below.
- **R2 (fail):** 30 of 41 wrong-direction drafts bypassed output_guard. Any superseded marker waived the stale number, for example "Use 14 columns instead of 16", "Previously INR 25,000, now INR 15,000", and the Arabic and Hinglish variants. 0 of 11 genuine corrections were rejected.
- **R6 (warning):** the privacy flag fired when a customer pasted their own PII (PN-101018, PN-100203, CL-202413). 4 of 4 synthetic real incidents were flagged.
- **PASS on everything else:** 321 tests passed, PN-100402 draft_for_agent 3 of 3, DSNs, timers, a7_admin, flush 4 to 4, and `auto_send_effective`.
- **Fixes:** a fix builder on `p34-fixes2`, done at 1ae1eed:
  - A direction-aware guard: a new `stale_values.py` with slot patterns, clause markers, and fail-closed behaviour.
  - The 41 and 11 validator cases plus 32 new cases pass.
  - PN-100402 draft_for_agent 3 of 3.
  - Privacy false positives fixed: 0 of 50 on the untouched seed 20261113; 4 of 4 synthetic incidents flagged.
  - Final run: 409 passed.
- **Next step in the plan:** a held-out adversarial validator on 1ae1eed.

---

## Held-out re-validation 2 of 1ae1eed: FAIL on H1

- **Commit:** 1ae1eed (`p34-fixes2`).
- **Verdict:** FAIL on H1. Other items are recorded below.
- **H1 (fail):** 8 of 57 wrong drafts bypassed the check. All bypasses used number words or shorthand: "fourteen", "15k", "fifteen thousand", "15 thousand", Arabic أربعة عشر, and Hindi चौदह and पंद्रह हज़ार.
- **H1 (warning):** 9 of 27 genuine corrections failed closed. Most had the stale value named as history, or "25k" not grounded against 25,000.
- **H2:** draft_for_agent 3 of 3.
- **H3:** privacy 6 of 6 true positives, 6 of 6 true negatives; the old false positives are fixed.
- **H4:** suite green, with one environment-only failure.
- **Decision taken with the verdict (Opus):** keep the rule-based guard as defence in depth. Statutory drafts always need human approval, so the residual risk is bounded. Fix number normalisation in general rather than adding strings. Add the H1 cases as regression tests. Run one more held-out round. A false-rejection rate of up to about 15% was accepted as a warning, since it fails closed.
- **Fixes:** the same builder, on `p34-fixes3`, done at 560ecb6:
  - A `numbers.py` normaliser: 0 of 57 bypasses and 0 of 27 false rejections, tested against fake chunks.
  - `topic_rules`: `statutory_no_rule_cited` fires only when a rule is in force.
  - PF in-force recall@5 went from 27 of 30 to 30 of 30 via the topic probe.
  - Scanner `mask_ids`; PN-100402 draft_for_agent 3 of 3.
  - Suite: 539 passed.
- **Later round:** the next held-out round on 560ecb6, with the real database chunks, failed. Its report is `gate34_revalidation3.md` in this folder.
