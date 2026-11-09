"""Shared config for the PayNest A7 CDK app: sizing profiles, naming contract, tags."""
from dataclasses import dataclass

REGION = "eu-north-1"  # one Region only (project SCP); never parameterise to another Region in this account
PROJECT_TAGS = {"project": "paynest", "app": "a7"}


@dataclass(frozen=True)
class Sizing:
    name: str
    db_instance_class: str       # RDS instance type without the "db." prefix
    db_allocated_gb: int
    db_backup_days: int
    cache_node_type: str
    endpoint_azs: int            # interface endpoints per service: AZs covered
    destroyable: bool            # dev-only teardown behaviour (DESTROY, no deletion protection)


SIZINGS = {
    # Plan: dev = db.t4g.micro / cache.t4g.micro, single node, endpoints in 1 AZ.
    "dev": Sizing("dev", "t4g.micro", 20, 1, "cache.t4g.micro", 1, True),
    # Session 4 Table 2: RDS db.t4g.medium, Valkey t4g.small ("if credits drain -> db.m7g.large").
    # Hardened: 7-day backups, deletion protection, retained data, endpoints in both AZs.
    "doc": Sizing("doc", "t4g.medium", 50, 7, "cache.t4g.small", 2, False),
}


# Role-name contract with ComputeStack (Builder B). The PII-vault key policy denies direct kms:Decrypt
# to every principal except the act role. ComputeStack MUST name its task roles with these functions.
def worker_role_name(env_name: str) -> str:
    return f"paynest-a7-{env_name}-worker-task"


def act_role_name(env_name: str) -> str:
    return f"paynest-a7-{env_name}-act-task"
