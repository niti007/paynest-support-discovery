# Discovery Call 6: Meera Iyer, Engineering Manager, Payroll Platform

*Recorded on Google Meet, 9 Oct 2026, 15:00 IST. Duration 33 min.*

**[00:00:06] Meera:** I'll say it plainly so we don't waste time. If the proposal involves a bot with write access to payroll or to the contract system, the answer is no.

**[00:00:21] Interviewer:** Understood. What's the history?

**[00:00:28] Meera:** The May payslip leak. Support tooling is now read-only everywhere and I'm not reopening that. If an LLM can call "create run" or "approve" or "refund", then someone's prompt-injected message can too. After the incident my VP signed off on read-only only. I'd have to get him to sign something else and he won't.

**[00:00:53] Interviewer:** What read access do you allow?

**[00:01:00] Meera:** And before you ask, no, I won't give support a 'break glass' write token either. Everyone asks. I've had that conversation four times since June.

**[00:01:14] Interviewer:** Who asked?

**[00:01:21] Meera:** Priya, once. Sandeep, twice. Your predecessor, once. All nice people with a real problem.

**[00:01:29] Meera:** Tenant profile, run list, payslip with the audit trail, and statutory rates. The run status endpoint is, well, it's rate limited. Thirty requests a minute per tenant at the gateway. It reads the live engine queue, I don't want it hammered. It isn't in the public spec as a number, there's a vague line in the description. Support's macro killed it twice in August.

**[00:01:59] Interviewer:** Is there a lighter alternative?

**[00:02:06] Meera:** Webhooks for run completed, run failed, bank file generated. If you listened for those you wouldn't poll at all. But that's for the customer's integration, nobody built it for support.

**[00:02:22] Meera:** The gateway limit by the way is per tenant, per token. Thirty a minute. So if one customer's chat triggers a lookup per message in a busy chat, with a few agents on the same tenant, you reach it quickly.

**[00:02:41] Interviewer:** What happens at the limit?

**[00:02:48] Meera:** Four-two-nine. And our on-call gets a page after three in a row. That's why I got annoyed. At two a.m.

**[00:03:00] Interviewer:** The statutory parameters endpoint. Does it hold the current values?

**[00:03:07] Meera:** Yes. The engine is the source of truth. `/statutory/rates` takes a region and an as-of date. We update it on the effective date of the circular. We did it on the first of August for the PF ceiling and the fifteenth of July for the WPS layout.

**[00:03:29] Meera:** On statutory rates, there's one gotcha. The rates endpoint returns the value effective on the as-of date. If you don't pass a date, it returns today's. If someone asks about a payslip from July, you need to pass July. A KB article can't do that. It only has one version.

**[00:03:53] Interviewer:** Is the history kept?

**[00:04:00] Meera:** Yes, back to 2022. It's in the contract DB export too. Not everything. Do not assume.

**[00:04:10] Interviewer:** Who tells Support?

**[00:04:17] Meera:** We publish release notes and we mail the support-all list. Whether anybody reads them, I don't know. Honestly, the KB isn't our job. Support should not be answering statutory questions from a wiki. They should be asking the engine.

**[00:04:36] Meera:** Honestly, the release notes are not great. Engineers write them. I read the PF one this morning and I had to read it twice. If I were an L1 agent I'd skip it.

**[00:04:53] Interviewer:** Would a shorter summary for support help?

**[00:05:00] Meera:** Ask the people who'd read it. I only know that my team doesn't write for that audience.

**[00:05:10] Interviewer:** So if a support bot is grounded on the KB?

**[00:05:17] Meera:** Then it'll be wrong the day after a circular. That's not an AI thing, it's a content thing. But I'm not the one who'd be on the hook. I also don't love the customer data going to an outside model. Ask Karthik.

**[00:05:38] Meera:** One more thing on the idea of grounding on tickets. Resolved tickets are not truth. They are what an agent said at the time. If you index them, you index mistakes. I'd at least want the date and the product version next to every answer.

**[00:05:59] Interviewer:** Are there tickets you'd trust more?

**[00:06:06] Meera:** The ones that closed with a Jira link and a fix. Those were verified by someone. The rest, I don't know.

**[00:06:18] Interviewer:** Peak load: when do you see the most traffic on the API?

**[00:06:26] Meera:** 25th to the 2nd. Month-end triples our baseline. If support starts calling our API on every chat message, I'll rate limit support's token, and I told Priya as much.

**[00:06:41] Meera:** Kestrel by the way, I have been more than patient. They keep opening tickets when their own supervisors double-enter. Half of it is their configuration. The other half is ours. 2.7.3 is out in two weeks I think. Or three.

**[00:07:01] Interviewer:** Which half is more?

**[00:07:08] Meera:** Don't write that down. *(laughs)* Seriously.

**[00:07:15] Interviewer:** Kestrel's bug?

**[00:07:22] Meera:** PNJ-4412. Connector v2.7 dropped an idempotency key on retries. Fix is in 2.7.3, waiting for customer UAT. It's not a support problem. It's not payroll week related. It's got nothing to do with how fast agents answer, but I hear support is taking the blame.

**[00:07:44] Interviewer:** The Meridian blank payslips?

**[00:07:51] Meera:** Template cache after a config publish. Fixed in minutes when I finally heard. But I only heard at ten to four. If thirty people write in about the same thing, something should tell me.

**[00:08:08] Interviewer:** If I proposed read-only tools only, plus a human approving anything that changes state?

**[00:08:17] Meera:** *(pause)* Then we can talk. Read-only, scoped tokens, audited, throttled. And I want to see the requests per minute before you ship it.

*[Excerpt ends.]*
