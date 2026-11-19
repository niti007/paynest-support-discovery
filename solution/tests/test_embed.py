import io
import json
import math

import pytest
from botocore.exceptions import ClientError

from solution.a7.embed import FakeEmbedder, embed


def test_fake_dims_and_determinism():
    f = FakeEmbedder()
    a, b = f(["WPS SIF file", "WPS SIF file"])
    assert len(a) == 1024 and a == b and a != f(["bank holiday"])[0]
    assert math.isclose(sum(x * x for x in a), 1.0, rel_tol=1e-6)


def test_batching_and_retry():
    calls = []

    class C:
        def invoke_model(self, **kw):
            body = json.loads(kw["body"])
            calls.append(len(body["texts"]))
            if len(calls) == 1:
                raise ClientError({"Error": {"Code": "ThrottlingException"}}, "InvokeModel")
            return {"body": io.BytesIO(json.dumps({"embeddings": {"float": [[0.0] * 1024] * len(body["texts"])}}).encode())}

    out = embed(["x"] * 200, client=C(), sleep=lambda s: None)
    assert len(out) == 200 and calls == [96, 96, 96, 8]  # first batch retried after the throttle


@pytest.mark.aws
def test_real_cohere_dims():
    vecs = embed(["My salary has not arrived yet.", "मेरी तनख्वाह अभी तक नहीं आई है।", "لم يصل راتبي بعد."], "search_query")
    assert [len(v) for v in vecs] == [1024, 1024, 1024]
