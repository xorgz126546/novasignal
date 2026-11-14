"""Declarative rule strategies — the SAFE way to turn outside ideas into testable strategies.

A rule spec is plain data: comparisons over whitelisted, deterministically computed features plus a stop
and invalidation recipe. It is validated strictly and evaluated by this module; no code from the spec
is ever executed.
"""
from __future__ import annotations

import operator

from pydantic import BaseModel, Field, field_validator

from ...trading_config import RiskConfig
from ..features import FEATURE_NAMES, Context
from .base import Reject, Setup, Strategy, plan_levels

OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}


class Condition(BaseModel):
    left: str
    op: str
    right: str | float

    @field_validator("left")
    @classmethod
    def _left(cls, v):
        if v not in FEATURE_NAMES:
            raise ValueError(f"unknown feature {v!r}")
        return v

    @field_validator("op")
    @classmethod
    def _op(cls, v):
        if v not in OPS:
            raise ValueError(f"operator must be one of {sorted(OPS)}")
        return v

    @field_validator("right")
    @classmethod
    def _right(cls, v):
        if isinstance(v, str) and v not in FEATURE_NAMES:
            raise ValueError(f"unknown feature {v!r}")
        if isinstance(v, float) and abs(v) > 1e9:
            raise ValueError("number out of range")
        return v


class StopRule(BaseModel):
    type: str = Field(pattern="^(atr|swing_low)$")
    tf: str = Field("1h", pattern="^(1h|4h)$")
    mult: float = Field(1.5, gt=0, le=5)  # atr: entry - mult*ATR ; swing_low: low - mult*ATR buffer
    lookback: int = Field(6, ge=2, le=50)


class RuleSpec(BaseModel):
    name: str = Field(min_length=3, max_length=80)
    description: str = Field("", max_length=600)
    conditions: list[Condition] = Field(min_length=1, max_length=12)
    stop: StopRule
    invalidation_buffer_pct: float = Field(0.2, ge=0, le=3)
    quality: float = Field(0.65, ge=0.3, le=0.9)


class RuleStrategy(Strategy):
    def __init__(self, slug: str, spec: RuleSpec):
        self.id = f"rule:{slug}"
        self.name = spec.name
        self.spec = spec
        self.default_params = {}

    def evaluate(self, ctx: Context, p: dict, risk: RiskConfig) -> Setup | Reject:
        f = ctx.features
        for c in self.spec.conditions:
            if c.left not in f or (isinstance(c.right, str) and c.right not in f):
                return Reject(self.id, f"feature unavailable for {c.left}")
            rv = f[c.right] if isinstance(c.right, str) else c.right
            if not OPS[c.op](f[c.left], rv):
                return Reject(self.id, f"condition failed: {c.left} {c.op} {c.right}")
        tf = self.spec.stop.tf
        atr = f.get(f"atr_{tf}")
        cs = ctx.candles.get(tf, [])
        if not atr or not cs:
            return Reject(self.id, "no ATR/candles for stop")
        entry = cs[-1].close if tf == "1h" else ctx.candles["1h"][-1].close
        if self.spec.stop.type == "atr":
            stop = entry - self.spec.stop.mult * atr
        else:
            stop = min(c.low for c in cs[-self.spec.stop.lookback:]) - self.spec.stop.mult * atr * 0.2
        inval = stop * (1 + self.spec.invalidation_buffer_pct / 100)
        reasons = [f"rule '{self.spec.name}': all {len(self.spec.conditions)} conditions met"] + \
                  [f"{c.left} {c.op} {c.right}" for c in self.spec.conditions[:6]]
        return plan_levels(self.id, ctx, entry, stop, inval, self.spec.quality, reasons, risk)
