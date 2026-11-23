# Gate 6 live validation (AWS eu-north-1, profile paynest, 2026-11-16 20:55-21:10 GMT, about 15 min)

Verdict: PASS-with-warnings (no FAIL).

| Item | Result | Evidence |
|---|---|---|
| G6.1 Health | PASS | Both ALB target groups (api, mock-payroll) healthy; `smoke health` = 200 {"status":"ok"} (/readyz); api, mock-payroll steady (1/1); worker scaled 0->1 at 20:54:17 (queue-depth alarm), back to 0 at 21:00:59, and 0->1 again for PN-100270 (sent 21:04:32, task started about 21:05, done 21:07:51); max 1 |
| G6.2 Lanes | PASS (queue name WARN) | table below; 0 payroll calls for 101333 and 101129 |
| G6.3 Approval | PASS | `decide` 200 approved; actions draft awaiting_agent_send outbox_id 6; flags: auto_send_enabled false, env_forces_off true, effective false; Act task exit 0, printed counts only |
| G6.4 P1 escalation | PASS-partial (WARN) | No P1 path under Nova (PN-100402, 100270, 101055, 101129 all P2; 101333 P3). Proven: Scheduler one-time schedule created per timer (CloudTrail), fires, worker runs escalation.handler; backstop `a7-run-due` rate(30 minutes) ENABLED, `{"run_due": true}`, group paynest-a7-dev, and run_due ran at 20:54:45 |
| G6.5 Logs PII | PASS | IBAN, PAN, EID, Aadhaar, phone: 0 matches in all 6 groups (292 records scanned; control count per group below). Data protection ACTIVATED on all 5 app groups. No AWS/Logs LogEventsWithFindings datapoints (0 masked findings). WARN: the audit policy has no FindingsDestination, so there are no audit findings to read |
| G6.6 Cost | WARN | Cost Explorer daily 11-14, 11-15, 11-16 = 0 (Estimated=True, lags up to 24h, credits). Budget paynest-a7-dev-monthly USD 20 MONTHLY exists; 3 ACTUAL notifications at 50, 80, 100% -> SNS. Alarms: all OK except the scaling alarm below |
| G6.7 DLQ and crashes | PASS | DLQ 0 visible, 0 in flight; main queue 0 after processing; no crash loops (service events: only steady state or start/stop by scaling); stopped tasks are one-shot Smoke/Act/Bootstrap, exit 0 (non-zero smoke exits were my 404 lookups and argparse typos) |

Alarm note: `...WorkerServiceTaskCountTargetQueueDepthUpperAlarm` is in ALARM (reason: 2 datapoints >= 1.0 at 21:04-21:05, the queue-depth scale-out trigger, expected). All others OK. `paynest-a7-dev-dlq-depth` OK.

## Per-ticket (as observed on AWS, worker already warm unless noted)
| Ticket | Tenant | Lane | Priority | Category | Citations | Tool calls | Latency (send to done) |
|---|---|---|---|---|---|---|---|
| PN-100402 | C-1003 | draft_for_agent (paused for approval) | P2 (accepted exception) | statutory | [R-UAE-WPS-2026-07, effective 15 Jul 2026] in the Arabic draft | 0 | sent 20:56:15, done 20:56:25 (about 10 s) |
| PN-101055 | C-1004 | route_queue | P2 | payroll | n/a | 2 mock-payroll GETs (payroll-runs?month=2026-08, then RUN-C-1004-2026-08/status), both 200 | done 20:56:31 (about 6 s after previous) |
| PN-101333 | C-1004 | route_queue (accepted exception) | P3 | payroll | n/a | 0 | done 20:56:37 (about 6 s) |
| PN-101129 | C-1031 | block | P2 (default) | "" | n/a | 0 | done 20:56:40 (about 3 s) |
| PN-100270 (extra, cold start from 0 workers) | C-1016 | draft_for_agent | P2 | statutory | [KB-0142], [KB-0143] | 0 | sent 21:04:32, done 21:07:51 (about 3.3 min incl. scale-out) |

WARN: the route_queue name "Payroll-Ops" is not visible through smoke or the logs (`/tickets/<id>` gives 404 for route_queue tickets and `/shadow` is empty), so I could not confirm it. Lane and priority are from the worker `done` lines.
Incident batch (Meridian C-1002): smoke has no batch command, SKIPPED.

## Escalation detail
- PN-100402 timer: kind l1_to_l2, due_at 2026-07-29 (historic, already overdue), fired_at 2026-11-16T20:57:43. Schedules a7-esc-1/2/3/5 were created via CloudTrail CreateSchedule at(now+60s), ActionAfterCompletion DELETE, group paynest-a7-dev, target the ingest queue. Worker logs: `escalation fired [1],[3],[2]` at 20:57:43-55 and `fired [5]` at 21:09:26 (PN-100270). The a7-esc-* schedules self-delete, so only a7-run-due is listed.
- A genuine P1 timer (SLA clock) could not be tested: no ticket routes P1. Overdue-on-creation timers are the safe way to force the handler.
- Escalation id 4 has no CreateSchedule in the lookup window (not investigated).

## Commands that worked
```
export AWS_PROFILE=paynest AWS_REGION=eu-north-1
out(){ aws cloudformation describe-stacks --stack-name "$1" --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text; }
C=paynest-a7-dev; NET=$(out $C-compute RunTaskNetworkConfiguration); QUEUE=$(out $C-data IngestQueueUrl)
# README run() breaks with an empty $2 in zsh: use an if/else, quote --overrides. smoke wrapper:
smoke(){ T=$(run SmokeTaskDefinition "$(python3 -c "import json,sys;print(json.dumps({'containerOverrides':[{'name':'smoke','command':['python','-m','solution.a7.smoke']+sys.argv[1:]}]}))" "$@")"); aws ecs wait tasks-stopped --cluster $C --tasks $T; echo ${T##*/}; }
# output: aws logs get-log-events --log-group-name /paynest/a7/dev/api --log-stream-name smoke/smoke/<taskid> --query 'events[].message' --output text
aws sqs send-message --queue-url $QUEUE --message-body '{"ticket_id":"PN-100402"}'
smoke health | flags | queue --tenant C-1003 | ticket --tenant C-1003 --ticket PN-100402 | decide --tenant C-1003 --ticket PN-100402 --status approved --reviewer demo
run ActTaskDefinition '{"containerOverrides":[{"name":"act","command":["python","-m","solution.a7.sender","--tenant","C-1003","--ticket","PN-100402"]}]}'
# tenants: PN-100402 C-1003, PN-101055 C-1004, PN-101333 C-1004, PN-101129 C-1031, PN-100270 C-1016
aws logs tail /paynest/a7/dev/worker --since 15m --format short | grep "sqs_worker done"
# Logs Insights: aws logs start-query --log-group-names <5 app groups + /aws/rds/instance/paynest-a7-dev-db/postgresql> --start-time <epoch 20:38Z> --end-time now --query-string '<README query>'; get-query-results
aws cloudtrail lookup-events --lookup-attributes AttributeKey=EventName,AttributeValue=CreateSchedule
aws budgets describe-budget / describe-notifications-for-budget --account-id <id> --budget-name paynest-a7-dev-monthly
```
Notes: `smoke ticket` returns 404 for route_queue and block tickets (only drafts are stored); a Smoke run takes about 45-60 s. Log groups scanned (records): api 95, mock-payroll 60, act 1, rds postgresql 30, worker 83, bootstrap 23.
