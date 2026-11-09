"""Engine wrapper shared by the HTTP server and the in-process backend.

Self-contained on purpose (no solution.* imports) so the container image only needs decider/.
Items are plain dicts: {"name", "question", "options", "context", "hints" (optional {option: description})}. Results are plain dicts:
{"choice", "confidence", "latency_ms", "raw"}; "__invalid__" / 0.0 when the output is not one of the options.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from typing import Any

CHECKPOINT = os.environ.get("DECIDER_CHECKPOINT", "StrandsAgents/strands-decider-2B-qwen3.5-v1-2610")
INVALID = "__invalid__"


def pick_device(requested: str | None = None) -> str:
    """MLX on Apple silicon (fastest, lowest RAM), else MPS, else CPU."""
    if requested:
        return requested
    from strands_decider.infer import mlx_available

    if mlx_available():
        return "mlx"
    import torch

    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class StubEngine:
    """Deterministic fake decisions (no model). Used for the container smoke test."""

    device = "stub"

    def run(self, items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        out = {}
        for it in items:
            t0 = time.perf_counter()
            h = int(hashlib.sha256((it["question"] + "|" + (it.get("context") or "")).encode()).hexdigest(), 16)
            opts = it["options"]
            out[it["name"]] = {
                "choice": opts[h % len(opts)],
                "confidence": round(0.5 + (h % 50) / 100, 2),
                "latency_ms": (time.perf_counter() - t0) * 1000,
                "raw": {"stub": True},
            }
        return out


class RealEngine:
    device: str

    def __init__(self, device: str | None = None, checkpoint: str | None = None):
        from strands_decider.infer import load_engine

        self.device = pick_device(device or os.environ.get("DECIDER_DEVICE"))
        self._eng = load_engine(checkpoint or CHECKPOINT, device=self.device)
        self._lock = threading.Lock()  # one forward at a time (MPS aborts on overlap; MLX has one worker)

    def run(self, items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """One request (shared-prefix forward) per distinct context: questions about one ticket are batched."""
        from strands_decider.schema import ChoiceQuestion

        groups: dict[str, list[dict[str, Any]]] = {}
        for it in items:
            groups.setdefault(it.get("context") or "", []).append(it)
        out: dict[str, dict[str, Any]] = {}
        for ctx, grp in groups.items():
            qs = {it["name"]: ChoiceQuestion(instructions=it["question"], criteria={o: (it.get("hints") or {}).get(o) for o in it["options"]})
                  for it in grp}
            t0 = time.perf_counter()
            with self._lock:
                resp = self._eng.ask(ctx, qs)
            ms = (time.perf_counter() - t0) * 1000
            for it in grp:
                ans = resp.answers[it["name"]]
                probs = ans.probabilities
                choice = ans.choice
                if choice not in it["options"]:
                    out[it["name"]] = {"choice": INVALID, "confidence": 0.0, "latency_ms": ms / len(grp),
                                       "raw": {"device": self.device, "multi_n": len(grp)}}
                    continue
                out[it["name"]] = {
                    "choice": choice,
                    # calibrated (temperature-scaled) probability of the chosen option, not the derived spread score
                    "confidence": float(probs[choice]),
                    "latency_ms": ms / len(grp),  # amortised: one forward serves the whole group
                    "raw": {"probs": probs, "derived_confidence": ans.confidence,
                            "device": self.device, "multi_n": len(grp), "group_ms": round(ms, 2)},
                }
        return out


def build_engine(stub: bool | None = None) -> StubEngine | RealEngine:
    if stub is None:
        stub = os.environ.get("DECIDER_STUB") == "1"
    return StubEngine() if stub else RealEngine()
