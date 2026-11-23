# PayNest A7 Phase 2 results card (for trainers)

## What was compared
Five ways to decide a ticket's category, urgency and "is this a prompt injection?":
the Decider (a local model), Laya and Laya-calibrated (cheap in-process models), and two
Bedrock models (Qwen3-32B and Nova Lite). All were scored on the same routing eval set
(1061 tickets, 300 shared by every backend), plus a 60-ticket injection safety set.

## Winner
Nova Lite: 82.0% category accuracy on the shared 300 tickets, best calibration (ECE 0.084),
and about $0.57 a month. Qwen3-32B was 77.3%; the Decider only 53.3% on the same tickets.

## Chosen policy (user decision, 2026-11-09)
- If the Decider's confidence is 0.90 or higher, the ticket is auto-routed to that queue.
- Otherwise a human triages it, with Nova's suggested category attached.
- Anything the injection guard or either model flags as injection always goes to a human.
- Threshold history: 0.80 was tried first (auto share 32.1%, accuracy 81.8%, n=341) and rejected.

## Numbers at 0.90
- Auto share 12.5% (133 of 1061); auto-lane accuracy 100.0% (n=133). This clears the 95% target.
- Human share 87.5%; Nova's suggestion is right 79.6% of the time on those (n=265).
- By language, auto-lane accuracy is 100% for en (n=89), ar (n=16) and hinglish (n=28); hi has no auto tickets.

## Exceptions the user accepted
1. No backend beats the keep-initial baseline (84.7%), so routing leans on the hybrid policy.
2. Injection recall with the guard is 96.7%, not 100%. Two Arabic variants (PN-101129-v19, -v47)
   reach the auto lane; the Decider is 0.917 and 0.932 confident, so a higher threshold will not
   stop them. Auto-routing only picks a queue: nothing is sent without human approval,
   auto_answer is shadow-only, tools are read-only.
3. The injection guard was committed after the safety results existed, so blind authorship
   cannot be proven from git. A content check found 0 shared 6-word phrases with the safety set.

## Cost
- Nova Lite about $0.57 a month at 15,150 tickets (Qwen3-32B $1.31).
- The Decider needs a Fargate task, about $74.75 a month, and is used only for the auto lane.
- Laya runs in-process at about $0.

Full detail: solution/evals/reports/bakeoff.md
