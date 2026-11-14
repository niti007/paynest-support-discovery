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


def api_role_name(env_name: str) -> str:
    return f"paynest-a7-{env_name}-api-task"


def mock_role_name(env_name: str) -> str:
    return f"paynest-a7-{env_name}-mock-task"


def bootstrap_role_name(env_name: str) -> str:
    return f"paynest-a7-{env_name}-bootstrap-task"


# Bedrock models actually used (must equal solution/a7/config.py; tests/test_compute.py asserts it). Claude is NOT used: denied on this account.
MODEL_CHEAP = "eu.amazon.nova-lite-v1:0"          # EU geo inference profile (routing, cheap drafting); underlying FM amazon.nova-lite-v1:0
MODEL_HARD = "qwen.qwen3-235b-a22b-2507-v1:0"     # in-Region on-demand
MODEL_FALLBACK = "qwen.qwen3-32b-v1:0"            # in-Region on-demand (routing fallback)
EMBED_MODEL = "eu.cohere.embed-v4:0"              # EU geo inference profile; underlying FM cohere.embed-v4:0
# Geo profiles route to EU Regions; IAM needs the profile ARN plus the foundation-model ARN in each destination Region.
# The wildcard is pinned to the eu-* prefix and conditioned on the profile ARN (bedrock:InferenceProfileArn).
GEO_FM_REGIONS = "eu-*"
