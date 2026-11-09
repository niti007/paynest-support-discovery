# PayNest A7 infra (AWS CDK, Python)

Stacks owned here: `paynest-a7-<env>-network` and `paynest-a7-<env>-data`. ComputeStack and OpsStack are Builder B.
Region is fixed to **eu-north-1** (the app refuses any other Region). Nothing here is deployed by default.

## Setup and synth

Own project file: `infra/pyproject.toml` (separate from the root `solution/` env). Python 3.12 via uv, CDK CLI via npx.

```
cd infra
uv sync --python 3.12
export CDK_DEFAULT_ACCOUNT=<project account>   # taken from env, never hard-coded
npx -y aws-cdk@2 synth -c sizing=dev           # or -c sizing=doc
uv run pytest                                   # 14 assertion tests, no AWS calls
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
(all confirmed available in eu-north-1). Security groups (all defined here): alb, api, worker, decider, db, cache, endpoint.
Ingress: ALB (VPC CIDR :80, or 0.0.0.0/0 :443 when public) -> api :8080; worker/api -> decider :8080, db :5432, cache :6379;
worker/api/decider -> endpoints :443. The only 0.0.0.0/0 ingress that can exist is the ALB :443 rule.
Decision: **ALB is internal by default**; a public ALB needs WAF and an ACM certificate, which are out of scope.

## DataStack
- One customer KMS key (rotation on): RDS storage, PII-vault data keys, SQS, Valkey at rest.
  Key policy denies direct `kms:Decrypt` to everyone except role `paynest-a7-<env>-act-task` (service calls via RDS/SQS are unaffected).
  Managed policies (stack outputs): `WorkerVaultPolicyArn` = `kms:GenerateDataKey` only; `ActVaultPolicyArn` = `kms:Decrypt`.
  **Contract for ComputeStack:** name the task roles `config.worker_role_name(env)` / `config.act_role_name(env)` and attach those policies.
- RDS PostgreSQL 17.11 (proven in probe P3), private isolated, not public, `ManageMasterUserPassword` (Secrets Manager), CMK-encrypted.
- Generated `app_user` secret (the RLS role). `solution/db/schema.sql` hardcodes `app_user_dev`; the bootstrap must set this secret's password on AWS.
- ElastiCache Valkey 9.0, single node, TLS in transit and at rest.
- SQS ingest queue (maxReceiveCount 5) plus DLQ (14 days), both KMS-encrypted, TLS enforced.
- Tags `project=paynest`, `app=a7` on everything.

### Schema bootstrap (recommendation, stub only)
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
RDS11 default port, SMG4 rotation, AEC6 Valkey AUTH, RDS10 (dev only), and a CdkNagValidationFailure for the CIDR token.
