"""PayNest A7 runtime configuration.

Values come from the Phase 0 probes (infra/probes/p1_*.py, results in infra/probes/results/),
run on 2026-11-08 against account 554559231166, Region eu-north-1, FREE plan.
Only values that a probe resolved and exercised are listed here.
"""
from dataclasses import dataclass, field

REGION = "eu-north-1"
AWS_PROFILE = "paynest"

# Passed p1_bedrock_llm: EN/AR/HI/Hinglish Converse + forced enum tool-use (cheap), EN/AR (hard).
MODEL_CHEAP = "qwen.qwen3-32b-v1:0"          # in-Region ON_DEMAND
MODEL_HARD = "openai.gpt-oss-120b-1:0"       # in-Region ON_DEMAND
MODEL_FALLBACK = "eu.amazon.nova-lite-v1:0"  # eu. Geo profile; enum tool-use passed

# DENIED (AccessDeniedException "... is not available for this account"); their Service Quotas
# tokens-per-minute are 0 on this plan, so not requestable here:
DENIED_MODELS = ("eu.anthropic.claude-haiku-5-5", "eu.anthropic.claude-sonnet-5-5")

# Passed p1_bedrock_embed: 1024 dims, en/hi/ar similarity above unrelated control. Cohere is multilingual.
EMBED_MODEL = "eu.cohere.embed-v4:0"  # InvokeModel body: texts, input_type, embedding_types=["float"], output_dimension=1024
EMBED_MODEL_ALT = "amazon.titan-embed-text-v2:0"  # also passed (dimensions=1024)
EMBED_DIMS = 1024

# USD per 1K tokens (input, output). APPROXIMATE public list prices as assumed by the probes
# (source: AWS Bedrock pricing page / provider list prices, not read from an API; recheck before billing use).
PRICE_PER_1K_TOKENS = {
    MODEL_CHEAP: (0.00015, 0.0006),
    MODEL_HARD: (0.00015, 0.0006),
    MODEL_FALLBACK: (0.00006, 0.00024),
    EMBED_MODEL: (0.00012, 0.0),
    EMBED_MODEL_ALT: (0.00002, 0.0),
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

# Free plan facts from freetier.get_account_plan_state
PLAN = {"type": "FREE", "credits_usd": 100.0, "expires": "2027-05-07"}
