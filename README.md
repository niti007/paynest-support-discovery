# Ramco Payce Support Discovery: a teaching case study

*Ramco Payce is a real company (https://www.ramco.com/products/payce). The support data, people and interviews in this case are a realistic simulation built for teaching and do not represent Ramco's actual data, staff or customers.*

 An HR and payroll SaaS company (Ramco Payce, India and UAE) wants "a ChatGPT chatbot to cut support headcount by 30%" and does not know how. Learners receive a raw, messy client **data room** and must **find the real problem** before they design anything: Discover, Diagnose, Architect, Solutionise.

Everything in the repo is a simulation. All people, tickets, incidents, circulars and numbers are invented for teaching, and every identifier (Aadhaar-like, PAN, Emirates ID, IBAN, IFSC, phone) is deliberately fake.

> **Status:** Phase 1 (data room, problem statement, answer key). The trainer Claude Doc and the reference solution code are separate later phases.

## Layout
```
Case.md   learner-facing brief (client's framing + data room index)
data-room/             the raw client material learners work from
generator/generate.py  seeded generator for every machine-generated data file
trainer/               TRAINER ONLY: ANSWER_KEY.md and analysis.py
```

## For students
1. Read `Case.md`, then `data-room/00_README_from_client.md`.
2. Work through the four phases. Do **not** open `trainer/`.
3. The data is intentionally imperfect. Clean it yourself and document your assumptions.



