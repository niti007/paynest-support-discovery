"""Minimal local copy of Builder A's decide contract. Deleted at integration (the real base.py takes over).

Import this module before anything that imports solution.a7.decide.base.
"""
from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence


def install() -> None:
    try:
        import solution.a7.decide.base  # noqa: F401
        return
    except ImportError:
        pass

    @dataclass
    class Decision:
        choice: str
        confidence: float
        backend: str
        latency_ms: float
        raw: dict = field(default_factory=dict)

    @dataclass
    class DecideItem:
        name: str
        question: str
        options: Sequence[str]
        context: str | None = None

    class Decider(Protocol):
        def decide(self, question: str, options: Sequence[str], *, context: str | None = None) -> Decision: ...
        def decide_multi(self, items: Sequence[DecideItem]) -> dict[str, Decision]: ...

    mod = types.ModuleType("solution.a7.decide.base")
    mod.Decision, mod.DecideItem, mod.Decider = Decision, DecideItem, Decider
    sys.modules["solution.a7.decide.base"] = mod


install()
