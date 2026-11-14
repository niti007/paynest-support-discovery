"""Langfuse (SDK 5.2.0, server 4.79.0 from the compose profile `langfuse`): span masking, a LangGraph callback trace, the living dataset.

Server tests need the profile up and LANGFUSE_HOST / LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY set; they skip (not fail) otherwise, and
the SDK export-path test below still proves the masking hook without a server.
Canaries are synthetic PII-shaped values; the proof is: send a PII-bearing trace, read back what Langfuse STORED, scan it.
"""
from __future__ import annotations

import json
import os
import time
import uuid

import pytest

from solution.a7 import tracing
from solution.a7.pii.scanner import scan
from solution.evals import results

pytestmark = pytest.mark.eval
CAN = {"iban": "AE070331234567890123456", "eid": "784-1990-1234567-1", "pan": "ABCDE1234F", "email": "rahul.test@example.com", "phone": "+91 98765 43210"}
TEXT = "Customer wrote: " + " | ".join(CAN.values())


def _leaks(blob: str) -> list[str]:
    hit = [k for k, v in CAN.items() if v in blob]
    return hit + [f"scanner:{h.type}" for h in scan(blob, presidio=True, ner=False) if not blob[h.start - 1:h.start + 1].startswith("<")]


def test_redact_text_and_legacy_mask():
    out = tracing.redact_text(TEXT)
    assert not [k for k, v in CAN.items() if v in out]
    assert tracing.mask_data({"a": [TEXT], "n": 3})["n"] == 3
    assert "<IBAN_REDACTED>" in out


def test_sdk_export_path_masks_spans_without_a_server():
    """mask_otel_spans on the real export path: an in-memory exporter receives the batch AFTER the hook ran."""
    from langfuse import Langfuse
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: F401
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exp = InMemorySpanExporter()
    lf = Langfuse(public_key="pk-lf-x", secret_key="sk-lf-x", host="http://127.0.0.1:9", mask_otel_spans=tracing.mask_otel_spans,
                  span_exporter=exp, tracing_enabled=True, flush_at=1)
    with lf.start_as_current_observation(name="export-path", as_type="span", input={"t": TEXT}) as sp:
        sp.update(output={"t": TEXT}, metadata={"m": CAN["email"]})
    lf.flush()
    spans = exp.get_finished_spans()
    assert spans, "no spans reached the exporter"
    blob = json.dumps([dict(s.attributes) for s in spans], default=str)
    leaks = [k for k, v in CAN.items() if v in blob]
    results.record("langfuse_export_masking", "Langfuse mask_otel_spans: no raw PII on the SDK export path (no server)", "0 canaries",
                   f"{len(leaks)} leaked of {len(CAN)} canaries", f"n=1 trace, {len(spans)} span(s)", not leaks,
                   "In-memory exporter after the hook. Whether the hook runs before a custom span_exporter is SDK behaviour; the server test below is the real proof.")
    assert not leaks, leaks


def _server_ok() -> bool:
    import urllib.request
    try:
        return tracing.enabled() and urllib.request.urlopen(os.environ["LANGFUSE_HOST"] + "/api/public/health", timeout=3).status == 200
    except Exception:  # noqa: BLE001
        return False


needs_server = pytest.mark.skipif(not _server_ok(), reason="Langfuse server not reachable (docker compose --profile langfuse up -d; set LANGFUSE_*)")


def _stored(lf, session: str, wait: float = 120.0) -> list:
    t0 = time.time()
    while time.time() - t0 < wait:
        try:
            r = lf.api.observations.get_many(session_id=session, fields="core,basic,io,metadata", limit=100)
            if r.data:
                time.sleep(4)          # let the rest of the batch land
                return lf.api.observations.get_many(session_id=session, fields="core,basic,io,metadata", limit=100).data
        except Exception:  # noqa: BLE001
            pass
        time.sleep(3)
    return []


@needs_server
def test_langgraph_trace_with_pii_is_stored_masked():
    """A LangGraph run (callback handler) whose state carries PII-shaped values: what Langfuse stored must contain none."""
    from langgraph.graph import END, START, StateGraph
    from typing import TypedDict

    class S(TypedDict, total=False):
        text: str
        reply: str

    g = StateGraph(S)
    g.add_node("answer", lambda s: {"reply": "Noted: " + s["text"]})
    g.add_edge(START, "answer"); g.add_edge("answer", END)
    graph = g.compile()
    session = f"p5a-mask-{uuid.uuid4().hex[:8]}"
    cfg = tracing.with_callbacks({"run_name": "a7-pii-probe"}, session)
    graph.invoke({"text": TEXT}, cfg)
    tracing.flush()
    lf = tracing.get_client()
    obs = _stored(lf, session)
    assert obs, "Langfuse stored no observations for the session within the wait time"
    blob = json.dumps([o.model_dump(mode="json") for o in obs], default=str)
    assert "_REDACTED>" in blob, "trace stored without redaction markers: the PII-bearing input never reached the hook (vacuous test)"
    leaks = [x for x in _leaks(blob)]
    results.record("langfuse_masking", "Langfuse stored trace contains no raw PII (LangGraph callback + mask_otel_spans), real server v4.79.0", "0",
                   f"{len(leaks)} leaks in {len(obs)} stored observations", f"n=1 PII-bearing LangGraph trace, {len(CAN)} canaries (IBAN, Emirates ID, PAN, email, phone)",
                   not leaks, "Read back through the Langfuse public observations API (v2) after ingestion; scanned with the canaries and Presidio/regex scanner.")
    assert not leaks, leaks


@needs_server
def test_living_dataset_from_approved_drafts():
    from solution.evals import langfuse_dataset as ds

    items = ds.approved_drafts()
    if not items:
        pytest.skip("no approved drafts in this database yet (run the harvest first)")
    r = ds.sync(items=items)
    again = ds.sync(items=items)
    results.record("langfuse_dataset", "Langfuse living dataset from approved drafts", "info",
                   f"{r['approved']} approved drafts in DB; added {r['added']}; second sync added {again['added']}", f"n={r['approved']} approved drafts",
                   None, "Idempotent: item id a7-<ticket>. Items are masked text only (mask_data + assert_masked).")
    assert again["added"] == 0
