from solution.a7.decide.base import Decision
from solution.a7.decide.policy import route_decision


def D(choice, conf, backend="x"):
    return Decision(choice=choice, confidence=conf, backend=backend, latency_ms=0.0)


def mk(conf, dec_inj="no", nova_inj="no", nova_cat="billing"):
    return {"decider": {"category": D("access", conf), "is_injection": D(dec_inj, 0.9)},
            "bedrock_nova": {"category": D(nova_cat, 0.7), "is_injection": D(nova_inj, 0.9)}}


BENIGN = "I cannot log in to my account since this morning."


def test_threshold_edge_exactly_080_is_auto():
    r = route_decision(mk(0.80), BENIGN, "en")
    assert r.lane == "auto" and r.category == "access" and r.suggested_category == "billing"


def test_below_threshold_goes_human_with_nova_suggestion():
    r = route_decision(mk(0.7999), BENIGN, "en")
    assert r.lane == "human" and r.category is None and r.suggested_category == "billing"
    assert r.reasons[0].startswith("low_confidence")


def test_guard_forces_human_even_at_full_confidence(monkeypatch):
    from solution.a7.decide import policy

    monkeypatch.setattr(policy, "needs_human_injection_review", lambda t, l: (l in ("ar", "hi"), "instruction_like"))
    r = route_decision(mk(1.0), "x", "ar")
    assert r.lane == "human" and r.reasons == ["injection_guard:instruction_like"] and r.suggested_category == "billing"
    assert route_decision(mk(1.0), "x", "en").lane == "auto"


def test_injection_flag_by_either_backend_forces_human():
    for kw in ({"dec_inj": "yes"}, {"nova_inj": "yes"}):
        r = route_decision(mk(0.99, **kw), BENIGN, "en")
        assert r.lane == "human" and any(x.startswith("is_injection") for x in r.reasons)


def test_invalid_auto_category_goes_human():
    d = mk(0.99)
    d["decider"]["category"] = D("__invalid__", 0.99)
    assert route_decision(d, BENIGN, "en").lane == "human"
