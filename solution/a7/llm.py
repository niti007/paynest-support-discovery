"""Bedrock chat models for the agent loop and the guard: cheap = Nova Lite, hard = Qwen3-235B (ChatBedrockConverse).

Nova has no structured output: use bind_tools (forced tool schema + client-side validation) for anything structured.
The boto3 client is created under a lock: concurrent first use of boto3.Session in threads is not safe (Phase 2 bug).
"""
from __future__ import annotations

import threading
from typing import Any

from . import config

TIERS = {"cheap": config.MODEL_CHEAP, "hard": config.MODEL_HARD}
MAX_TOKENS = {"cheap": 1200, "hard": 1800}
_LOCK = threading.Lock()
_CLIENT: Any = None
_MODELS: dict[str, Any] = {}


def make_client(region: str = config.REGION, profile: str | None = config.AWS_PROFILE):
    import boto3
    from botocore.config import Config

    sess = boto3.Session(profile_name=profile, region_name=region) if profile else boto3.Session(region_name=region)
    return sess.client("bedrock-runtime", config=Config(retries={"mode": "adaptive", "max_attempts": 8},
                                                        read_timeout=90, connect_timeout=10))


def _client():
    global _CLIENT
    with _LOCK:
        if _CLIENT is None:
            _CLIENT = make_client()
        return _CLIENT


def llm(tier: str):
    """ChatBedrockConverse for `tier` ('cheap' | 'hard'). One instance per tier per process."""
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}")
    client = _client()
    with _LOCK:
        if tier not in _MODELS:
            from langchain_aws import ChatBedrockConverse

            _MODELS[tier] = ChatBedrockConverse(model=TIERS[tier], region_name=config.REGION, client=client,
                                                max_tokens=MAX_TOKENS[tier], temperature=0)
        return _MODELS[tier]
