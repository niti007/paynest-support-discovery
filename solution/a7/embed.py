"""Embeddings via Bedrock Cohere Embed v4 (1024 dims), batched, with retry/backoff.

Quota is 20 RPM, so batches are <= 96 texts and throttling errors back off exponentially.
FakeEmbedder is a deterministic offline stand-in (hash-seeded unit vectors; NOT semantic,
but identical texts get identical vectors and token overlap gives some similarity).
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from typing import Protocol

from solution.a7.config import AWS_PROFILE, EMBED_DIMS, EMBED_MODEL, REGION

MAX_BATCH = 96
RETRYABLE = ("ThrottlingException", "TooManyRequestsException", "ServiceUnavailableException",
             "ModelTimeoutException", "InternalServerException")


class Embedder(Protocol):
    def __call__(self, texts: list[str], input_type: str = "search_document") -> list[list[float]]: ...


def _client():
    import boto3
    from botocore.config import Config
    session = boto3.Session(profile_name=AWS_PROFILE, region_name=REGION)
    return session.client("bedrock-runtime", config=Config(retries={"max_attempts": 1}, read_timeout=120))


def embed(texts: list[str], input_type: str = "search_document", *, client=None,
          max_retries: int = 8, base_delay: float = 3.0, sleep=time.sleep) -> list[list[float]]:
    """input_type: 'search_document' (index) or 'search_query' (queries)."""
    from botocore.exceptions import ClientError
    client = client or _client()
    out: list[list[float]] = []
    for i in range(0, len(texts), MAX_BATCH):
        batch = texts[i:i + MAX_BATCH]
        body = json.dumps({"texts": batch, "input_type": input_type,
                           "embedding_types": ["float"], "output_dimension": EMBED_DIMS})
        for attempt in range(max_retries + 1):
            try:
                resp = client.invoke_model(modelId=EMBED_MODEL, body=body,
                                           contentType="application/json", accept="*/*")
                vecs = json.loads(resp["body"].read())["embeddings"]["float"]
                break
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code", "")
                if code not in RETRYABLE or attempt == max_retries:
                    raise
                sleep(min(base_delay * 2 ** attempt, 60))
        if len(vecs) != len(batch):
            raise RuntimeError(f"embedding count mismatch: {len(vecs)} != {len(batch)}")
        out.extend(vecs)
        if i + MAX_BATCH < len(texts):
            sleep(3.2)  # stay under 20 RPM
    return out


class FakeEmbedder:
    """Deterministic, offline. Bag-of-tokens hashed into 1024 dims, L2-normalised."""

    def __init__(self, dims: int = EMBED_DIMS):
        self.dims = dims

    def _one(self, text: str) -> list[float]:
        v = [0.0] * self.dims
        for tok in re.findall(r"\w+", text.lower()) or [""]:
            h = int.from_bytes(hashlib.sha256(tok.encode()).digest()[:8], "big")
            v[h % self.dims] += 1.0 if (h >> 63) & 1 else -1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def __call__(self, texts: list[str], input_type: str = "search_document") -> list[list[float]]:
        return [self._one(t) for t in texts]
