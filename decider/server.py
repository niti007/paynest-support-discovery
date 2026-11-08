"""FastAPI front for the Strands Decider.

POST /decide        {"question","options":[...],"context":null}            -> Decision JSON
POST /decide_multi  {"items":[{"name","question","options","context"}]}    -> {"decisions": {name: Decision JSON}}
GET  /health        no auth (ALB health check)

Auth: header X-A7-Token must equal env A7_DECIDER_TOKEN. If the env var is not set the server rejects every
request (fail closed). The Dockerfile CMD binds 0.0.0.0:8080 inside the container only; natively use
`uv run uvicorn decider.server:app --port 8090` (uvicorn defaults to 127.0.0.1).
DECIDER_STUB=1 returns deterministic fake decisions without loading the model.
"""
from __future__ import annotations

import contextlib
import hmac
import os

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from decider.engine import build_engine


class DecideReq(BaseModel):
    question: str
    options: list[str] = Field(min_length=2)
    context: str | None = None


class ItemReq(DecideReq):
    name: str


class MultiReq(BaseModel):
    items: list[ItemReq] = Field(min_length=1)


def require_token(x_a7_token: str | None = Header(default=None)) -> None:
    expected = os.environ.get("A7_DECIDER_TOKEN")
    if not expected:
        raise HTTPException(status_code=401, detail="server token not configured")
    if not x_a7_token or not hmac.compare_digest(x_a7_token, expected):
        raise HTTPException(status_code=401, detail="bad or missing X-A7-Token")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.engine = build_engine()
    yield


app = FastAPI(title="a7-decider", lifespan=lifespan)


@app.get("/health")
def health():
    eng = app.state.engine
    return {"status": "ok", "device": eng.device, "stub": eng.device == "stub"}


@app.post("/decide", dependencies=[Depends(require_token)])
def decide(req: DecideReq):
    return app.state.engine.run([{"name": "q", **req.model_dump()}])["q"]


@app.post("/decide_multi", dependencies=[Depends(require_token)])
def decide_multi(req: MultiReq):
    names = [i.name for i in req.items]
    if len(set(names)) != len(names):
        raise HTTPException(status_code=422, detail="duplicate item names")
    return {"decisions": app.state.engine.run([i.model_dump() for i in req.items])}
