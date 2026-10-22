# Post-mortem: ConversoAssist Pilot (April-June 2026)

**Author:** Neha Joshi (Support Ops), reviewed by Priya Nair  |  **Status:** circulated to leadership 2 July 2026

## Summary
PayNest ran a managed AI-assistant pilot from 6 April to 28 June 2026 on the in-app widget for 140 customer accounts. The pilot was stopped early. The vendor's dashboard reported a 38% "deflection rate"; our own review found it was not safe to extend.

## What happened
1. **Setup.** The vendor ingested our Confluence KB (the 24 published articles) on 28 March, and pointed the assistant at it. There was **no re-sync job**. We were told "KB updates can be pushed manually on request".
2. **Wrong numbers.** From April, customers received answers quoting the **previous financial year's TDS slabs** (our KB article was updated on 8 April; the vendor's copy was not). In May, the assistant told a UAE customer an end-of-service gratuity figure that is not in any of our articles, with a confident citation. Support found 11 conversations in a sample of 200 with a factual statutory error.
3. **Latency.** Normal response time was 3-4 seconds. During the **25th-28th May** peak, the 95th percentile rose to **19 seconds** and 6% of sessions timed out. Customers reopened as tickets: "bot not responding". The vendor said it was "scaling".
4. **Handoff.** When the assistant could not answer it created a ticket with a one-line summary and no chat transcript, so agents asked customers to repeat themselves.
5. **No access to customer data.** The assistant could not see a customer's contract tier or payroll run state. It answered "it may take 24-48 hours" to a P1 salary-not-credited question from an Enterprise customer.
6. **Cost.** The platform fee was predictable. Usage was not. June cost USD 41,200, of which the "premium model surcharge" was USD 24,890 and was applied automatically at peak. No cap existed in the contract.
7. **Data handling.** The vendor's sub-processor list included a US region. Our DPO had not been consulted before the pilot began.

## What was measured
| Metric | Vendor dashboard | PayNest review |
|---|---|---|
| Deflection rate | 38% | "Not comparable": 41% of "deflected" sessions were users abandoning after a wrong or empty answer |
| Median latency | 3.1 s | 3.4 s outside peak; 8.6 s on 25th-28th |
| Wrong-fact rate (sample of 200) | not reported | 5.5% (11 of 200) |
| CSAT of conversations | 4.2 | 3.6 (our survey link) |

## Decision
Pilot terminated; contract not renewed. CFO has asked that any future proposal come with a monthly spend cap.
