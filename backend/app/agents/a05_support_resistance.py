"""AGENT 05 — SUPPORT AND RESISTANCE: swing-point levels, breakout zones, invalidation level, room to target."""
from __future__ import annotations

from ..market import indicators as ta
from ..market.structure import cluster_levels, swing_highs, swing_lows
from .base import AgentOutput, BaseAgent, MarketSnapshot, clamp01


class SRInput(MarketSnapshot):
    target_min_pct: float = 3.0
    max_stop_distance_pct: float = 2.5


class SROutput(AgentOutput):
    supports: list[dict] = []
    resistances: list[dict] = []
    nearest_support: float | None = None
    nearest_resistance: float | None = None
    room_to_resistance_pct: float | None = None
    invalidation_level: float | None = None
    breakout: bool = False


class SupportResistance(BaseAgent[SRInput, SROutput]):
    id = "support_resistance"
    number = 5
    name = "Support & Resistance"
    responsibility = "Detect support/resistance from 1h and 4h swing points, breakout zones and the trade invalidation level."
    Input = SRInput
    Output = SROutput

    def execute(self, inp: SRInput) -> SROutput:
        if len(inp.candles.get("1h", [])) < 60 or len(inp.candles.get("4h", [])) < 30:
            return SROutput(passed=False, reasons=["insufficient history for structure"])
        price = inp.last_price
        pts: list[float] = []
        for tf, w in (("1h", 3), ("4h", 2)):
            hs, ls = inp.highs(tf)[-200:], inp.lows(tf)[-200:]
            pts += [p for _, p in swing_highs(hs, w)] + [p for _, p in swing_lows(ls, w)]
        levels = cluster_levels(pts, 0.5)
        supports = sorted([lv for lv in levels if lv["price"] < price * 0.998], key=lambda x: -x["price"])
        resistances = sorted([lv for lv in levels if lv["price"] > price * 1.002], key=lambda x: x["price"])
        atr1h = ta.last(ta.atr(inp.highs("1h"), inp.lows("1h"), inp.closes("1h"), 14)) or 0.0
        ns = supports[0]["price"] if supports else None
        nr = resistances[0]["price"] if resistances else None
        # Breakout: last close above the highest high of the previous 20 4h candles (excluding the last 2).
        h4 = inp.highs("4h")
        prior_high = max(h4[-22:-2])
        breakout = inp.closes("4h")[-1] > prior_high
        invalidation = (ns - 0.25 * atr1h) if ns else None
        room = ta.pct(nr, price) if nr else None
        reasons = []
        reasons.append(f"nearest support {ns:.8g}" if ns else "no support found below price")
        reasons.append(f"nearest resistance {nr:.8g} ({room:.2f}% away)" if nr else "no overhead resistance in lookback (price discovery)")
        if breakout:
            reasons.append(f"4h breakout above prior range high {prior_high:.8g}")
        room_ok = room is None or room >= inp.target_min_pct
        stop_dist = ta.pct(price, invalidation) if invalidation else None
        stop_ok = stop_dist is not None and stop_dist <= inp.max_stop_distance_pct * 1.6
        if not room_ok:
            reasons.append(f"resistance blocks the {inp.target_min_pct}% target")
        if not stop_ok:
            reasons.append("no nearby structural invalidation level")
        score = clamp01(0.5 * (1.0 if room_ok else clamp01((room or 0) / inp.target_min_pct))
                        + 0.3 * (1.0 if stop_ok else 0.0) + 0.2 * (1.0 if breakout else 0.0))
        return SROutput(passed=room_ok and stop_ok, score=round(score, 4), supports=supports[:5], resistances=resistances[:5],
                        nearest_support=ns, nearest_resistance=nr, room_to_resistance_pct=room,
                        invalidation_level=invalidation, breakout=breakout, reasons=reasons)
