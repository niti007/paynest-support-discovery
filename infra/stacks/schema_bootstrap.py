"""Schema bootstrap: DESIGN STUB (no resources created here, no Lambda).

Recommendation: a one-off ECS Fargate task (RunTask), NOT a CDK custom resource / Lambda.
Why:
  * schema.sql needs CREATE EXTENSION vector and a psycopg connection from inside the private VPC; the
    worker image already contains psycopg and solution/db/schema.sql, so no new artefact is needed.
  * A custom resource needs a VPC Lambda (packaging, extra moving parts) and ties DDL to stack
    create/update/rollback; failures are hard to debug and can wedge a rollback.
  * schema.sql is idempotent, so re-running the task after any deploy is safe.
Flow (ComputeStack, Builder B, owns the task definition):
  1. Task def reuses the worker image, command: python -m solution.db.bootstrap (to be written; reads
     the RDS master secret and the app-user secret, runs schema.sql, then ALTER ROLE app_user PASSWORD
     from the app-user secret). schema.sql hardcodes the dev password 'app_user_dev': the bootstrap
     MUST overwrite it on AWS.
  2. Run it with SchemaBootstrapSpec.run_task_cli(...).
  3. Task role: secretsmanager:GetSecretValue on the db master secret and app-user secret only.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class SchemaBootstrapSpec:
    schema_sql_path: str = "solution/db/schema.sql"
    command: tuple = ("python", "-m", "solution.db.bootstrap")

    @staticmethod
    def run_task_cli(cluster: str, task_def: str, subnet: str, sg: str) -> str:
        return (f"aws ecs run-task --region eu-north-1 --cluster {cluster} --task-definition {task_def} "
                f"--launch-type FARGATE --network-configuration "
                f"'awsvpcConfiguration={{subnets=[{subnet}],securityGroups=[{sg}],assignPublicIp=DISABLED}}'")
