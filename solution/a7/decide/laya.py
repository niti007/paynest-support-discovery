"""Laya multilingual backend (convaiinnovations/laya-multilingual, 322M, non-autoregressive).

- In-process, MPS first with CPU fallback (override: env LAYA_DEVICE). Loaded lazily, once per process.
- decide_multi answers every question in ONE forward pass (agent.predict with several questions).
- Confidence = Laya's native probability for the chosen option. Optional temperature scaling (one T per
  question name, fitted on the held-out calib split of the routing set) is stored in calib/laya.json.
  Scaling p_i -> p_i^(1/T) / sum is the softmax-temperature of the log-probabilities; argmax is unchanged.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path
from typing import Any, Sequence

from .base import BaseDecider, DecideItem, Decision, invalid, make_decision

os.environ.setdefault("USE_TF", "0")  # transformers otherwise probes TensorFlow and may hang

MODEL_ID = "convaiinnovations/laya-multilingual"
CALIB_PATH = Path(__file__).resolve().parent / "calib" / "laya.json"

_lock = threading.Lock()
_agent: Any = None
_device: str | None = None


def load_agent(device: str | None = None):
    """Process-wide lazy singleton. MPS, falling back to CPU if loading or the first call fails."""
    global _agent, _device
    with _lock:
        if _agent is not None:
            return _agent
        import laya
        import torch

        want = device or os.environ.get("LAYA_DEVICE") or ("mps" if torch.backends.mps.is_available() else "cpu")
        tried = [want] if want == "cpu" else [want, "cpu"]
        last: Exception | None = None
        for dev in tried:
            try:
                agent = laya.load(MODEL_ID, device=dev)
                agent.predict({"body": "warm up"}, {"q": {"type": "choice", "instructions": "x", "criteria": ["a", "b"]}})
                _agent, _device = agent, dev
                return _agent
            except Exception as e:  # noqa: BLE001
                last = e
        raise RuntimeError(f"could not load {MODEL_ID}: {last!r}")


def loaded_device() -> str | None:
    return _device


# ---- temperature scaling ------------------------------------------------------------------------------------
def scale_probs(probs: dict[str, float], temperature: float) -> dict[str, float]:
    t = max(temperature, 1e-3)
    logs = {k: math.log(max(v, 1e-12)) / t for k, v in probs.items()}
    m = max(logs.values())
    ex = {k: math.exp(v - m) for k, v in logs.items()}
    z = sum(ex.values())
    return {k: v / z for k, v in ex.items()}


def nll(samples: Sequence[tuple[dict[str, float], str]], temperature: float) -> float:
    tot = 0.0
    for probs, label in samples:
        tot -= math.log(max(scale_probs(probs, temperature).get(label, 0.0), 1e-12))
    return tot / max(len(samples), 1)


def fit_temperature(samples: Sequence[tuple[dict[str, float], str]]) -> float:
    """Minimise NLL over T in [0.2, 20] (golden-section search on log T; NLL is unimodal in practice)."""
    if not samples:
        return 1.0
    lo, hi = math.log(0.2), math.log(20.0)
    g = (math.sqrt(5) - 1) / 2
    a, b = hi - g * (hi - lo), lo + g * (hi - lo)
    fa, fb = nll(samples, math.exp(a)), nll(samples, math.exp(b))
    for _ in range(60):
        if fa < fb:
            hi, b, fb = b, a, fa
            a = hi - g * (hi - lo)
            fa = nll(samples, math.exp(a))
        else:
            lo, a, fa = a, b, fb
            b = lo + g * (hi - lo)
            fb = nll(samples, math.exp(b))
    return math.exp((lo + hi) / 2)


class Calibrator:
    """Per-question temperatures. `fit` must be given calib-split data only."""

    def __init__(self, temperatures: dict[str, float] | None = None, meta: dict[str, Any] | None = None):
        self.temperatures = dict(temperatures or {})
        self.meta = dict(meta or {})

    def temperature(self, name: str) -> float:
        return self.temperatures.get(name, 1.0)

    def apply(self, name: str, probs: dict[str, float]) -> dict[str, float]:
        return scale_probs(probs, self.temperature(name))

    @classmethod
    def fit(cls, by_question: dict[str, Sequence[tuple[dict[str, float], str]]], meta: dict[str, Any] | None = None) -> "Calibrator":
        return cls({q: fit_temperature(s) for q, s in by_question.items()}, meta)

    def save(self, path: Path = CALIB_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"temperatures": self.temperatures, "meta": self.meta}, indent=2, sort_keys=True))

    @classmethod
    def load(cls, path: Path = CALIB_PATH) -> "Calibrator":
        if not path.exists():
            return cls()
        d = json.loads(path.read_text())
        return cls(d.get("temperatures"), d.get("meta"))


# ---- decider ------------------------------------------------------------------------------------------------
class LayaDecider(BaseDecider):
    name = "laya"

    def __init__(self, calibrate: bool = False, device: str | None = None, calibrator: Calibrator | None = None):
        self.calibrate = calibrate
        self.device = device
        self.calibrator = calibrator or (Calibrator.load() if calibrate else Calibrator())
        if calibrate:
            self.name = "laya-calibrated"

    # Laya reads the ticket from the state dict; questions carry their option labels (+ optional hints).
    @staticmethod
    def _questions(items: Sequence[DecideItem]) -> dict[str, dict[str, Any]]:
        qs: dict[str, dict[str, Any]] = {}
        for it in items:
            crit: Any = {o: (it.hints or {}).get(o, o) for o in it.options} if it.hints else list(it.options)
            qs[it.name] = {"type": "choice", "instructions": f"{it.question} (`body`)", "criteria": crit}
        return qs

    def decide(self, question: str, options: Sequence[str], *, context: str | None = None) -> Decision:
        return self.decide_multi([DecideItem("q", question, list(options), context)])["q"]

    def decide_multi(self, items: Sequence[DecideItem]) -> dict[str, Decision]:
        if not items:
            return {}
        text = items[0].context or ""
        t0 = time.perf_counter()
        try:
            agent = load_agent(self.device)
            t0 = time.perf_counter()  # exclude one-off model load from latency
            res = agent.predict({"body": text}, self._questions(items))
        except Exception as e:  # noqa: BLE001
            ms = (time.perf_counter() - t0) * 1000 / len(items)
            return {it.name: invalid(self.name, ms, error=type(e).__name__) for it in items}
        call_ms = (time.perf_counter() - t0) * 1000
        ms = call_ms / len(items)  # amortised per question
        out: dict[str, Decision] = {}
        for it in items:
            ans = (res.get("answers") or {}).get(it.name) or {}
            probs = {k: float(v) for k, v in (ans.get("probabilities") or {}).items() if k in it.options}
            raw = {"probs": {k: round(v, 5) for k, v in probs.items()}, "shared_call": len(items) > 1, "call_ms": call_ms,
                   "device": loaded_device(), "truncated": (res.get("usage") or {}).get("truncated", False)}
            choice = ans.get("choice")
            if not probs:
                out[it.name] = make_decision(None, 0, it.options, self.name, ms, raw)
                continue
            if self.calibrate:
                cal = self.calibrator.apply(it.name, probs)
                raw["raw_confidence"] = round(probs.get(choice, 0.0), 5)
                raw["temperature"] = self.calibrator.temperature(it.name)
                conf = cal.get(choice, 0.0)
            else:
                conf = probs.get(choice, 0.0)
            out[it.name] = make_decision(choice, conf, it.options, self.name, ms, raw)
        return out
