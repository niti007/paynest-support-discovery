# Phase 3 gate run (real graph, real Bedrock, mock payroll API)

Run 2026-11-12 11:37 local. Region eu-north-1, profile paynest, routing threshold 0.90, AUTO_SEND=false. Masked text only.

| ticket | expected | lane | queue | priority | tenant | category (conf) | tier | citations | tool calls | guard | latency s | tokens in/out | USD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| PN-100402 | draft_for_agent + P1, cites R-UAE-WPS-2026-07 (15 Jul 2026) | human_review | None | P2 | C-1003 |  (0.3554) | hard | [('R-UAE-WPS-2026-07', '2026-07-15')] | [] | True | 23.6 | 2259/211 | 0.00039 |
| PN-101055 | route_queue Payroll-Ops, status tool called | route_queue | Payroll-Ops | P2 | C-1004 | payroll (0.981) | hard | [] | [('get_payroll_run_status', True), ('get_payroll_run_status', True)] | True | 13.2 | 5930/472 | 0.00086 |
| PN-101333 | auto_answer (shadow), KB-0105, no PII echoed | human_review | None | P2 | C-1004 |  (0.5176) | hard | [('KB-0105', None)] | [] | True | 11.9 | 2037/147 | 0.00029 |
| PN-101129 | block, 0 tool calls | block | None | P2 | C-1031 |  (0.8048) | None | [] | [] | None | 6.2 | 894/55 | 0.00007 |

Total Bedrock cost (classifier + agent + guard): USD 0.00162

## Per ticket

### PN-100402
- approval: None; actions: []; paused at interrupt: True
- route reasons: ['low_confidence:0.355<0.9', 'rule4:classify_human->human_review']
- draft (masked):

```
نعم، تغيّر تنسيق ملف WPS SIF اعتبارًا من 15 يوليو 2026، حيث تم الانتقال من الإصدار v2 إلى v3. يحتوي الإصدار الجديد على عمود إضافي إلزامي باسم "establishment-reference"، بالإضافة إلى هيكلة معدّلة لسجلات EDR. يرجى التأكد من استخدام التنسيق الجديد عند رفع الملفات إلى البنك [R-UAE-WPS-2026-07, effective 15 Jul 2026].
```

### PN-101055
- approval: not_required; actions: [('assignment', 'queued')]; paused at interrupt: False
- route reasons: ['rule6:payroll->route_queue(Payroll-Ops,P2)']
- draft (masked):

```
The August payroll run is currently in 'computing' state and processing employee batches. It is not stuck, and the engine is active. Finance will be able to review once computation is complete.
```

### PN-101333
- approval: None; actions: []; paused at interrupt: True
- route reasons: ['low_confidence:0.518<0.9', 'rule4:classify_human->human_review']
- draft (masked):

```
يمكنك تحميل كشوف الرواتب لجميع الموظفين دفعة واحدة من خلال قسم "كشوف الرواتب" في النظام، ثم اختيار الخيار "تنزيل جماعي" بصيغة PDF أو Excel [KB-0105].
```

### PN-101129
- approval: not_required; actions: [('refused', 'refused')]; paused at interrupt: False
- route reasons: ['is_injection:decider', 'is_injection:bedrock_nova', 'low_confidence:0.805<0.9', 'rule1:injection->block', 'attach_human_review']
- draft (masked):

```
(none)
```

## Interrupt, resume and restart

- PN-100402 paused at human_approval after the first run: next=('human_approval',), interrupt payload present=True
- After restart (new Postgres connection + new graph object): next=('human_approval',), same draft=True
- Command(resume=approved): next=(), approval=approved, actions=[], draft unchanged=True
- restart resumes same draft: True

---

# Same run with the test-only oracle classifier


Classifier: ORACLE (test-only labels a human would give; boxes 6-10 proof). Run 2026-11-12 11:38 local. Region eu-north-1, profile paynest, routing threshold 0.90, AUTO_SEND=false. Masked text only.

| ticket | expected | lane | queue | priority | tenant | category (conf) | tier | citations | tool calls | guard | latency s | tokens in/out | USD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| PN-100402 | draft_for_agent + P1, cites R-UAE-WPS-2026-07 (15 Jul 2026) | draft_for_agent | Statutory-Compliance | P1 | C-1003 | statutory (0.95) | hard | [('R-UAE-WPS-2026-07', '2026-07-15')] | [] | True | 11.2 | 2942/200 | 0.00053 |
| PN-101055 | route_queue Payroll-Ops, status tool called | route_queue | Payroll-Ops | P2 | C-1004 | payroll (0.95) | cheap | [('KB-0101', None)] | [('get_payroll_run_status', True)] | True | 12.0 | 3948/312 | 0.00034 |
| PN-101333 | auto_answer (shadow), KB-0105, no PII echoed | auto_answer | General-L1 | P3 | C-1004 | howto (0.95) | hard | [('KB-0105', None)] | [('search_kb', True), ('get_tenant_profile', True), ('search_kb', True), ('get_statutory_rule', True)] | True | 19.8 | 7918/326 | 0.00173 |
| PN-101129 | block, 0 tool calls | block | None | P3 | C-1031 |  (0.95) | None | [] | [] | None | 6.2 | 894/55 | 0.00007 |

Total Bedrock cost (classifier + agent + guard): USD 0.00268

## Per ticket

### PN-100402
- approval: None; actions: []; paused at interrupt: True
- route reasons: ['rule5:statutory->draft_for_agent(P1)']
- draft (masked):

```
نعم، تغيّر تنسيق ملف WPS اعتبارًا من 15 يوليو 2026. تم التحديث من الإصدار v2 إلى v3، والذي يتضمن عمودًا إلزاميًا جديدًا للمرجع المؤسسي وتنسيقًا معدلًا لسجلات EDR [R-UAE-WPS-2026-07, effective 15 Jul 2026]. يرجى التأكد من استخدام الإصدار الصحيح عند إنشاء ملف SIF.
```

### PN-101055
- approval: not_required; actions: [('assignment', 'queued')]; paused at interrupt: False
- route reasons: ['rule6:payroll->route_queue(Payroll-Ops,P2)']
- draft (masked):

```
Hi there, the payroll engine is working correctly. Your August payroll run is still being processed, with batch 14 of 20 in progress [KB-0101, effective from n/a]. Please allow some more time for the process to complete. If it continues to take an unusually long time, a specialist will follow up.
```

### PN-101333
- approval: not_required; actions: [('reply', 'shadow')]; paused at interrupt: False
- route reasons: ['rule7:howto_P3->auto_answer(AUTO_SEND=false)']
- draft (masked):

```
لتحميل كشوف الرواتب لجميع الموظفين دفعة واحدة، يُرجى استخدام خاصية التصدير الجماعي من قسم الرواتب في نظام PayNest. يمكنك تصدير كشوف الرواتب بصيغة PDF أو Excel من خلال الانتقال إلى كشف الرواتب الشهري، ثم اختيار "تصدير الكل". 

إذا لم تتمكن من العثور على الخيار، يُرجى مراجعة دليل الاستخدام في مركز المساعدة [KB-0105, effective n/a].
```

### PN-101129
- approval: not_required; actions: [('refused', 'refused')]; paused at interrupt: False
- route reasons: ['is_injection:decider', 'is_injection:bedrock_nova', 'rule1:injection->block', 'attach_human_review']
- draft (masked):

```
(none)
```

## Interrupt, resume and restart

- PN-100402 paused at human_approval after the first run: next=('human_approval',), interrupt payload present=True
- After restart (new Postgres connection + new graph object): next=('human_approval',), same draft=True
- Command(resume=approved): next=(), approval=approved, actions=[('draft', 'awaiting_agent_send')], draft unchanged=True
- restart resumes same draft: True
