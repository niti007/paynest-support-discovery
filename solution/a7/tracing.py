"""Langfuse tracing for the A7 graph (SDK 5.2.0, server 4.79.x, compose profile `langfuse`).

PII rule: no raw PII reaches a trace. The graph already works on MASKED text, so this is the second line of defence: the
`mask_otel_spans` export hook (Langfuse SDK >= 5, the replacement for the legacy `mask=`) scans every string attribute of every span
with the same regex + Presidio scanner as the pii_gate leak scan and replaces what it finds with `<TYPE_REDACTED>` BEFORE the batch
is sent. If scanning an attribute fails, that attribute is deleted (fail closed).

    from solution.a7 import tracing
    cfg = tracing.with_callbacks(thread_config(ticket_id), ticket_id)      # no-op unless LANGFUSE_* env is set
    graph.invoke(state, cfg)
    tracing.flush()

Env: LANGFUSE_HOST (e.g. http://localhost:53710), LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY. Telemetry to Langfuse is off by default.
"""
from __future__ import annotations

import os
from typing import Any, Mapping, Optional

from .pii.scanner import scan

REDACT_FMT = "<{}_REDACTED>"
_CLIENT: Any = None


def redact_text(text: str) -> str:
    """Replace every PII span (regex layers + Presidio, no NER) with <TYPE_REDACTED>. Raises on scanner failure (callers fail closed)."""
    hits = sorted(scan(text, presidio=True, ner=False), key=lambda h: h.start)
    out, pos = [], 0
    for h in hits:
        if h.start < pos:
            continue
        out.append(text[pos:h.start])
        out.append(REDACT_FMT.format(h.type))
        pos = h.end
    out.append(text[pos:])
    return "".join(out)


def mask_data(data: Any) -> Any:
    """Legacy `mask=` hook shape (keyword-only `data`), recursive over str / dict / list."""
    if isinstance(data, str):
        return redact_text(data)
    if isinstance(data, Mapping):
        return {k: mask_data(v) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [mask_data(v) for v in data]
    return data


def _mask_attr(value: Any) -> Any:
    """Return the masked value, or the original object when nothing changed."""
    if isinstance(value, str):
        new = redact_text(value)
        return value if new == value else new
    if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
        new = [redact_text(v) for v in value]
        return value if list(new) == list(value) else new
    return value


def mask_otel_spans(*, params: Any) -> Any:
    """Export-stage hook: patch every span whose string attributes contain PII. Fail closed per attribute."""
    from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

    patches = {}
    for ident, span in params.spans.items():
        setv: dict[str, Any] = {}
        delv: list[str] = []
        for key, val in span.attributes.items():
            try:
                new = _mask_attr(val)
            except Exception:  # noqa: BLE001 - cannot prove it is clean: drop it
                delv.append(key)
                continue
            if new is not val:
                setv[key] = new
        if setv or delv:
            setv["a7.masking.applied"] = True
            patches[ident] = OtelSpanPatch(set_attributes=setv, delete_attributes=tuple(delv))
    return MaskOtelSpansResult(span_patches=patches)


def enabled() -> bool:
    return bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY") and os.environ.get("LANGFUSE_HOST"))


def get_client(**kw: Any) -> Any:
    """The process-wide masked Langfuse client (created once)."""
    global _CLIENT
    if _CLIENT is None:
        os.environ.setdefault("LANGFUSE_TRACING_ENVIRONMENT", "a7-local")
        from langfuse import Langfuse
        _CLIENT = Langfuse(mask_otel_spans=mask_otel_spans, **kw)
    return _CLIENT


def with_callbacks(config: dict, ticket_id: str, *, tags: Optional[list[str]] = None) -> dict:
    """Add the LangGraph/LangChain callback to a run config. Returns config unchanged when Langfuse is not configured."""
    if not enabled():
        return config
    get_client()
    from langfuse.langchain import CallbackHandler
    meta = {**config.get("metadata", {}), "langfuse_session_id": ticket_id, "langfuse_tags": tags or ["a7"]}
    return {**config, "callbacks": [*config.get("callbacks", []), CallbackHandler(public_key=os.environ["LANGFUSE_PUBLIC_KEY"])], "metadata": meta}


def flush() -> None:
    if _CLIENT is not None:
        _CLIENT.flush()
