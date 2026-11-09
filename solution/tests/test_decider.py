import pytest
from fastapi.testclient import TestClient

from solution.a7.decide.base import DecideItem
from solution.a7.decide.decider import StrandsDeciderBackend

CATS = ["payroll", "billing", "howto"]
Q = "Which team should handle this ticket?"
TEXT = "Salary file rejected by the bank, employees unpaid, payroll closes tomorrow."


@pytest.fixture()
def stub_client(monkeypatch):
    monkeypatch.setenv("DECIDER_STUB", "1")
    monkeypatch.setenv("A7_DECIDER_TOKEN", "s3cret")
    from decider.server import app

    with TestClient(app) as c:
        yield c


def http_backend(client):
    b = StrandsDeciderBackend(url="http://test", token="s3cret")
    client.headers["X-A7-Token"] = "s3cret"
    b._client = client
    return b


def test_health_no_auth(stub_client):
    r = stub_client.get("/health")
    assert r.status_code == 200 and r.json()["stub"] is True


def test_token_required(stub_client, monkeypatch):
    body = {"question": Q, "options": CATS, "context": TEXT}
    assert stub_client.post("/decide", json=body).status_code == 401
    assert stub_client.post("/decide", json=body, headers={"X-A7-Token": "wrong"}).status_code == 401
    assert stub_client.post("/decide", json=body, headers={"X-A7-Token": "s3cret"}).status_code == 200
    # missing server-side token fails closed
    monkeypatch.delenv("A7_DECIDER_TOKEN")
    assert stub_client.post("/decide", json=body, headers={"X-A7-Token": "s3cret"}).status_code == 401


def test_http_decide_contract(stub_client):
    b = http_backend(stub_client)
    d = b.decide(Q, CATS, context=TEXT)
    assert d.choice in CATS and 0 <= d.confidence <= 1 and d.backend == "decider"
    assert d.latency_ms >= 0 and isinstance(d.raw, dict)
    assert b.decide(Q, CATS, context=TEXT).choice == d.choice  # deterministic


def test_http_decide_multi(stub_client):
    b = http_backend(stub_client)
    items = [DecideItem("category", Q, CATS, TEXT), DecideItem("urgency", "How urgent?", ["low", "high"], TEXT)]
    out = b.decide_multi(items)
    assert set(out) == {"category", "urgency"}
    assert out["category"].choice in CATS and out["urgency"].choice in ["low", "high"]


def test_invalid_output_maps_to_invalid():
    class Bad:
        def run(self, items):
            return {i["name"]: {"choice": "nonsense", "confidence": 0.9, "latency_ms": 1.0, "raw": {}} for i in items}

    d = StrandsDeciderBackend(engine=Bad()).decide(Q, CATS, context=TEXT)
    assert d.choice == "__invalid__" and d.confidence == 0


def test_mode_selection(monkeypatch):
    monkeypatch.delenv("A7_DECIDER_URL", raising=False)
    assert StrandsDeciderBackend().mode == "in-process"
    assert StrandsDeciderBackend(url="http://x:8080/").mode == "http"


@pytest.mark.model
def test_real_mlx_decision():
    b = StrandsDeciderBackend()
    d = b.decide(Q, CATS, context=TEXT)
    assert d.choice in CATS and 0 < d.confidence <= 1
    assert d.raw["device"] in ("mlx", "mps")
    assert abs(sum(d.raw["probs"].values()) - 1) < 0.01
    out = b.decide_multi([DecideItem("category", Q, CATS, TEXT), DecideItem("urgency", "How urgent?", ["low", "high"], TEXT)])
    assert out["category"].choice == d.choice
    assert out["urgency"].choice in ("low", "high")


def test_hints_reach_engine_criteria():
    """CATEGORY_HINTS must reach the strands-decider ChoiceQuestion.criteria (they used to be dropped)."""
    from types import SimpleNamespace

    from decider.engine import RealEngine
    from solution.a7.decide.base import CATEGORY_HINTS, classify_questions

    seen = {}

    class FakeEng:
        def ask(self, ctx, qs):
            seen.update({n: q.criteria for n, q in qs.items()})
            ans = {n: SimpleNamespace(choice=list(q.criteria)[0], probabilities={k: 1 / len(q.criteria) for k in q.criteria},
                                      confidence=0.5) for n, q in qs.items()}
            return SimpleNamespace(answers=ans)

    eng = RealEngine.__new__(RealEngine)
    import threading
    eng._eng, eng._lock, eng.device = FakeEng(), threading.Lock(), "fake"
    items = classify_questions({"masked_text": "hello"})
    cat = next(i for i in items if i.name == "category")
    assert cat.hints
    StrandsDeciderBackend(engine=eng).decide_multi(items)
    assert seen["category"] == {o: CATEGORY_HINTS[o] for o in cat.options}
    # questions without hints still send bare labels (None)
    other = next(i for i in items if i.name != "category")
    assert all(v is None for v in seen[other.name].values()) or other.hints


def test_hints_pass_over_http(stub_client):
    r = stub_client.post("/decide_multi", headers={"X-A7-Token": "s3cret"},
                         json={"items": [{"name": "a", "question": Q, "options": CATS, "context": TEXT,
                                          "hints": {"payroll": "salary"}}]})
    assert r.status_code == 200
