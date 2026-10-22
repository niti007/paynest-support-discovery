# Escalation Policy

**Owner:** Head of Support  |  **Version:** 2.6  |  **Last reviewed:** 3 Mar 2025

## Tiers
- **L1**: first-line agents (24 FTE planned). Resolve from KB, categorise, gather information.
- **L2**: senior agents and domain specialists (12). Complex payroll and statutory cases, workarounds.
- **L3**: product-support engineers (6). Reproduce defects, own the Jira escalation, liaise with Engineering.

## When to escalate
| Situation | Action | Time limit |
|---|---|---|
| L1 cannot resolve from the KB | Escalate to L2 with a summary of what was tried | within 1 h for P1, 4 h otherwise |
| Suspected product defect | L2 raises a Jira ticket in project PNJ (component, customer, steps, ticket link) | same day |
| 5 or more customers report the same symptom | Support Lead declares an incident and pages Engineering on-call | immediately |
| Security or privacy concern (data shown to the wrong user, PII exposure) | Notify the DPO and Security within 1 h | 1 h |
| Customer threatens to churn / SLA breach likely | Notify the CSM and Head of Support | immediately |

## Ownership
- Jira tickets are owned by L3 until Engineering accepts. Engineering sets the fix date.
- Support must update the customer at least every 4 hours on P1s and every 24 hours on P2s.

## Not allowed
- Skipping L2 to ping engineers on Slack directly.
- Sharing screenshots containing another customer's data.
