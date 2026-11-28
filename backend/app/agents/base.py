"""Common agent contract.

Every agent has: an id/name/responsibility, a pydantic Input model, a pydantic Output model,
validation of both, structured logging, error capture and an optional recorder hook (persists
an AgentRun row). Agent logic lives in `execute` and is unit-testable without a DB or network.
"""
from __future__ import annotations

import inspect
import logging
import time
import traceback
from collections.abc import Callable
from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..market.binance import Candle

log = logging.getLogger("cryptonex.agents")


class AgentInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=False)


class AgentOutput(BaseModel):
    agent_id: str = ""
    passed: bool = False
    score: float = Field(0.0, ge=0.0, le=1.0)  # normalized evidence strength, NOT a probability
    reasons: list[str] = []
    error: str | None = None


class MarketSnapshot(AgentInput):
    """Closed candles per timeframe for one symbol. Shared input of the analysis agents."""

    symbol: str
    candles: dict[str, list[Candle]]
    last_price: float = Field(gt=0)

    def closes(self, tf: str) -> list[float]:
        return [c.close for c in self.candles[tf]]

    def highs(self, tf: str) -> list[float]:
        return [c.high for c in self.candles[tf]]

    def lows(self, tf: str) -> list[float]:
        return [c.low for c in self.candles[tf]]

    def volumes(self, tf: str) -> list[float]:
        return [c.volume for c in self.candles[tf]]


I = TypeVar("I", bound=AgentInput)
O = TypeVar("O", bound=AgentOutput)
Recorder = Callable[[str, str | None, float, str, AgentOutput | None, str | None], None]


class BaseAgent(Generic[I, O]):
    id: ClassVar[str]
    number: ClassVar[int]
    name: ClassVar[str]
    responsibility: ClassVar[str]
    Input: ClassVar[type[AgentInput]]
    Output: ClassVar[type[AgentOutput]]

    def __init__(self, recorder: Recorder | None = None):
        self.recorder = recorder

    def execute(self, inp: I) -> O:  # pragma: no cover - abstract
        raise NotImplementedError

    def _failure(self, msg: str) -> O:
        return self.Output.model_construct(agent_id=self.id, passed=False, score=0.0, reasons=[msg], error=msg)  # type: ignore[return-value]

    def _finish(self, inp: Any, out: Any, t0: float) -> O:
        symbol = getattr(inp, "symbol", None)
        dur = (time.perf_counter() - t0) * 1000
        if isinstance(out, AgentOutput):
            try:
                out = self.Output.model_validate(out.model_dump())
            except ValidationError as e:
                out = self._failure(f"invalid output: {e.errors()[:2]}")
            out.agent_id = self.id
        status = "error" if out.error else "ok"
        log.info("agent=%s symbol=%s passed=%s score=%.2f dur=%.1fms", self.id, symbol, out.passed, out.score, dur)
        if self.recorder:
            try:
                self.recorder(self.id, symbol, dur, status, out, out.error)
            except Exception:  # recording must never break analysis
                log.exception("recorder failed for %s", self.id)
        return out

    def run(self, inp: I) -> O:
        t0 = time.perf_counter()
        try:
            inp = self.Input.model_validate(inp) if not isinstance(inp, self.Input) else inp  # type: ignore[assignment]
            out = self.execute(inp)
        except ValidationError as e:
            out = self._failure(f"invalid input: {e.errors()[:2]}")
        except Exception as e:
            log.error("agent %s failed: %s\n%s", self.id, e, traceback.format_exc())
            out = self._failure(f"{type(e).__name__}: {e}")
        return self._finish(inp, out, t0)

    async def arun(self, inp: I) -> O:
        t0 = time.perf_counter()
        try:
            inp = self.Input.model_validate(inp) if not isinstance(inp, self.Input) else inp  # type: ignore[assignment]
            res = self.execute(inp)
            out = await res if inspect.isawaitable(res) else res
        except ValidationError as e:
            out = self._failure(f"invalid input: {e.errors()[:2]}")
        except Exception as e:
            log.error("agent %s failed: %s", self.id, e)
            out = self._failure(f"{type(e).__name__}: {e}")
        return self._finish(inp, out, t0)

    @classmethod
    def describe(cls) -> dict:
        return {
            "id": cls.id, "number": cls.number, "name": cls.name, "responsibility": cls.responsibility,
            "input_schema": cls.Input.model_json_schema() if cls.Input is not MarketSnapshot else {"title": "MarketSnapshot"},
            "output_schema": cls.Output.model_json_schema(),
        }


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))
