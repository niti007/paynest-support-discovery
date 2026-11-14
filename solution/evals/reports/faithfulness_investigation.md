# Faithfulness investigation (user decision 2026-11-14)

Question: with the gate set to the stricter of two judges (min of Qwen3-235B and Nova Lite per draft, threshold 0.9), mean faithfulness was **0.784** and 38% of
drafts were >= 0.9. Are the drafts really unfaithful, or is the measurement wrong?

Method: the 10 lowest min-score drafts of the 40-draft sample (`p5_faith_sample_ids.json`, scores in `p5_deepeval_scores.json` before this work) were re-judged by
Nova Lite with DeepEval `FaithfulnessMetric(include_reason=True)` (masked text only; `solution/evals/faith_investigate.py`). For each draft the extracted claims, verdicts
and reasons were read against the retrieved chunks, the ticket and the tool calls, and every claim the judge did not mark "yes" was classified by hand:

- **REAL**: the draft states something that is in no source it was given.
- **JUDGE ERROR**: the claim is supported (or is boilerplate such as "a specialist will follow up"); the judge is wrong.
- **CONTEXT GAP**: the claim is correct, but the evidence passed to the judge was incomplete (tool outputs or the ticket text were not in `retrieval_context`).

## Finding 0: the Nova judge is noisy

The same draft, same judge, temperature 0, scored differently between the gate run and this investigation run: PN-101055 0.00 then 1.00, PN-100794 0.25 then 1.00,
PN-101320 0.50 then 0.50, PN-101333 0.50 then 0.50, PN-100193 0.29 (both). So a good part of the "lowest 10" is judge variance, not draft quality.
DeepEval only counts verdict `no` against a draft (`borderline`/`idk` count as faithful), and Nova flips between `no` and `borderline` for the same sentence.

## Per-claim classification (only claims not marked `yes`)

| draft | claim (shortened) | Nova verdict | class | why |
|---|---|---|---|---|
| PN-101055 | "system connectivity issue" retrieving the payroll run status | borderline | CONTEXT GAP | all four `get_payroll_run_status` calls failed; the tool error was not in the judge's context |
| PN-101055 | "our technical team is investigating" | borderline | REAL | no source says so; the draft invents an operational fact |
| PN-101055 | "a specialist will follow up shortly" | borderline | JUDGE ERROR | boilerplate the system prompt asks for |
| PN-100794 | "PayNest applies this updated ceiling for PF calculations" | borderline | JUDGE ERROR | follows from R-IN-PF-2026-08 (retrieved) and the `get_statutory_rule` result |
| PN-100794 | "differences with prior computations may arise if older wage limits were used" | borderline | REAL | generic advice, in no source |
| PN-100453 | "employer contribution restricted to 12% of INR 15,000 unless the joint option is exercised" | no | JUDGE ERROR | KB-0142 + KB-0143 say exactly this |
| PN-100453 | "the same cap applies to the employee's contribution unless the joint option is in place" | no | JUDGE ERROR | KB-0142: employees above 15,000 contribute on actual wage only through the joint option |
| PN-101320 | "effective since 15 July 2026" | no | JUDGE ERROR | the judge objected to "since" versus "from" |
| PN-101320 | mandatory column "رقم مرجعي للمؤسسة" | no | JUDGE ERROR | Arabic translation of "establishment-reference column" (R-UAE-WPS-2026-07); judge compared English to Arabic strings |
| PN-101320 | reference is R-UAE-WPS-2026-07 | no | JUDGE ERROR | the chunk id is the reference; the judge read "Source: Central Bank / MoHRE notice" as the reference |
| PN-101329 | PF on up to INR 25,000 for a 37,000 basic | borderline | JUDGE ERROR | direct reading of the ceiling in R-IN-PF-2026-08 |
| PN-101333 | download all payslips in one batch through the "Payroll" section, as PDF or Excel | no | **REAL** | KB-0105 has a title only ("Downloading payslips in bulk"); the menu name and the formats are invented |
| PN-100023 | PF = 12% of INR 15,000 for a 20,000 basic | no | JUDGE ERROR | KB-0142 says exactly this |
| PN-100032 | PF = 12% of INR 15,000 for a 58,000 basic; joint option allows actual wage | no | JUDGE ERROR | KB-0142 / KB-0143 |
| PN-100193 | format changed as of 15 July 2026 / "moved from v2 to v3" (2 claims) | no | JUDGE ERROR | the ticket was received 2026-07-20, after the rule's effective date; Nova read it as "not yet in force" |
| PN-100193 | v3 has a mandatory establishment-reference column; revised EDR layout; 14 to 16 columns; bank should expect v3 (4 claims) | no / borderline | JUDGE ERROR | all four are written in the rule chunk (title, Was, Now) |
| PN-100193 | change is documented in [R-UAE-WPS-2026-07] | no | JUDGE ERROR | same misreading of the citation as in PN-101320 |
| PN-100802 | "the support team is available if needed", "the message is from PayNest Support" | borderline | JUDGE ERROR | greeting / sign-off boilerplate |

Tally (claims not marked `yes`, 25 in total): **REAL 3**, **CONTEXT GAP 1**, **JUDGE ERROR 21**.

Two real draft-quality problems that are not faithfulness verdicts: PN-100802 contains `<reply>` tags, "[Your Name]" and a leaked "effective_from=n/a" field dump
(prompt fix below), and in PN-101333 the KB chunk is title-only, which tempts the model to invent steps.

## What this means

The 0.784 is mostly the measurement, not the drafts: 21 of 25 non-supported claims are judge errors (Nova Lite misreads citations, translations and dates, and treats
greetings as claims), 1 is a missing-evidence artefact and only 3 are real. The 3 real ones all have the same shape: the draft goes beyond the sources (invented
operational fact, generic advice, steps for an article whose body is only a title).

## Fixes (by cause)

| cause | fix | where |
|---|---|---|
| REAL | The drafting prompt now says: state only facts that are written in the chunks, tool results or ticket; end every factual sentence with its source id; no generic advice, no "why figures may differ", no claims about what PayNest or its team is doing; a title-only chunk gets its id plus "a specialist will send the details"; a failed tool call gives no facts; no `[Your Name]`, `<reply>` or field dumps. | `solution/a7/nodes/agent.py` SYSTEM |
| CONTEXT GAP | The agent keeps the (already masked) tool outputs as `tool_evidence`; the eval harness passes retrieved chunks + tool outputs + the masked ticket as `retrieval_context`. | `agent.py`, `state.py`, `p5_harvest.py`, `test_deepeval_quality.case()` |
| JUDGE ERROR | Documented here. **Not special-cased**: no allow-list of boilerplate, no judge prompt change, no per-draft override. Threshold 0.9 and the min-of-two rule are unchanged. | - |

## Re-measurement (same 40 ids, drafts regenerated with the tightened prompt)

RESULT_PLACEHOLDER
