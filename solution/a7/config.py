"""PayNest A7 runtime configuration.

Values come from the Phase 0 probes (infra/probes/p1_*.py, results in infra/probes/results/),
run on 2026-11-08 against account 554559231166, Region eu-north-1, FREE plan.
Only values that a probe resolved and exercised are listed here.
"""
from dataclasses import dataclass, field

REGION = "eu-north-1"
AWS_PROFILE = "paynest"

# Model choice confirmed by the user on 2026-11-08.
# Evidence: infra/probes/results/p1_model_candidates.json (EN/AR/HI/Hinglish + forced enum tool, all passed for these).
MODEL_CHEAP = "eu.amazon.nova-lite-v1:0"          # eu. Geo profile
MODEL_HARD = "qwen.qwen3-235b-a22b-2507-v1:0"     # in-Region ON_DEMAND (alt: openai.gpt-oss-120b-1:0, also passed all checks)
MODEL_FALLBACK = "qwen.qwen3-32b-v1:0"            # in-Region ON_DEMAND, passed all checks

# Claude 4.x did NOT pass, so it is not selected:
#  eu.anthropic.claude-haiku-4-5-20251001-v1:0 and eu.anthropic.claude-sonnet-4-6 ->
#  ResourceNotFoundException: Model use case details have not been submitted for this account.
#  Fill out the Anthropic use case details form before using the model. (user action in the Bedrock console)
# Claude 5.5 IDs: AccessDeniedException "... is not available for this account" (quota TPM = 0).
DENIED_MODELS = ("eu.anthropic.claude-haiku-5-5", "eu.anthropic.claude-sonnet-5-5",
                 "eu.anthropic.claude-haiku-4-5-20251001-v1:0", "eu.anthropic.claude-sonnet-4-6")

# Passed p1_bedrock_embed: 1024 dims, en/hi/ar similarity above unrelated control. Cohere is multilingual.
EMBED_MODEL = "eu.cohere.embed-v4:0"  # InvokeModel body: texts, input_type, embedding_types=["float"], output_dimension=1024
EMBED_MODEL_ALT = "amazon.titan-embed-text-v2:0"  # also passed (dimensions=1024)
EMBED_DIMS = 1024

# USD per 1M tokens (input, output), from the AWS Price List API (pricing, us-east-1 endpoint), read 2026-11-08.
# eu-north-1 standard on-demand rows; Claude and Cohere rows are the eu. geo (non-Global) rates. None = no row, TODO.
PRICE_PER_1M_TOKENS = {
    "eu.amazon.nova-lite-v1:0": (0.065, 0.26),
    "qwen.qwen3-235b-a22b-2507-v1:0": (0.22, 0.88),
    "qwen.qwen3-32b-v1:0": (0.15, 0.60),
    "openai.gpt-oss-120b-1:0": (0.15, 0.60),
    "eu.anthropic.claude-haiku-4-5-20251001-v1:0": (1.10, 5.50),
    "eu.anthropic.claude-sonnet-4-6": (3.30, 16.50),
    "eu.cohere.embed-v4:0": (0.12, 0.0),
    "amazon.titan-embed-text-v2:0": None,  # TODO: no Titan row found in the Price List API
}


@dataclass(frozen=True)
class Quotas:
    """Service Quotas read by p1_account (account-level, eu-north-1)."""
    fargate_ondemand_vcpu: int = 12
    rds_db_instances: int = 40
    elasticache_nodes_per_region: int = 300
    vpc_interface_endpoints_per_vpc: int = 50
    nova_lite_ondemand_rpm: int = 10
    nova_lite_ondemand_tpm: int = 200_000
    qwen3_32b_ondemand_rpm: int = 100
    qwen3_32b_ondemand_tpm: int = 100_000_000
    cohere_embed_v4_crossregion_rpm: int = 20
    cohere_embed_v4_crossregion_tpm: int = 300_000
    claude_haiku_5_5_tpm: int = 0
    claude_sonnet_5_5_tpm: int = 0


QUOTAS = Quotas()

# Free plan facts from freetier GetAccountPlanState: USD 140 credits live as of 2026-11-08 (it was 100 at sign-up).
PLAN = {"type": "FREE", "credits_usd": 140.0, "expires": "2027-05-07"}
