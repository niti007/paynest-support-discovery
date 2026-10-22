# Incident Report INC-2026-05-02: Payslip disclosed to wrong customer

**Classification:** Personal data disclosure (internal), Severity 2  |  **Report owner:** Karthik Subramanian (DPO)  |  **Date of event:** 14 May 2026  |  **Closed:** 12 June 2026

## Summary
On 14 May 2026 at ~19:40 IST an L1 agent, working a P1 ticket for an Enterprise customer ("payslip not downloading"), attached a payslip PDF to the ticket reply. The PDF belonged to an employee of a **different** customer. The wrong file had been downloaded from the internal admin console, where two browser tabs with different tenants were open during payroll week.

## Impact
- One payslip (name, employee ID, bank account last 4 digits, PAN, net pay) disclosed to one customer HR admin for approximately 31 minutes.
- No evidence of onward disclosure. The customer confirmed deletion in writing.
- The affected employee's employer was notified within 24 hours.

## Contributing factors
1. Admin console allows multiple tenants in parallel sessions and has no visible tenant banner.
2. Agent had been on shift for 11 hours. Payroll-week staffing was below the runbook minimum that day.
3. No second check on attachments for P1 tickets.
4. Tickets and replies are not scanned for personal data.

## Actions
| # | Action | Owner | Status |
|---|---|---|---|
| 1 | Tenant banner in admin console | Engineering | Done 30 May |
| 2 | **Support tooling has read-only access to production payroll data; no write scopes issued** | Engineering | Done 5 Jun |
| 3 | PII scan of the last 90 days of tickets | DPO | Partially complete: found Aadhaar-like numbers, PAN and bank details in ticket bodies |
| 4 | DPIA required before any new system processes ticket content | DPO | Standing requirement |
| 5 | Review of cross-border processing for any third-party AI | DPO | Open |

## Regulatory notes
Assessed under India's Digital Personal Data Protection Act, 2023, and the UAE Federal Decree-Law No. 45 of 2021 (PDPL). Threshold for regulator notification was judged not met; documented rationale on file.
