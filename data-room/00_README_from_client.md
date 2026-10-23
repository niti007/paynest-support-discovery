# Ramco Payce: Support Data Room

*Ramco Payce is a real company (https://www.ramco.com/products/payce). The support data, people and interviews in this case are a realistic simulation built for teaching and do not represent Ramco's actual data, staff or customers.*

Hi,

Thanks for taking this on. Our CEO asked me to pull together "everything" so you can see how Support works today. I've tried to be complete, but it was assembled in a hurry from several systems, so expect rough edges. Please don't assume any of it has been cleaned.

## What we want
Leadership wants **a ChatGPT-style chatbot for customer support that lets us reduce support headcount by 30%**. Nobody here has built something like this before, and we are not sure what we should be asking for. We'd like your recommendation.

## Things you should know about the data
- The ticket export covers **Q3 2026 (1 July to 30 September)** for our **managed accounts** (customers with named account managers and SLA contracts, plus some Standard accounts). It is **not** all of our ticket volume. The cost sheet has the full-company numbers.
- Our helpdesk has a **different export format for each channel**, and WhatsApp and phone notes come from other systems entirely. I merged what I could.
- **Timestamps are mixed.** Some systems store IST, some store the customer's local time (UAE customers are on GST), some store UTC or epoch milliseconds, and a few are saved with no timezone at all. I believe the naive ones are IST, but nobody has confirmed that.
- **Category labels** are typed or picked by customers, agents and partners, so you will see several spellings of the same thing.
- A few tickets appear twice. We think this is because of the export script and because customers sometimes write twice.
- Customers paste all sorts of things into tickets. We did not scrub them. Please treat the data as confidential and do not copy it to any public service. (All values in this pack are synthetic, but handle them as if real.)
- `first_response_at` is sometimes blank. We don't know why.
- The SOPs in `process/` are our official documents. I can't promise they describe what happens day to day.
- The interview transcripts were auto-transcribed; speaker attribution is mostly right.

## What's in the folder
See `PROBLEM_STATEMENT.md` in the repo root for the full index. Each file's header or neighbouring document explains where it came from.

## Who to ask
Neha Joshi (Support Ops) for data questions; my office for interview scheduling. We would rather you ask than guess.

Thanks again,
Aditi Rao, Chief of Staff to the CEO
