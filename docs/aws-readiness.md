# AWS readiness for PayNest A7

## Summary

- Account 554559231166 on the FREE plan: USD 140 credits live as of 2026-11-08 (it was 100 at sign-up), status ACTIVE, expires 2027-05-07.
- Region: eu-north-1. Checked on 2026-11-08.
- Probe rows: 12 PASS, 1 BLOCKED with a fallback (Bedrock Evaluations).
- Gate 0 validation: `infra/probes/results/gate0_validation.md`.
- Total probe cost recorded in the result files: about USD 0.08.

## Results

Each row is one probe group from the Phase 0b table in the plan. Result files are in `infra/probes/results/`.

| Group | Status | Evidence | Fallback / note | Result file |
|---|---|---|---|---|
| Account | PASS | Plan FREE, ACTIVE, USD 140 credits live (100 at sign-up). Fargate On-Demand vCPU quota 12. RDS DB instances 40. ElastiCache nodes per Region 300. | None | p1_account.json |
| Bedrock LLMs | PASS | Claude Haiku 5.5 and Sonnet 5.5 denied: "not available for this account" (TPM quota 0). Nova Lite, Qwen3 235B and Qwen3 32B pass en, ar, hi, Hinglish and the enum tool. | Claude 5.5 denied, Claude 4.x needs the use-case form; in-Region Nova Lite / Qwen3 chosen. See Models chosen. | p1_bedrock_llm.json, p1_model_candidates.json |
| Bedrock embeddings | PASS | eu.cohere.embed-v4:0 returns 1024 dims for en, hi, ar and control. en-ar similarity 0.6742, control 0.2471. | Titan Text Embeddings V2 also passed (1024 dims). | p1_bedrock_embed.json |
| Bedrock Evaluations | BLOCKED-with-fallback | CreateEvaluationJob: AccessDeniedException "Your account is not authorized to invoke this API operation." The probe's S3 bucket and IAM role were deleted. | DeepEval judge, cross-checked by a second Bedrock model. | p1_bedrock_eval.json |
| Database | PASS | RDS PostgreSQL 17.11. pgvector 0.8.2. HNSW nearest-neighbour query uses the index. tsvector query returns 1 row. RLS: app_user sees 2 rows as T1 and 0 rows in the T2 lookup. | None | p3_rds_pgvector.json |
| Cache | PASS | Valkey 9.0 over TLS. SET, GET with TTL 30, and DELETE all work (53 ms). | None | p3_valkey.json |
| Containers | PASS | ARM64 hello image pushed to ECR. Task A (0.5 vCPU) and task B (2 vCPU / 8 GB) both RUNNING. | None | p2_network_compute.json |
| Decider hosting | PASS | AgentCore Runtime reachable (0 runtimes). SageMaker reachable (0 endpoints). 4 SageMaker quotas read. | Default host: ECS Fargate 2 vCPU / 8 GB. | p1_decider_hosting.json |
| Messaging and scheduling | PASS | SQS send, receive and delete work. A one-shot EventBridge Scheduler schedule fired and "scheduled-hello" arrived. The schedule deleted itself. | ECS RunTask target not exercised. | p2_sqs_scheduler.json |
| Network | PASS | VPC vpc-090b40bf8fdd946e5 with 2 private subnets, no internet gateway, no NAT. 8 interface endpoints. A Fargate task reached Bedrock (BEDROCK_OK) and SQS (SQS_OK). ALB target healthy. | None | p2_network_compute.json |
| Security | PASS | KMS encrypt and decrypt round trip match=True. Secrets Manager create and describe work. Probe secret deleted. Probe KMS key in PendingDeletion. | None | p1_security.json |
| Ops | PASS | Logs Insights count=1 over 5 events. Budget create and describe (USD 20). Cost Explorer read works (7-day total 0 USD). | None | p1_ops.json |
| IaC | PASS | CDK bootstrap: stack CDKToolkit CREATE_COMPLETE, bootstrap version 33. | None | p2_cdk_bootstrap.json |

## Models chosen

The user decided these on 2026-11-08. Values come from `solution/a7/config.py` and `p1_model_candidates.json`.

| Role | Model ID | USD per 1M tokens (in / out) | Quota (RPM / TPM) |
|---|---|---|---|
| Cheap | eu.amazon.nova-lite-v1:0 | 0.065 / 0.26 | On-demand 10 RPM / 200,000 TPM (cross-region 20 RPM / 400,000 TPM) |
| Hard | qwen.qwen3-235b-a22b-2507-v1:0 | 0.22 / 0.88 | 100 RPM / 100,000,000 TPM |
| Fallback | qwen.qwen3-32b-v1:0 | 0.15 / 0.60 | 100 RPM / 100,000,000 TPM |
| Embeddings | eu.cohere.embed-v4:0 (1024 dims) | 0.12 / 0 | Cross-region 20 RPM / 300,000 TPM |

Why Claude is not used:
- Claude 5.5 is "not available for this account." Its token quota is 0, so calls cannot run.
- Claude 4.x (Haiku 4.5, Sonnet 4.6) needs the Anthropic use-case form filled in the Bedrock console first. Even then, it allows only 10 requests per minute.

Note: `p1_bedrock_llm.json` predates the decision; see its `superseded_by` field. This table follows `config.py`.

## Limits that affect the build

- Nova Lite is slow to scale. Its on-demand limit is 10 requests per minute, so the cheap tier can be throttled under load.
- The Fargate on-demand vCPU quota is 12 in total.
- Valkey took 656,334 ms (about 11 minutes) from create to available.
- RDS took 400,554 ms (about 7 minutes) from create to available.
- Bedrock Evaluations is not available. The probe was denied, and the free-plan notes say it will not launch in eu-north-1.
- Near the spend limit, the free-plan notes say a spend-limit policy applies. It denies create actions such as CreateDBInstance, CreateCacheCluster, CreateService, CreateRepository and CreateSchedule. It also denies Bedrock and SageMaker invoke calls. The dollar amount and the trigger point are not recorded.
- The free-plan notes do not record what happens to running resources when credits run out.
- The free-plan notes say cross-Region features are not supported. The `eu.` Nova Lite and Cohere profiles still passed the probes.

## How to re-run a probe

Command pattern:

```
AWS_PROFILE=paynest uv run --python 3.12 --with "boto3[crt]" infra/probes/<file>.py
```

- Add `--cleanup` to a probe to run its cleanup path. The probe scripts accept this flag. Check the script's argument parser before relying on it.
- The `[crt]` extra is needed because the profile uses login credentials, and the CRT extra supports them.
- Probes create billable resources for a few minutes. The data run (`p3_data.py`) took about 20 minutes of wall time, and the network run about 8.6 minutes.

Recorded cost per probe (USD, from each JSON's `est_cost_usd`):

| Probe file | Cost (USD) |
|---|---|
| p1_account.py | 0.00 |
| p1_bedrock_llm.py | 0.00035 |
| p1_bedrock_llm_candidates.py | 0.01 |
| p1_bedrock_embed.py | 0.000007 |
| p1_bedrock_eval.py | 0.00 |
| p1_decider_hosting.py | 0.00 |
| p1_security.py | 0.00 |
| p1_ops.py | 0.01 |
| p2_cdk_bootstrap (run by hand) | 0.00 |
| p2_network_compute.py | 0.046 |
| p2_sqs_scheduler.py | 0.00 |
| p3_data.py (RDS, Valkey and runner; three JSON files from one run) | 0.015 |

## What stays in the account

- The CDKToolkit bootstrap stack. It is CREATE_COMPLETE, bootstrap version 33, with qualifier hnb659fds. It is tagged project=paynest.
- One KMS key, 43c53102-ad91-4e38-98a8-16a0b188b46a, in PendingDeletion, DeletionDate 2026-11-15T15:27:11Z.
- Gate 0 tag sweep: 16 tagged ARNs in eu-north-1, 0 live (all stale index entries). A us-east-1 tag query is denied by the project SCP; nothing was created there.
