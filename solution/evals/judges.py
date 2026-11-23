"""DeepEval / RAGAS judges on Bedrock (Converse), with every call booked in the spend ledger.

Probe result (2026-11-12, eu-north-1, profile paynest): deepeval 4.3.3 `AmazonBedrockModel` works natively through Converse with
qwen.qwen3-235b-a22b-2507-v1:0 and eu.amazon.nova-lite-v1:0 (plain generate and FaithfulnessMetric with its JSON schemas). No custom
DeepEvalBaseLLM wrapper is needed for DeepEval. RAGAS uses langchain-aws ChatBedrockConverse through LangchainLLMWrapper.

Judges see MASKED text only: callers pass harvest fields (masked_text / draft / contexts); `assert_masked` refuses text the scanner flags.
"""
from __future__ import annotations

import os

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")

from deepeval.models import AmazonBedrockModel  # noqa: E402

from solution.a7 import config  # noqa: E402
from solution.a7.pii.scanner import scan  # noqa: E402
from solution.evals import spend  # noqa: E402

JUDGE_PRIMARY = config.MODEL_HARD            # Qwen3-235B
JUDGE_CROSS = config.MODEL_CHEAP             # Nova Lite (Builder B's cross-check)


def assert_masked(*texts: str) -> None:
    for t in texts:
        hits = scan(t or "", presidio=True, ner=False)
        assert not hits, f"unmasked PII ({sorted({h.type for h in hits})}) must not reach a judge"


class TrackedBedrock(AmazonBedrockModel):
    """AmazonBedrockModel that books each call's cost (config prices, eu-north-1) in the ledger and stops at the stop line."""

    def __init__(self, model: str, component: str, **kw):
        p_in, p_out = spend.price(model)
        super().__init__(model=model, region=config.REGION, cost_per_input_token=p_in / 1e6, cost_per_output_token=p_out / 1e6,
                         generation_kwargs={"temperature": 0, "maxTokens": 2000, **kw.pop("generation_kwargs", {})}, **kw)
        self._ledger_component = component

    def generate(self, *a, **kw):
        spend.guard()
        return super().generate(*a, **kw)

    async def a_generate(self, *a, **kw):
        spend.guard()
        return await super().a_generate(*a, **kw)

    def calculate_cost(self, input_tokens, output_tokens):
        spend.add(self._ledger_component, spend.usd(self.name, input_tokens, output_tokens))
        return super().calculate_cost(input_tokens, output_tokens)


def primary_judge(component: str = "judge_qwen235b") -> TrackedBedrock:
    return TrackedBedrock(JUDGE_PRIMARY, component)


def cross_judge(component: str = "judge_nova") -> TrackedBedrock:
    return TrackedBedrock(JUDGE_CROSS, component)


def ragas_llm(component: str = "ragas_qwen235b"):
    """RAGAS classic API LLM: ChatBedrockConverse(Qwen3-235B) behind LangchainLLMWrapper. Cost is booked via a callback."""
    import solution.evals.ragas_compat  # noqa: F401  (must precede `import ragas`)
    from langchain_aws import ChatBedrockConverse
    from langchain_core.callbacks import BaseCallbackHandler
    from ragas.llms import LangchainLLMWrapper

    from solution.a7.llm import make_client

    class Book(BaseCallbackHandler):
        def on_llm_end(self, response, **kw):
            tin = tout = 0
            for gens in response.generations:
                for g in gens:
                    um = getattr(getattr(g, "message", None), "usage_metadata", None) or {}
                    tin += um.get("input_tokens", 0); tout += um.get("output_tokens", 0)
            spend.add(component, spend.usd(JUDGE_PRIMARY, tin, tout))

    spend.guard()
    chat = ChatBedrockConverse(model=JUDGE_PRIMARY, region_name=config.REGION, client=make_client(), max_tokens=2000,
                               temperature=0, callbacks=[Book()])
    return LangchainLLMWrapper(chat)
