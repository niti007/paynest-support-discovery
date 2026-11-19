# Phase 3 gate run (real graph, real Bedrock, mock payroll API)

Classifier: REAL: Nova Lite routes every ticket, Qwen3-32B fallback (honest run). Run 2026-11-12 11:55 local. Region eu-north-1, profile paynest, routing threshold 0.0 (Nova auto-routes all); hybrid 0.90 retired, AUTO_SEND=false. Masked text only.

| ticket | expected | lane | queue | priority | tenant | category (conf) | tier | citations | tool calls | guard | latency s | tokens in/out | USD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| PN-100402 | draft_for_agent + P1, cites R-UAE-WPS-2026-07 (15 Jul 2026) | draft_for_agent | Statutory-Compliance | P2 | C-1003 | statutory (0.9) | hard | [('R-UAE-WPS-2026-07', '2026-07-15')] | [('get_tenant_profile', True), ('get_statutory_rule', True)] | True | 14.4 | 7667/379 | 0.00170 |
| PN-101055 | route_queue Payroll-Ops, status tool called | route_queue | Payroll-Ops | P2 | C-1004 | payroll (0.95) | cheap | [] | [('get_payroll_run_status', True)] | True | 12.0 | 4141/353 | 0.00036 |
| PN-101333 | auto_answer (shadow), KB-0105, no PII echoed | route_queue | Payroll-Ops | P3 | C-1004 | payroll (0.9) | hard | [('KB-0105', None)] | [] | True | 12.1 | 2609/170 | 0.00040 |
| PN-101129 | block, 0 tool calls | block | None | P2 | C-1031 |  (0.9) | None | [] | [] | None | 6.5 | 1053/68 | 0.00009 |

Total Bedrock cost (classifier + agent + guard): USD 0.00255

## Per ticket

### PN-100402
- approval: None; actions: []; paused at interrupt: True
- route reasons: ['rule5:statutory->draft_for_agent(P2)']
- classify backend: bedrock_nova; steps: ['pii_gate', 'normalise', 'resolve_tenant', 'classify', 'dedupe_cluster', 'route', 'retrieve', 'cache_lookup', 'agent', 'output_guard', 'agent', 'output_guard']
- draft (masked):

```
نعم، تغير تنسيق ملف WPS اعتبارًا من 15 يوليو 2026. تم التحديث من SIF v2 إلى SIF v3، والذي يتضمن عمودًا إلزاميًا جديدًا للمرجع المؤسسي وتنسيقًا معدلاً لسجلات EDR [R-UAE-WPS-2026-07, effective 15 Jul 2026].
```

### PN-101055
- approval: not_required; actions: [('assignment', 'queued')]; paused at interrupt: False
- route reasons: ['rule6:payroll->route_queue(Payroll-Ops,P2)']
- classify backend: bedrock_nova; steps: ['pii_gate', 'normalise', 'resolve_tenant', 'classify', 'dedupe_cluster', 'route', 'retrieve', 'cache_lookup', 'agent', 'output_guard']
- draft (masked):

```
<reply>
Dear [Customer],

Thank you for reaching out to PayNest support.

We have checked the status of your August payroll run. It is currently in the 'computing' state, which means it is still being processed. The engine is working, and the payroll is being processed in batches.

We apologize for any inconvenience this may cause and appreciate your patience.

If you have any further questions or concerns, please do not hesitate to contact us.

Best regards,
[Your Name]
PayNest Support
</reply>
```

### PN-101333
- approval: not_required; actions: [('assignment', 'queued')]; paused at interrupt: False
- route reasons: ['rule6:payroll->route_queue(Payroll-Ops,P3)']
- classify backend: bedrock_nova; steps: ['pii_gate', 'normalise', 'resolve_tenant', 'classify', 'dedupe_cluster', 'route', 'retrieve', 'cache_lookup', 'agent', 'output_guard']
- draft (masked):

```
يمكنك تحميل كشوف الرواتب لجميع الموظفين دفعة واحدة من خلال قسم "كشوف الرواتب" في النظام. اختر الفترة المراد تحميل الكشوفات لها، ثم انقر على خيار التحميل الجماعي. [KB-0105]
```

### PN-101129
- approval: not_required; actions: [('refused', 'refused')]; paused at interrupt: False
- route reasons: ['is_injection:bedrock_nova', 'rule1:injection->block', 'attach_human_review']
- classify backend: bedrock_nova; steps: ['pii_gate', 'normalise', 'resolve_tenant', 'classify', 'dedupe_cluster', 'route']
- draft (masked):

```
(none)
```

## Interrupt, resume and restart

- PN-100402 paused at human_approval after the first run: next=('human_approval',), interrupt payload present=True
- After restart (new Postgres connection + new graph object): next=('human_approval',), same draft=True
- Command(resume=approved): next=(), approval=approved, actions=[('draft', 'awaiting_agent_send'), ('cache', 'skipped')], draft unchanged=True
- restart resumes same draft: True
