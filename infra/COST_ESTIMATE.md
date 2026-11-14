# PayNest A7 demo deploy: cost estimate (eu-north-1, USD)

Unit prices are from the AWS Price List API (read 2026-11-14, eu-north-1, on-demand). Everything else is a rounded estimate.
The account is on the FREE plan with limited credits (about USD 140 live at the last probe), so the plan is **deploy for the gate,
run, destroy**. Nothing here is a quote; Cost Explorer daily spend is the check (Gate 6).

## Unit prices used

| Item | Price |
|---|---|
| Fargate ARM vCPU / GB | 0.0356 / 0.00392 per hour |
| ALB | 0.02394 per hour + 0.0076 per LCU-hour (idle demo: about 1 LCU) |
| Interface VPC endpoint | 0.0105 per hour per AZ-ENI, plus 0.01 per GB processed |
| RDS db.t4g.micro (PostgreSQL, single-AZ) | 0.016 per hour (db.t4g.medium about 0.064) + gp3 20 GB about 0.003 per hour |
| ElastiCache Valkey cache.t4g.micro | 0.016 per hour (cache.t4g.small about 0.032) |
| KMS key / each Secrets Manager secret | 1.00 / 0.40 per month |
| CloudWatch alarm / dashboard | 0.10 per alarm-month / 3.00 per dashboard-month (3 free) |
| Bedrock Nova Lite / Qwen3-235B / Qwen3-32B per 1M tokens (in / out) | 0.065 / 0.26, 0.22 / 0.88, 0.15 / 0.60 |
| Cohere Embed v4 per 1M tokens | 0.12 |

Not charged or negligible: EventBridge Scheduler (free tier is 14M invocations), SQS, SNS, Budgets (the first 2 budgets are free),
CloudWatch Logs at demo volume (ingestion about 0.57 per GB, data protection scanning adds about 0.12 per GB), ECR image storage
(about 0.10 per GB-month, roughly 1.5 GB shared layers). There is no NAT gateway (it would be 0.05 per hour).

## Hourly, default demo deploy (`sizing=dev`, endpoints in 1 AZ)

| Component | USD per hour |
|---|---|
| 8 interface endpoints x 1 AZ (bedrock-runtime, sqs, kms, secretsmanager, ecr.api, ecr.dkr, logs, scheduler) | 0.084 |
| ALB (internal) | 0.032 |
| RDS db.t4g.micro + 20 GB | 0.019 |
| Valkey cache.t4g.micro | 0.016 |
| Fargate api (0.5 vCPU, 2 GB) | 0.026 |
| Fargate mock-payroll (0.25 vCPU, 0.5 GB) | 0.011 |
| Fargate worker (1 vCPU, 3 GB, 0 to 2 tasks; 0.047 per task) | about 0.012 average at 25% duty, 0.095 worst case |
| KMS, 8 secrets, 7 alarms, dashboard (pro-rated) | about 0.009 |
| **Fixed total** | **about 0.21 per hour** |
| One-off tasks (bootstrap 0.5 vCPU, act 0.5 vCPU, smoke 0.25 vCPU) | cents per run |

**About USD 0.21 per hour, USD 5.0 per day, USD 150 per 30 days if forgotten.** Worst case with two busy workers: about 0.29 per hour.
`sizing=doc` (db.t4g.medium, cache.t4g.small, endpoints in 2 AZs) is about 0.38 per hour (USD 9 per day).

Bedrock is per use, not per hour: at most USD 0.02 per draft (Gate 5 limit), so a 50-ticket Gate 6 run is about USD 1 and the
bootstrap embeds 28 KB chunks for under USD 0.01.

## Recommended deploy window

1. Pre-flight on the laptop (free): `uv run pytest`, `cdk synth -c nag=true`, `cdk diff`, local image builds.
2. Deploy, bootstrap, gate run, collect evidence, destroy: **about 3 hours** of paid time.
   - Valkey needs about 11 minutes to create and RDS about 7, in parallel; image publish and first deploy about 25 minutes in total.
   - Bootstrap/ingest task about 3 minutes. Gate 6 flows (one ticket per lane, approval, P1 escalation, log mask check) about 1 hour.
   - Cost of the window: 3 h x 0.21 = about USD 0.65, plus Bedrock about USD 1, so **under USD 2 for a Gate 6 session**.
3. Run `cdk destroy --all` the same day. A demo to a class is the same pattern (deploy 30 minutes before, destroy after).
4. Never leave it up overnight: 24 h is USD 5, a week is USD 35.
5. Cost Explorer lags by a day, so the AWS Budget (alerts at 50/80/100% of USD 20) is the safety net, not the meter.

## Why the endpoint set is the minimum

Interface endpoints are the largest fixed line (0.084 of 0.21 per hour) because there is no NAT. All eight are needed:
ecr.api + ecr.dkr (image pull; layers come from S3 through the free gateway endpoint), logs (awslogs driver), secretsmanager
(ECS secrets, bootstrap), sqs (worker, Scheduler), kms (vault GenerateDataKey/Decrypt), bedrock-runtime (all model calls), scheduler
(escalation timers created from the worker/api). Dropping `scheduler` is possible only if escalations use the DB poller, which has
no runner on AWS. **Single AZ for the demo is safe**: tasks in the other AZ reach the endpoint ENIs across AZs (private DNS returns the
one ENI), at a cross-AZ data charge of 0.01 per GB, which is cents. The only cost of single AZ is no endpoint redundancy, which a demo
does not need; `sizing=doc` uses both AZs. The ALB must span two AZ subnets either way (an ALB requirement, not extra cost per AZ).

## Free-plan notes

Near the spend limit the plan denies create actions (CreateDBInstance, CreateService, CreateSchedule, Bedrock invoke); an
alarm at 80% of the budget therefore matters more than the 100% alert. The credits balance is in AWS Settings, Billing.
Destroy removes billable resources; the CDK asset images in the bootstrap ECR repository stay (about USD 0.15 per month).
