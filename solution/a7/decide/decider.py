"""Strands Decider 2B backend: in-process (MLX, falling back to MPS) or HTTP client to decider/server.py.

Mode: A7_DECIDER_URL set -> http (token from A7_DECIDER_TOKEN, header X-A7-Token); otherwise in-process.
Confidence = calibrated probability of the chosen option (not the library's derived spread score; that is in raw).
Input text (context) must already be masked; nothing here logs it.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Sequence

from solution.a7.decide.base import DecideItem, Decision

BACKEND = "decider"
INVALID = "__invalid__"
_ENGINE = None
_ENGINE_LOCK = threading.Lock()


def _engine():
    """Load the model once per process."""
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            from decider.engine import RealEngine

            _ENGINE = RealEngine()
        return _ENGINE


def _to_decision(res: dict, options: Sequence[str], wall_ms: float | None = None) -> Decision:
    choice = res.get("choice")
    if choice not in options:
        return Decision(choice=INVALID, confidence=0.0, backend=BACKEND,
                        latency_ms=float(res.get("latency_ms", wall_ms or 0.0)), raw=res.get("raw", {}))
    return Decision(choice=choice, confidence=float(res["confidence"]), backend=BACKEND,
                    latency_ms=float(res.get("latency_ms", wall_ms or 0.0)), raw=res.get("raw", {}))


class StrandsDeciderBackend:
    name = BACKEND

    def __init__(self, url: str | None = None, token: str | None = None, timeout: float = 120.0, engine=None):
        self.url = (url or os.environ.get("A7_DECIDER_URL") or "").rstrip("/") or None
        self.token = token or os.environ.get("A7_DECIDER_TOKEN", "")
        self.timeout = timeout
        self._engine_override = engine  # tests / callers may inject an engine with .run(items)
        self._client = None

    @property
    def mode(self) -> str:
        return "http" if self.url else "in-process"

    # -- transport ---------------------------------------------------------------------------
    def _run(self, items: list[dict]) -> dict[str, dict]:
        if self.url:
            import httpx

            if self._client is None:
                self._client = httpx.Client(timeout=self.timeout, headers={"X-A7-Token": self.token})
            if len(items) == 1:
                it = items[0]
                r = self._client.post(f"{self.url}/decide", json={k: it.get(k) for k in ("question", "options", "context", "hints")})
                r.raise_for_status()
                return {it["name"]: r.json()}
            r = self._client.post(f"{self.url}/decide_multi", json={"items": items})
            r.raise_for_status()
            return r.json()["decisions"]
        eng = self._engine_override or _engine()
        return eng.run(items)

    # -- contract ----------------------------------------------------------------------------
    def decide(self, question: str, options: Sequence[str], *, context: str | None = None) -> Decision:
        opts = list(options)
        t0 = time.perf_counter()
        res = self._run([{"name": "q", "question": question, "options": opts, "context": context}])["q"]
        return _to_decision(res, opts, (time.perf_counter() - t0) * 1000)

    def decide_multi(self, items: Sequence[DecideItem]) -> dict[str, Decision]:
        """One forward pass per distinct context (ticket): its questions share the prefix and are batched."""
        items = list(items)
        if not items:
            return {}
        t0 = time.perf_counter()
        payload = [{"name": i.name, "question": i.question, "options": list(i.options), "context": i.context,
                    "hints": dict(i.hints) if i.hints else None} for i in items]
        res = self._run(payload)
        wall = (time.perf_counter() - t0) * 1000 / len(items)
        return {i.name: _to_decision(res[i.name], list(i.options), wall) for i in items}
