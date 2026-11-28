"""AGENT 03 — TREND DETECTOR: direction & strength on 15m/1h/4h/1d, sideways detection, conflict rejection."""
from __future__ import annotations

from ..market import indicators as ta
from ..trading_config import IndicatorConfig
from .base import AgentOutput, BaseAgent, MarketSnapshot, clamp01

TF_WEIGHTS = {"15m": 0.15, "1h": 0.30, "4h": 0.35, "1d": 0.20}


class TrendInput(MarketSnapshot):
    config: IndicatorConfig = IndicatorConfig()


class TrendOutput(AgentOutput):
    timeframes: dict[str, dict] = {}
    conflicts: list[str] = []
    sideways: bool = False


def classify(closes: list[float], highs: list[float], lows: list[float]) -> dict:
    e21, e50 = ta.ema(closes, 21), ta.ema(closes, 50)
    a = ta.adx(highs, lows, closes, 14)
    e21v, e50v, e50p = ta.last(e21), ta.last(e50), ta.last(e50, 5)
    adxv = ta.last(a)
    if None in (e21v, e50v, e50p):
        return {"direction": "unknown", "adx": adxv}
    c = closes[-1]
    sep = (e21v - e50v) / e50v * 100  # EMA separation, %
    slope = ta.pct(e50v, e50p)  # EMA50 change over 5 bars, %
    # Minimum separation/slope thresholds stop float noise in flat markets from reading as a trend.
    if sep > 0.1 and c > e50v and slope > 0.05:
        d = "up"
    elif sep < -0.1 and c < e50v and slope < -0.05:
        d = "down"
    else:
        d = "sideways"
    return {"direction": d, "adx": adxv, "ema21": e21v, "ema50": e50v, "ema50_slope_pct": ta.pct(e50v, e50p)}


class TrendDetector(BaseAgent[TrendInput, TrendOutput]):
    id = "trend_detector"
    number = 3
    name = "Trend Detector"
    responsibility = "Identify direction and strength (EMA structure + ADX) on 15m/1h/4h/1d; reject sideways or conflicting trends."
    Input = TrendInput
    Output = TrendOutput

    def execute(self, inp: TrendInput) -> TrendOutput:
        tfs = {tf: classify(inp.closes(tf), inp.highs(tf), inp.lows(tf)) for tf in TF_WEIGHTS if tf in inp.candles}
        reasons: list[str] = []
        if "1h" not in tfs or "4h" not in tfs or tfs["1h"]["direction"] == "unknown":
            return TrendOutput(passed=False, reasons=["missing 1h/4h trend data"], timeframes=tfs)
        conflicts = [tf for tf, v in tfs.items() if v["direction"] == "down"]
        adx1h = tfs["1h"].get("adx") or 0.0
        sideways = tfs["1h"]["direction"] == "sideways" or adx1h < inp.config.adx_min
        score = 0.0
        for tf, w in TF_WEIGHTS.items():
            d = tfs.get(tf, {}).get("direction")
            score += w * {"up": 1.0, "sideways": 0.4, "down": 0.0}.get(d, 0.4)
        strength = clamp01((adx1h - 10) / 30)
        score = clamp01(score * (0.7 + 0.3 * strength))
        passed = (tfs["1h"]["direction"] == "up" and tfs["4h"]["direction"] == "up"
                  and tfs.get("1d", {}).get("direction") != "down" and not sideways)
        for tf, v in tfs.items():
            reasons.append(f"{tf}: {v['direction']} (ADX {v['adx']:.1f})" if v.get("adx") else f"{tf}: {v['direction']}")
        if conflicts:
            reasons.append("conflicting bearish timeframes: " + ", ".join(conflicts))
        if sideways:
            reasons.append(f"1h market sideways/weak (ADX {adx1h:.1f} < {inp.config.adx_min})")
        return TrendOutput(passed=passed, score=round(score, 4), timeframes=tfs, conflicts=conflicts, sideways=sideways,
                           reasons=reasons)
