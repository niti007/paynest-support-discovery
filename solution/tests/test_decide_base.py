import pytest

from solution.a7.decide import base
from solution.a7.decide.base import (CATEGORIES, INVALID, BaseDecider, DecideItem, Decision, classify_questions,
                                     get_decider, make_decision, register_decider)
from solution.a7.nodes.normalise import canon


class Fake(BaseDecider):
    name = "fake"

    def __init__(self, answers):
        self.answers = answers
        self.calls = 0

    def decide(self, question, options, *, context=None):
        self.calls += 1
        a = self.answers.get(question)
        if isinstance(a, Exception):
            raise a
        return make_decision(a, 0.8, options, self.name, 1.0)


def test_categories_are_canon_names():
    assert len(CATEGORIES) == 9 and len(set(CATEGORIES)) == 9
    for c in CATEGORIES:
        assert canon(c) == c


def test_classify_questions_shape():
    items = classify_questions({"masked_text": "hello"})
    assert [i.name for i in items] == ["category", "urgency", "is_injection"]
    assert all(i.context == "hello" for i in items)
    assert items[0].options == list(CATEGORIES)
    assert items[2].options == ["yes", "no"]
    with pytest.raises(ValueError):
        classify_questions({})


def test_make_decision_valid_and_invalid():
    d = make_decision("a", 0.7, ["a", "b"], "x", 3.0, {"k": 1})
    assert isinstance(d, Decision) and d.choice == "a" and d.confidence == 0.7 and d.raw == {"k": 1}
    for bad in ("zzz", None, 5):
        d = make_decision(bad, 0.9, ["a", "b"], "x", 3.0)
        assert d.choice == INVALID and d.confidence == 0.0 and not d.valid
    assert make_decision("a", float("nan"), ["a"], "x", 0).confidence == 0.0
    assert make_decision("a", 7, ["a"], "x", 0).confidence == 1.0
    assert make_decision("a", "oops", ["a"], "x", 0).confidence == 0.0


def test_default_decide_multi_loops_and_isolates_errors():
    f = Fake({"q1": "a", "q2": "nope", "q3": RuntimeError("boom")})
    out = f.decide_multi([DecideItem("n1", "q1", ["a", "b"]), DecideItem("n2", "q2", ["a", "b"]),
                          DecideItem("n3", "q3", ["a", "b"])])
    assert f.calls == 3
    assert out["n1"].choice == "a"
    assert out["n2"].choice == INVALID
    assert out["n3"].choice == INVALID and out["n3"].raw["error"] == "RuntimeError"


def test_registry():
    assert {"laya", "bedrock_nova", "bedrock_qwen32b", "decider"} <= set(base.available_deciders())
    register_decider("fake-test", lambda **kw: Fake({}))
    assert get_decider("fake-test").name == "fake"
    with pytest.raises(KeyError):
        get_decider("nope")
    d = get_decider("bedrock_qwen32b", client=object())
    assert d.model_id.startswith("qwen.qwen3-32b") and isinstance(d, base.Decider)


def test_registry_aliases_and_guarded_decider():
    assert get_decider("bedrock-qwen3-32b", client=object()).model_id.startswith("qwen.qwen3-32b")
    assert get_decider("bedrock_nova", client=object()).model_id.startswith("eu.amazon.nova-lite")
    try:
        get_decider("decider")  # Builder B's backend: either builds or raises a clean RuntimeError
    except RuntimeError as e:
        assert "not available" in str(e)
