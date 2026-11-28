"""Momentum ignition — catching a move in its FIRST days (the measurable version of "before it trends").

Research on real Binance data (79 liquid USDT coins, ~5.5 months of 4H bars, entry on the next bar open, fees and
slippage included) found NO edge in "quiet accumulation" signals, but a consistent one here: a coin up +10…+40% over
the last 3 days with daily volume ≥ 1.5× its 20-day average kept going more often than the average coin, and a
12% target / 6% stop was net positive in BOTH halves of the period. That is a hypothesis with evidence, not a promise:
the weekly tournament re-tests it and grades it like every other strategy.
"""
from __future__ import annotations

import statistics

from ...trading_config import RiskConfig
from ..features import Context
from .base import Reject, Setup, Strategy, plan_levels


def ignition_stats(c4: list, d: list) -> tuple[float, float] | None:
    """(3-day % change on 4H closes, 3-day daily volume vs its 20-day average). Closed candles only."""
    if len(c4) < 20 or len(d) < 22:
        return None
    r3 = (c4[-1].close - c4[-19].close) / c4[-19].close * 100
    base = statistics.fmean(c.quote_volume for c in d[-21:-1])
    vol3 = statistics.fmean(c.quote_volume for c in d[-3:]) / base if base > 0 else 0.0
    return r3, vol3


class MomentumIgnition(Strategy):
    id = "momentum_ignition"
    name = "Momentum Ignition (3-day move + volume)"
    profile = "momentum"
    needs_features = False  # uses raw candles only → fast backtests
    requires_approval = True  # off until the owner approves it (Telegram ✅) after seeing the gate result
    default_params = {"ret3_min": 10.0, "ret3_max": 40.0, "vol_min": 1.5, "stop_pct": 6.0}

    def evaluate(self, ctx: Context, p: dict, risk: RiskConfig) -> Setup | Reject:
        st = ignition_stats(ctx.candles.get("4h", []), ctx.candles.get("1d", []))
        if st is None:
            return Reject(self.id, "insufficient 4H/1D history")
        r3, vol3 = st
        if r3 < p["ret3_min"]:
            return Reject(self.id, f"no ignition: {r3:+.1f}% in 3 days (< +{p['ret3_min']:.0f}%)")
        if r3 > p["ret3_max"]:
            return Reject(self.id, f"already parabolic: {r3:+.1f}% in 3 days (> +{p['ret3_max']:.0f}%)")
        if vol3 < p["vol_min"]:
            return Reject(self.id, f"move without volume: 3-day volume {vol3:.1f}× (< {p['vol_min']}×)")
        entry = ctx.last_price
        stop = entry * (1 - p["stop_pct"] / 100)
        inval = entry * (1 - p["stop_pct"] * 0.8 / 100)
        reasons = [f"momentum ignition: {r3:+.1f}% in 3 days with volume {vol3:.1f}× its 20-day average",
                   "trend in its first days: the plan rides the continuation with ONE target and a fixed stop",
                   "high-volatility setup: wider stop, smaller position (risk % unchanged)"]
        return plan_levels(self.id, ctx, entry, stop, inval, 0.7, reasons, risk)


MOMENTUM = [MomentumIgnition()]
