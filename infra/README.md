# PayNest A7 infra (AWS CDK, Python)

Stacks: `paynest-a7-<env>-network`, `-data`, `-ops`, `-compute`, `-monitoring` (Phase 6 B added the last three; see the Phase 6 section).
Region is fixed to **eu-north-1** (the app refuses any other Region). Nothing here is deployed by default.

## Setup and synth

Own project file: `infra/pyproject.toml` (separate from the root `solution/` env). Python 3.12 via uv, CDK CLI via npx.

```
cd infra
uv sync --python 3.12
export CDK_DEFAULT_ACCOUNT=<project account>   # taken from env, never hard-coded
npx -y aws-cdk@2 synth -c sizing=dev           # or -c sizing=doc
uv run pytest                                   # assertion tests, no AWS calls
npx -y aws-cdk@2 synth -c nag=true -q          # cdk-nag AwsSolutions pack
uv run cfn-lint cdk.out/*.template.json
```

Synth needs no AWS credentials: the AZ lookup is pre-seeded in `app.py`. Node 20 prints an end-of-life banner from
jsii; set `JSII_SILENCE_WARNING_END_OF_LIFE_NODE_VERSION=1` or use Node 22+.

Context flags (`cdk.json`, override with `-c`): `region=eu-north-1`, `env_name=dev`, `sizing=dev|doc`, `public_alb=false`, `nag=false`.

| sizing | RDS | Valkey | backups | deletion | interface endpoints |
|---|---|---|---|---|---|
| dev | db.t4g.micro, 20 GB gp3 | cache.t4g.micro, 1 node | 1 day | destroy, no protection, automated backups deleted | 1 AZ |
| doc (Session 4) | db.t4g.medium, 50 GB | cache.t4g.small, 1 node | 7 days | protection on, final snapshot, key retained | 2 AZs |

## NetworkStack
VPC 10.42.0.0/16, 2 AZs, **no NAT gateway**, private isolated `workload` subnets. Public subnets exist only if `public_alb=true`.
Endpoints (9): S3 gateway plus interface endpoints for bedrock-runtime, sqs, kms, secretsmanager, ecr.api, ecr.dkr, logs, scheduler
(all confirmed available in eu-north-1). Security groups (all defined here): alb, api, worker, mock, db, cache, endpoint (the Decider is out of the runtime path, so no decider SG or task).
Ingress: ALB (VPC CIDR :80, plain HTTP, see Accepted demo risks below; or 0.0.0.0/0 :443 when public) -> api :8080; worker/api -> db :5432, cache :6379; worker -> ALB :8081 -> mock :8080;
worker/api/mock -> endpoints :443 (the endpoint SG admits ONLY those three task SGs; no VPC-CIDR rule). The only 0.0.0.0/0 ingress that can exist is the ALB :443 rule.
Decision: **ALB is internal by default**; a public ALB needs WAF and an ACM certificate, which are out of scope.

## DataStack
- One customer KMS key (rotation on): RDS storage, PII-vault data keys, SQS, Valkey at rest.
  Key policy denies direct `kms:Decrypt` to every account role and user except `paynest-a7-<env>-act-task`; AWS services keep working (see "KMS key policy" below).
  Managed policies (stack outputs): `WorkerVaultPolicyArn` = `kms:GenerateDataKey` only; `ActVaultPolicyArn` = `kms:Decrypt`.
  **Contract for ComputeStack:** name the task roles `config.worker_role_name(env)` / `config.act_role_name(env)` and attach those policies.
- RDS PostgreSQL 17.11 (proven in probe P3), private isolated, not public, `ManageMasterUserPassword` (Secrets Manager), CMK-encrypted.
- Generated `app_user` secret (the RLS role). `solution/db/schema.sql` hardcodes `app_user_dev`; the bootstrap must set this secret's password on AWS.
- ElastiCache Valkey 9.0, single node, TLS in transit and at rest.
- SQS ingest queue (maxReceiveCount 5) plus DLQ (14 days), both KMS-encrypted, TLS enforced.
- Tags `project=paynest`, `app=a7` on everything.

### Schema bootstrap (implemented: `solution/db/provision.py`, bootstrap task in ComputeStack)
A **one-off ECS Fargate task** that runs the idempotent `solution/db/schema.sql` from the worker image, then sets the `app_user` password
from the secret. Reasons: it needs a psycopg connection and `CREATE EXTENSION vector` inside the VPC, the image already has both, and a
custom resource would need a VPC Lambda and ties DDL to stack rollback. See `stacks/schema_bootstrap.py` (no resources; Builder B defines the task).

## Cost if left on (USD per month, rough, eu-north-1)
Network and data only; compute (Fargate, ALB, Decider) is in Builder B's stacks.
- Interface endpoints: about 7.5 each per AZ. dev: 8 x 1 AZ = about 60. doc: 8 x 2 = about 120.
- RDS db.t4g.micro about 13 plus 20 GB gp3 about 2.5. Valkey cache.t4g.micro about 10. KMS key 1, two secrets about 1, SQS negligible.
- **dev total about USD 88**; doc about USD 200+.

## Teardown
`npx -y aws-cdk@2 destroy --all -c sizing=dev` (dev: RDS, keys, queues and secrets are destroyed; the KMS key has a 7-day pending deletion).
doc sizing retains the key and takes a final RDS snapshot; delete those by hand. Endpoints are the main cost, so destroy when idle.

## cdk-nag (AwsSolutions)
Clean for dev and doc after suppressions, each with a written reason in `app.py` (`_suppress`): VPC7 flow logs, RDS3/AEC4 single-AZ,
RDS11 default port, SMG4 rotation, AEC6 Valkey AUTH, RDS10 (dev only), and a CdkNagValidationFailure for the CIDR token (EC23 on the ALB SG; it fires, so it stays). Stale suppressions that never fired (ops SNS2, compute EC23) were removed after a per-suppression probe. ComputeStack IAM5 is suppressed per role and per
resource (`applies_to`: the EU-prefix Bedrock foundation-model ARNs, the scheduler group, `ecr:GetAuthorizationToken`), never stack-wide.


---

# Phase 6 B: images, ComputeStack, OpsStack, MonitoringStack (synth-only until the user confirms a deploy)

## Images (`docker/Dockerfile`, build context = repo root, allow-list `.dockerignore`)
One multi-stage file, five `linux/arm64` targets, all `python:3.12-slim`, non-root uid 10001, deps installed by `uv pip install` from
compiled pins (`docker/requirements-*.txt`, made with `uv pip compile` from the versions in the root `uv.lock`), no secrets or `.env` baked in.
`worker` (`python -m solution.a7.sqs_worker`), `api` (uvicorn :8080), `act` (`solution.a7.sender`, same deps), `mock-payroll`, `bootstrap`
(`python -m solution.db.provision`: schema + role passwords + checkpoint tables, then load_reference and load_kb). The runtime images leave out
torch, Laya, Strands, DeepEval, RAGAS and Langfuse (about 635 MB). CDK builds them with `DockerImageAsset` (ARM64) at deploy time.
```
docker buildx build --platform linux/arm64 -f docker/Dockerfile --target worker -t a7-worker .
```

## ComputeStack
ECS cluster (Fargate ARM64, no Container Insights), internal ALB (:80 to api, health `/livez` (no DB; `/readyz` checks the DB and is used by the smoke task); :8081 to mock-payroll), three services:
`api` (1 task), `mock-payroll` (1 task), `worker` (0 to **1** task, step scaling on queue visible + in-flight messages). RunTask-only task
definitions: `bootstrap`, `act`, `smoke` (an in-VPC client, because an internal ALB is unreachable from a laptop).
Secrets (generated, never in the template): a7_act, a7_admin, reviewer token, admin token, mock token; plus `app_user` and the RDS master secret
from DataStack. They reach the containers as ECS secrets; each task definition has its own execution role that reads only its own secrets.

### Act design (decision)
The graph's `act` node only writes outbox rows (as `app_user`; no decryption). Only `act.render_outbound` (send-time PII restore) opens the vault,
so it runs as **its own RunTask definition `act` under role `paynest-a7-<env>-act-task`**, not inside the worker and not by the worker assuming a role.
That role alone has `kms:Decrypt` on the vault key (DataStack's `ActVaultPolicy`) and `GetSecretValue` on the a7_act secret (also the bootstrap role, which
sets the password once; a secret resource policy denies everyone else). The a7_act secret is deliberately not an ECS secret: the act container reads it with
its task role (`A7_ACT_SECRET_ARN`), so no execution role can read it. The worker has `GenerateDataKey` only and no `sts:AssumeRole`.
Exception to "only act has Decrypt": the worker, as an SQS SSE-KMS consumer, has `kms:Decrypt` conditioned on `kms:ViaService=sqs.eu-north-1.amazonaws.com`
(the key policy exempts service-mediated calls and denies every other direct Decrypt except act's). The tests assert exactly that.

### Bedrock scope
InvokeModel / InvokeModelWithResponseStream only, on: `eu.amazon.nova-lite-v1:0` profile + `amazon.nova-lite-v1:0` FM in `eu-*` (conditioned on the profile ARN),
`qwen.qwen3-235b-a22b-2507-v1:0`, `qwen.qwen3-32b-v1:0` (in-Region), `eu.cohere.embed-v4:0` profile + FM. Worker: all; api and bootstrap: embeddings; act, mock, smoke: none.

### Escalation timers (EventBridge Scheduler)
`A7_ESCALATION_BACKEND=scheduler` (set on worker and api) makes `escalation.create_timers` create one one-time schedule per timer (`a7-esc-<id>`, group
`paynest-a7-<env>`, auto-delete) that sends `{"escalation_id": N}` to the ingest queue; the worker calls `escalation.handler`. Overdue timers fire in about a minute.
A recurring `rate(30 minutes)` schedule sends `{"run_due": true}` as a backstop (context `escalation_backstop_minutes`, 0 disables). Unset (local) = unchanged DB poller.

### Environment
`A7_AUTO_SEND=false` (env can only force OFF; the DB flag `auto_send_enabled` defaults OFF), `AWS_REGION=eu-north-1`, `A7_AWS_PROFILE=none`, DB by parts
(`A7_DB_HOST` + secrets; `sslmode=require`), Valkey `rediss://`.

## OpsStack and MonitoringStack
Log groups `/paynest/a7/<env>/{api,worker,act,mock-payroll,bootstrap}` (retention 7 days, context `log_retention_days` 7|14|30) with a **data protection policy** (audit + mask):
managed `EmailAddress`, `CreditCardNumber`, `CreditCardSecurityCode`, `BankAccountNumber-{DE,ES,FR,GB,IT}`, `AwsSecretKey`; custom `IbanAny`, `EmiratesId`, `AadhaarIN`,
`PanIN`, `BankAcctIfscIN`, `PhoneIntl` (AWS has no managed Aadhaar/PAN/Emirates ID identifier, and its phone/bank identifiers do not cover IN/UAE; CloudWatch custom
regexes cannot use parentheses). SNS topic `paynest-a7-<env>-alarms` (email subscription only if `-c alarm_email=...`, empty by default). Alarms: DLQ depth, ALB 5xx, unhealthy
hosts, worker errors (metric filter on `message failed`), RDS CPU and free storage, Bedrock throttles. One dashboard.
**Budget**: `AWS::Budgets::Budget` does not exist in eu-north-1 (Budgets is global; its CFN resource is us-east-1 only and that Region is off limits here), so
`infra/budget/create_budget.sh` creates it through the API (probe P1 showed the API works from eu-north-1): 50/80/100% of `BUDGET_USD` (default 20), to the SNS topic and optional email.

## Context flags
`alarm_email` (default empty), `budget_usd` (20, used by the script), `log_retention_days` (7), `keep_data` (false: RDS and CMK destroyed; true: final RDS snapshot + CMK retained, deletion protection off so destroy works),
`alb_certificate_arn` (required only with `public_alb=true`), `escalation_backstop_minutes` (30).

## Teardown
`cdk destroy --all` removes everything created here: log groups, secrets, queues, Scheduler group (and its schedules), cluster, ALB, endpoints, RDS (or snapshot with `keep_data`).
`infra/budget/create_budget.sh delete` removes the budget. Left behind on purpose: CDK asset images in the bootstrap ECR repo, and with `keep_data=true` the snapshot and the CMK.
`sizing=doc` keeps deletion protection on RDS: disable it first.

## Tests
`uv run pytest` (42 tests: no NAT, no public IPs, internal ALB, ARM64, only the act role has an unconditioned `kms:Decrypt`, Bedrock scoped to the exact models, data
protection policy and identifiers, secrets via Secrets Manager only, removal policies, keep_data). cdk-nag `-c nag=true` is clean for dev and doc with documented suppressions in `app.py`.

Cost: `COST_ESTIMATE.md`.

## KMS key policy: why the Decrypt deny does not break RDS, Valkey, SQS, Secrets Manager or Logs
The data key carries one `Deny kms:Decrypt` (sid `DenyDirectDecryptExceptActRole`). It applies to a caller only when ALL of these hold:
`aws:PrincipalArn` is an account IAM role or user (`ArnLike role/*`, `user/*`), the caller is NOT the act role and NOT a service-linked role
(`ArnNotLike .../role/aws-service-role/*`), and `kms:ViaService` is absent or not one of `rds|elasticache|sqs|secretsmanager|logs.eu-north-1.amazonaws.com`.
Reasoning (AWS KMS docs on `kms:ViaService`, `kms:GrantIsForAWSResource`, `aws:PrincipalIsAWSService`, and how RDS / ElastiCache use KMS):
- `kms:ViaService` is only present when a service calls KMS on a principal's behalf (forward access session): SQS for the worker role, Secrets Manager, Logs.
- RDS and ElastiCache (like EBS) do not use forward access sessions for at-rest encryption. The deployer's `CreateGrant` (allowed by the default
  account statement and constrained by `kms:GrantIsForAWSResource`) gives the service a grant, and the service then calls KMS with its own identity
  (a service principal or its service-linked role). Those calls carry no ViaService, and an explicit deny overrides a grant. The first design
  (deny unless ViaService is present) would therefore have blocked them. A service principal has no `aws:PrincipalArn`, so `ArnLike` is false and the
  deny cannot apply to it; service-linked roles are exempted by name.
- Humans (SSO roles), the CDK execution role and every task role other than act still cannot call `Decrypt` directly; the worker's `Decrypt` works only
  through SQS (`ViaService`). `GenerateDataKey` (the vault write path) is not denied.

Synth tests (`tests/test_compute.py`) evaluate the deny against sample callers (worker, api, bootstrap, SSO role, CDK role, IAM user: denied; act role, RDS and
ElastiCache service-linked roles, service principals, ViaService calls: not denied) and assert the key has no other Decrypt allow.

**How the deploy proves it:** the data stack creates the encrypted RDS instance (about 7 min) and the Valkey group (about 11 min); both fail at create time if the
service cannot use the key. The compute stack then proves SQS (the worker reads a CMK-encrypted queue) and the bootstrap task proves Secrets Manager.
**Rollback cost if it does not work:** a failed data-stack create rolls back automatically (nothing else is deployed yet). The cost is the 10 to 20 minutes of
RDS/Valkey time already paid (about USD 0.05) plus a fix: delete the one `DenyDirectDecryptExceptActRole` statement in `stacks/data_stack.py` (the narrow IAM
allows stay) and redeploy. The rolled-back key sits in the 7-day KMS pending-deletion window (no charge).

## Accepted demo risks and known behaviour
- The internal ALB is plain HTTP (:80 and :8081). Bearer tokens and masked ticket text cross it inside the VPC only (isolated subnets, no IGW/NAT, security-group
  restricted). Accepted for the demo; a public ALB requires HTTPS with an ACM certificate (and WAF), enforced in code.
- The AWS account id appears in committed docs and probe results. Low sensitivity, left as is.
- The `AadhaarIN` log-masking regex also masks a 12-digit AWS account id starting 2-9 (for example inside an ARN in a log line). CloudWatch Logs regexes cannot use
  parentheses, so a lookbehind is impossible. Over-masking only.
- Worker concurrency is capped at ONE task: Nova Lite allows 10 requests per minute on-demand, each worker rate-limits itself to 0.85 x 10, and the p5-fin stale check adds one
  LLM call per statutory draft. Two workers would send about 17 RPM. A second worker needs a shared rate limit first. The worker extends the SQS visibility timeout
  (heartbeat every 100 s, to 300 s) while a message is processed, so a slow throttled run is not delivered twice.
- The api and act images contain only `data-room/data/customers.csv`; the worker and bootstrap images contain the whole data-room.

## Pre-flight checklist (before any deploy; the user approves each step)
1. Docker is running (`docker info`); the CDK builds five ARM64 images (`docker buildx` with QEMU or a native arm64 builder).
2. `aws sts get-caller-identity --profile paynest` is valid (the session is not expired; never run `aws login` from a script).
3. Cost window: about USD 0.21 per hour (about USD 5 per day). Plan 3 hours (about USD 0.62 plus about USD 1 Bedrock) and the destroy time. Confirm the spend limit and credits in AWS Settings.
4. `cd infra && uv run pytest -q` is green, `npx -y aws-cdk@2 synth -c nag=true -q` succeeds and `cdk diff --all` has been reviewed.
5. The user has said "deploy". Nothing here deploys, bootstraps or destroys on its own.

## Deploy runbook (only after the user confirms; synth and tests need no credentials)
The api ALB health check is `/livez` (no database), so the stack deploys with an EMPTY database. The api task is up, but `/readyz` returns 503 until the bootstrap task
has created the schema and set the `app_user` password. The connection pool inside the api reconnects by itself, so there is NO redeploy after the bootstrap.
```
export AWS_PROFILE=paynest AWS_REGION=eu-north-1 JSII_SILENCE_WARNING_END_OF_LIFE_NODE_VERSION=1
export CDK_DEFAULT_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
cd infra && uv sync --python 3.12 && uv run pytest -q
npx -y aws-cdk@2 synth -c nag=true -q && npx -y aws-cdk@2 diff --all        # diff is read-only; Docker must be running for deploy
npx -y aws-cdk@2 deploy --all                                               # default approval: IAM/SG changes are shown and need a "y". About 25 min (Valkey 11, RDS 7); optional: -c alarm_email=you@example.com
BUDGET_USD=20 ./budget/create_budget.sh create                              # idempotent: an existing budget only gets its limit updated
out() { aws cloudformation describe-stacks --stack-name "$1" --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text; }
C=paynest-a7-dev; NET=$(out $C-compute RunTaskNetworkConfiguration); QUEUE=$(out $C-data IngestQueueUrl)
run() {                                # zsh-safe: an empty overrides argument must not become an empty --overrides
  if [ -n "$2" ]; then
    aws ecs run-task --cluster $C --launch-type FARGATE --network-configuration "$NET" --task-definition "$(out $C-compute $1)" --overrides "$2" --query 'tasks[0].taskArn' --output text
  else
    aws ecs run-task --cluster $C --launch-type FARGATE --network-configuration "$NET" --task-definition "$(out $C-compute $1)" --query 'tasks[0].taskArn' --output text
  fi
}
smoke() {                              # smoke <subcommand and args>: runs the Smoke task, waits, prints the task id (output: aws logs get-log-events, see below)
  T=$(run SmokeTaskDefinition "$(python3 -c "import json,sys;print(json.dumps({'containerOverrides':[{'name':'smoke','command':['python','-m','solution.a7.smoke']+sys.argv[1:]}]}))" "$@")")
  aws ecs wait tasks-stopped --cluster $C --tasks $T; echo ${T##*/}
}
# smoke output: aws logs get-log-events --log-group-name /paynest/a7/dev/api --log-stream-name smoke/smoke/<taskid> --query 'events[].message' --output text
T=$(run BootstrapTaskDefinition); aws ecs wait tasks-stopped --cluster $C --tasks $T
aws ecs describe-tasks --cluster $C --tasks $T --query 'tasks[0].containers[0].exitCode'      # 0; logs: aws logs tail /paynest/a7/dev/bootstrap
S=$(run SmokeTaskDefinition)       # default command `smoke health` = GET /readyz: 200 once the bootstrap has run. Other commands: live | queue | ticket (lane, queue, priority of any processed ticket) | decide | flags | payroll | batch (`smoke batch --tickets PN-101230,PN-101235..PN-101239` replays ids through the ingest queue)
aws sqs send-message --queue-url $QUEUE --message-body '{"ticket_id":"PN-100402"}'          # repeat for PN-101055, PN-101333, PN-101129 (worker scales 0 -> 1 in 2-3 min)
S=$(run SmokeTaskDefinition '{"containerOverrides":[{"name":"smoke","command":["python","-m","solution.a7.smoke","queue","--tenant","<C-id>"]}]}')
aws logs tail /paynest/a7/dev/api --since 10m     # smoke output; approve with: smoke decide --tenant <C-id> --ticket <id> --status approved --reviewer demo
run ActTaskDefinition '{"containerOverrides":[{"name":"act","command":["python","-m","solution.a7.sender","--tenant","<C-id>","--ticket","PN-100402"]}]}'
```

## Gate 6 PII checks in the logs (CloudWatch Logs Insights)
Data protection masks matches at ingestion, so these queries should find ZERO unmasked values. Run each over the five `/paynest/a7/dev/*` log groups and
`/aws/rds/instance/paynest-a7-dev-db/postgresql` for the whole window (`aws logs start-query --log-group-names ... --start-time ... --end-time ... --query-string '<query>'`, then
`get-query-results`). Never print matched values; report counts per log group only.
```
# IBAN (2 letters, 2 digits, then 11 to 30 alphanumerics; spaces allowed between groups)
fields @log | filter @message like /\b[A-Z]{2}[0-9]{2}[ ]?[A-Z0-9]{4}[ ]?[0-9]{4}[ ]?[0-9]{4}[ ]?[0-9]{3,4}/ | stats count(*) by @log
# PAN (5 letters, 4 digits, 1 letter)
fields @log | filter @message like /\b[A-Z]{5}[0-9]{4}[A-Z]\b/ | stats count(*) by @log
# Emirates ID (784-YYYY-NNNNNNN-C, separators optional)
fields @log | filter @message like /\b784[ -]?[0-9]{4}[ -]?[0-9]{7}[ -]?[0-9]\b/ | stats count(*) by @log
# Aadhaar (12 digits starting 2-9, optional spaces or hyphens between groups of 4)
fields @log | filter @message like /\b[2-9][0-9]{3}[ -]?[0-9]{4}[ -]?[0-9]{4}\b/ | stats count(*) by @log
# Phone (international: + then 8 to 14 digits, separators allowed; or a 10-digit Indian mobile starting 6-9)
fields @log | filter @message like /\+[0-9][0-9 -]{7,16}[0-9]/ or @message like /\b[6-9][0-9]{9}\b/ | stats count(*) by @log
```
Expected: 0 rows. A non-zero row means a value reached a log unmasked: stop and report it. Also read the data protection audit findings per log group.

## Destroy (only after the user says "destroy")
```
npx -y aws-cdk@2 destroy --all          # asks for confirmation; add --force only if the user explicitly asked for a non-interactive destroy
./budget/create_budget.sh delete        # idempotent: a missing budget is a no-op
```
Leftovers on purpose: CDK asset images in the bootstrap ECR repo; with `keep_data=true` the snapshot and the CMK. The RDS PostgreSQL log group is now owned by the data stack and is removed by destroy.

## Lessons from the first live deploy (2026-11-16)
- Deploy took 968 s for all five stacks. The KMS `Deny kms:Decrypt` (everyone but the act role, with service and ViaService exemptions) was accepted by RDS, Valkey, SQS and
  Secrets Manager, and the CloudWatch data-protection custom regexes were accepted at create time.
- Gate 6 verdict: PASS with warnings (see `docs/validation/gate6_live.md`). Warnings: route_queue name not visible through smoke (fixed here: `smoke ticket` now returns lane, queue and
  priority), no Incident batch command (fixed here: `smoke batch`), Cost Explorer lags up to 24 h, no audit findings destination on the log data-protection policy.
- A cold-start ticket (worker scaled 0 to 1) took about 3.3 min end to end; a warm worker takes 3 to 10 s per ticket.
- No ticket routed P1 under Nova (all P2, one P3), so the P1 escalation path was proven only by forcing overdue timers (Scheduler schedule, fire, handler).
- The deployment was left up about 25 h because the AWS login expired before destroy could run. Lesson: run destroy in the same session as the deploy, and check
  `aws sts get-caller-identity --profile paynest` before the cost window ends (never run `aws login` from a script).
