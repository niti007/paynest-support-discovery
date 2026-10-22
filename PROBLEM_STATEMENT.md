# PayNest Support: Problem Statement

*Written by the client. This is the brief we give you, in our own words. It is deliberately not a specification.*

## 1. About PayNest
PayNest is a mid-size HR and payroll SaaS company serving about 1,400 business customers in **India and the UAE**. Roughly **180,000 employees** are paid each month through our platform. Customers use us to run payroll, calculate and file statutory deductions, issue payslips and manage leave and attendance. Larger customers have contracts with service-level commitments, and miss-penalties are paid as credits.

Our support organisation has **42 agents** in three tiers (L1, L2, L3) across Pune, Bengaluru and Dubai. We use a helpdesk tool, Jira for engineering escalations, a Confluence knowledge base and Slack. Customers reach us by email, an in-app widget, a partner/reseller portal, WhatsApp and phone.

## 2. How support works today (as we understand it)
A customer raises a ticket and picks a category. The ticket goes to the matching team's queue. An L1 agent answers using the knowledge base. Harder cases go to L2, and product defects go to L3 and into Jira. Our SOPs in `data-room/process/` describe this in detail. Payroll weeks (around the 25th to the 3rd of each month) are our busiest time.

## 3. What leadership wants
Our CEO has asked for **a ChatGPT-style chatbot for customer support that reduces support headcount by 30%.** Other leaders have other asks (for example, a voice bot). We tried a vendor chatbot earlier this year and stopped it.

**We want AI, and we do not know how.** We don't know what to ask for, what it would cost to run, what could go wrong, or whether a chatbot is even the right thing. We want you to tell us.

## 4. Your role
You are a Forward Deployed Engineer. You have been given our "data room": raw exports, internal documents and interview transcripts, exactly as we have them. They are messy, they were assembled in a hurry, and people in the company do not always agree with each other. Do not assume anything has been cleaned or verified.

## 5. Your tasks

### Phase 1: Discover
Work out what is actually going on in Support. Read, query and join the data. Decide what you believe, and what you cannot yet believe.
**Deliverables:** (a) a problem brief of at most two pages: what you found, with evidence references (file and ticket ids); (b) a list of claims made by stakeholders that the data confirms, contradicts or cannot settle; (c) three to five success metrics a CFO would accept; (d) open questions and who you would ask.

### Phase 2: Diagnose
Compare the process we say we follow with what the data shows we actually do.
**Deliverables:** (a) a current-state flow diagram built from the ticket histories; (b) a gap list ranking the gaps between documented and actual practice by their cost to PayNest; (c) a short statement of what you think the problem is, and what you think it is not.

### Phase 3: Architect
Design a target solution that responds to what you found, not to what was asked. You are free to choose any cloud, framework, model provider and language; justify your choices.

**Hints:** think about which data belongs in an operational database (e.g. PostgreSQL, MongoDB, Cosmos DB, DynamoDB) and which knowledge should be searchable by meaning in a vector database (e.g. pgvector, Qdrant, Pinecone, Weaviate, Chroma, Azure AI Search). Look closely at what metadata that knowledge needs to carry.
**Deliverables:** (a) an architecture diagram; (b) for every component, the specific evidence in the data room that makes it necessary, and what would happen without it; (c) a view on safety, privacy and failure behaviour; (d) a cost model for a peak week against our spending constraints; (e) the integration constraints you found and how your design respects them.

### Phase 4: Solutionise
Turn the design into a plan.
**Deliverables:** (a) a phased build plan with the smallest useful first release; (b) an evaluation plan with a test set built from the real tickets; (c) a business case that speaks to the 30% headcount ask honestly, including what you would recommend we do instead or in addition; (d) risks, and how you would roll out and roll back.

## 6. Ground rules
- Treat all data as confidential, even though the values in this pack are synthetic.
- Where our documents disagree with our data, tell us. Where you cannot decide, say so and explain how you would find out.
- Every claim in your deliverables should be traceable to something in the data room.

## 7. Data room index
Everything lives under `data-room/`.

| Path | What it is |
|---|---|
| `00_README_from_client.md` | Our cover note. Read first, including the caveats. |
| `org/org_chart.json`, `org/stakeholders.md` | Who is who, and who wants what. |
| `process/ticket_triage_sop.md` | Triage procedure. |
| `process/escalation_policy.md` | When and how tickets escalate. |
| `process/payroll_week_runbook.md` | What we do in payroll week. |
| `process/sla_contract_excerpt.md` | Service-level tiers and how credits are calculated. |
| `transcripts/01..09_*.md` | Nine discovery calls: CEO, CFO, Head of Support, L2 lead, L1 agent, Engineering manager, DPO, VP Sales, two customers' HR admins. |
| `vendor_pilot/` | Invoice and post-mortem of the chatbot pilot we ran in Q2. |
| `integrations/payroll_api_openapi.yaml` | Our payroll engine's API description. |
| `integrations/contract_db_schema.sql` | Schema of our contracts database. |
| `integrations/statutory_rules.json` | Statutory rule changes, with effective dates. |
| `security/incident_2026_05_payslip_leak.md` | Report on an earlier privacy incident. |
| `comms/slack_support_leads.json`, `comms/email_cfo_thread.md` | Support leads' Slack channel and an email thread with the CFO. |
| `data/tickets_export.json` | Q3 2026 tickets from email, widget and partner portal. Different fields per channel. |
| `data/whatsapp_export.json` | WhatsApp threads. |
| `data/call_center_notes.json` | Phone call notes. |
| `data/sla_breaches_q3.csv` | Finance's list of SLA breaches and credits. |
| `data/csat_survey.csv` | Customer satisfaction survey responses. |
| `data/jira_escalations.json` | Engineering escalations. |
| `data/kb_articles.json` | Knowledge-base article metadata and excerpts. |
| `data/customers.csv` | Customers in the extract: tier, region, fee. |
| `data/agent_roster.csv` | Support agents. |
| `data/monthly_cost_sheet.csv` | Monthly support cost figures from Finance. |
