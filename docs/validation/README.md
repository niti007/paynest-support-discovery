# Validator reports

Independent validator reports for the A7 build, copied from the build scratchpad. The AWS account id is written `<account-id>`, local paths are
stripped, and every report was scanned for Aadhaar, PAN, IBAN, Emirates ID, phone, email and AWS-key patterns (0 hits). Dates marked (inferred) are
not written in the report; they come from the seeds and commit ids it cites.

- `gate34_revalidation3.md`: Gates 3 and 4, held-out re-validation round 3 (HEAD 560ecb6), verdict FAIL (6 plain-language bypasses; about 62% false rejection), about 2026-11-14 (inferred); the guard was reworked afterwards (rules plus an LLM check).
- `gate5_validation_part1.md`: Gate 5 part 1 (everything except the stale check and faithfulness), verdict PASS with WARNs, about 2026-11-15 (inferred).
- `gate6_predeploy.md`: Gate 6 pre-deploy infra review (p6b 5123df4), verdict NOT READY (three blockers, all fixed afterwards), about 2026-11-15 (inferred).
- `final_validation.md`: Gate 5 part 2 and Gate 6 pre-deploy recheck (a7-final 395be84), verdict MERGE-READY with warnings and DEPLOY-READY on the user's explicit go, 2026-11-16.
- `gate6_live.md`: Gate 6 live validation on AWS eu-north-1, verdict PASS with warnings, 2026-11-16.

- `gates_3_4_summary.md`: Gate 3 (ff0096c), Gate 4 (0a4c8a0), Gate 3/4 re-validations 1 (da1aa00, FAIL on R2) and 2 (1ae1eed, FAIL on H1); verdicts PASS-with-warnings, PASS-with-warnings plus 1 defect, FAIL, FAIL; about 2026-11-12 to 2026-11-14 (inferred); reconstructed from the orchestrator's plan notes on 2026-11-18, since the original reports were lost.
