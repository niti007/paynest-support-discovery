"""Mask PII in anything a tool returns to the model, and in what we log about tool args."""
from __future__ import annotations

from typing import Any

from solution.a7.pii.scanner import scan

PARTIAL_KEYS = {"employee_id"}


def mask_text(text: str) -> str:
    hits = sorted(scan(text), key=lambda h: h.start)
    out, pos = [], 0
    for h in hits:
        if h.start < pos:
            continue
        out.append(text[pos:h.start])
        out.append(f"[{h.type}]")
        pos = h.end
    out.append(text[pos:])
    return "".join(out)


def mask_obj(obj: Any, key: str | None = None) -> Any:
    if isinstance(obj, str):
        if key in PARTIAL_KEYS:
            return "*" * max(0, len(obj) - 2) + obj[-2:]
        return mask_text(obj)
    if isinstance(obj, dict):
        return {k: mask_obj(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask_obj(v, key) for v in obj]
    return obj
