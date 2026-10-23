# Ramco Payce Support Discovery: a teaching case study

*Ramco Payce is a real company (https://www.ramco.com/products/payce). The support data, people and interviews in this case are a realistic simulation built for teaching and do not represent Ramco's actual data, staff or customers.*

A hands-on case for FDE Academy learners. An HR and payroll SaaS company (Ramco Payce, India and UAE) wants "a ChatGPT chatbot to cut support headcount by 30%" and does not know how. Learners receive a raw, messy client **data room** and must **find the real problem** before they design anything: Discover, Diagnose, Architect, Solutionise.

Everything in the repo is a simulation. All people, tickets, incidents, circulars and numbers are invented for teaching, and every identifier (Aadhaar-like, PAN, Emirates ID, IBAN, IFSC, phone) is deliberately fake.

> **Status:** Phase 1 (data room, problem statement, answer key). The trainer Claude Doc and the reference solution code are separate later phases.

## Layout
```
PROBLEM_STATEMENT.md   learner-facing brief (client's framing + data room index)
data-room/             the raw client material learners work from
generator/generate.py  seeded generator for every machine-generated data file
trainer/               TRAINER ONLY: ANSWER_KEY.md and analysis.py
```

## For students
1. Read `PROBLEM_STATEMENT.md`, then `data-room/00_README_from_client.md`.
2. Work through the four phases. Do **not** open `trainer/`.
3. The data is intentionally imperfect. Clean it yourself and document your assumptions.

Students should receive a copy of the repo **without** `trainer/` (and without `generator/`, which contains the generating logic). Zip suggestion:
```
git archive HEAD data-room PROBLEM_STATEMENT.md -o ramcopayce-student-pack.zip
```

## For trainers
- `trainer/ANSWER_KEY.md`: the hidden root cause with exact numbers, red herrings, planted traps (with file and ticket ids), and the mapping from evidence to architecture needs.
- `trainer/analysis.py`: recomputes the proof numbers from the raw data room, using only the files learners see. Its output matches the answer key.
  ```
  python3 trainer/analysis.py
  ```

## Regenerating the data
Python 3.9+, standard library only. From the repo root:
```
python3 generator/generate.py
```
The generator is seeded (`random.seed(42)`) and rewrites these files byte-for-byte identically on every run:

`data-room/data/{tickets_export.json, whatsapp_export.json, call_center_notes.json, sla_breaches_q3.csv, csat_survey.csv, jira_escalations.json, kb_articles.json, customers.csv, agent_roster.csv, monthly_cost_sheet.csv}` and `data-room/integrations/statutory_rules.json`.

All other files (transcripts, SOPs, org chart, Slack export, etc.) are hand-written and are not touched by the generator. If you change the generator, re-run `trainer/analysis.py` and update the numbers in `trainer/ANSWER_KEY.md`.
