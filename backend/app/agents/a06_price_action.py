"""AGENT 06 — PRICE ACTION ANALYST: candlestick patterns, HH/HL structure, breakout-retest, momentum."""
from __future__ import annotations

from ..market.structure import detect_patterns, swing_highs, swing_lows
from .base import AgentOutput, BaseAgent, MarketSnapshot, clamp01


class PriceActionInput(MarketSnapshot):
    pass


class PriceActionOutput(AgentOutput):
    patterns: list[dict] = []
    structure: str = "unknown"  # bullish|bearish|mixed
    retest_confirmed: bool = False
    momentum_pct_5: float | None = None


class PriceActionAnalyst(BaseAgent[PriceActionInput, PriceActionOutput]):
    id = "price_action"
    number = 6
    name = "Price Action Analyst"
    responsibility = "Analyze candlestick patterns, market structure (HH/HL), breakouts/retests and momentum on 1h."
    Input = PriceActionInput
    Output = PriceActionOutput

    def execute(self, inp: PriceActionInput) -> PriceActionOutput:
        cs = inp.candles.get("1h", [])
        if len(cs) < 50:
            return PriceActionOutput(passed=False, reasons=["insufficient 1h candles"])
        highs, lows = [c.high for c in cs[-120:]], [c.low for c in cs[-120:]]
        sh, sl = swing_highs(highs, 3), swing_lows(lows, 3)
        structure = "mixed"
        if len(sh) >= 2 and len(sl) >= 2:
            hh, hl = sh[-1][1] > sh[-2][1], sl[-1][1] > sl[-2][1]
            lh, ll = sh[-1][1] < sh[-2][1], sl[-1][1] < sl[-2][1]
            structure = "bullish" if hh and hl else "bearish" if lh and ll else "mixed"
        patterns = detect_patterns(cs)
        bearish_pat = [p["name"] for p in patterns if p["bias"] == "bearish"]
        bullish_pat = [p["name"] for p in patterns if p["bias"] == "bullish"]
        # Breakout-retest: within last 20 candles price closed above prior swing high L, later wicked back to ~L and held.
        retest = False
        if len(sh) >= 2:
            level = sh[-2][1]
            recent = cs[-20:]
            broke = [i for i, c in enumerate(recent) if c.close > level * 1.002]
            if broke:
                after = recent[broke[0] + 1:]
                retest = any(c.low <= level * 1.005 and c.close > level for c in after) and recent[-1].close > level
        mom = (cs[-1].close - cs[-6].close) / cs[-6].close * 100
        reasons = [f"1h structure: {structure}", f"5-candle momentum {mom:+.2f}%"]
        if patterns:
            reasons.append("patterns: " + ", ".join(p["name"] for p in patterns))
        if retest:
            reasons.append("breakout + successful retest")
        score = {"bullish": 0.5, "mixed": 0.25, "bearish": 0.0}[structure]
        score += 0.2 if bullish_pat else 0.0
        score += 0.2 if retest else 0.0
        score += 0.1 if mom > 0 else 0.0
        if bearish_pat:
            score *= 0.4
        passed = structure != "bearish" and not bearish_pat and mom > -1.5
        return PriceActionOutput(passed=passed, score=round(clamp01(score), 4), patterns=patterns, structure=structure,
                                 retest_confirmed=retest, momentum_pct_5=round(mom, 4), reasons=reasons)
