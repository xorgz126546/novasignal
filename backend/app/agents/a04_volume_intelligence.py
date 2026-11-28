"""AGENT 04 — VOLUME INTELLIGENCE: relative volume, abnormal volume, OBV trend, liquidity (24h volume + spread)."""
from __future__ import annotations

import statistics

from .base import AgentOutput, BaseAgent, MarketSnapshot, clamp01


class VolumeInput(MarketSnapshot):
    quote_volume_24h: float
    bid: float | None = None
    ask: float | None = None
    min_quote_volume: float = 20_000_000
    max_spread_pct: float = 0.10


class VolumeOutput(AgentOutput):
    relative_volume: float | None = None
    volume_zscore: float | None = None
    abnormal: bool = False
    abnormal_on_bearish_candle: bool = False
    obv_rising: bool | None = None
    spread_pct: float | None = None
    liquidity_ok: bool = False


class VolumeIntelligence(BaseAgent[VolumeInput, VolumeOutput]):
    id = "volume_intelligence"
    number = 4
    name = "Volume Intelligence"
    responsibility = "Compare current to historical volume, detect abnormal/distribution volume, assess liquidity and spread."
    Input = VolumeInput
    Output = VolumeOutput

    def execute(self, inp: VolumeInput) -> VolumeOutput:
        candles = inp.candles.get("1h", [])
        if len(candles) < 60:
            return VolumeOutput(passed=False, reasons=["insufficient 1h volume history"])
        vols = [c.volume for c in candles]
        base = vols[-23:-3]
        base_mean = statistics.fmean(base) if base else 0
        rel = statistics.fmean(vols[-3:]) / base_mean if base_mean > 0 else 0.0
        hist = vols[-53:-3]
        sd = statistics.pstdev(hist) if len(hist) > 1 else 0
        z = (vols[-1] - statistics.fmean(hist)) / sd if sd > 0 else 0.0
        abnormal = z > 3.0
        last = candles[-1]
        bearish_spike = abnormal and last.close < last.open
        obv = 0.0
        obv_series = []
        for i in range(1, len(candles)):
            if candles[i].close > candles[i - 1].close:
                obv += candles[i].volume
            elif candles[i].close < candles[i - 1].close:
                obv -= candles[i].volume
            obv_series.append(obv)
        obv_rising = obv_series[-1] > obv_series[-21] if len(obv_series) > 21 else None
        spread = None
        if inp.bid and inp.ask:
            spread = (inp.ask - inp.bid) / ((inp.ask + inp.bid) / 2) * 100
        liquidity_ok = inp.quote_volume_24h >= inp.min_quote_volume and (spread is None or spread <= inp.max_spread_pct)
        reasons = [f"relative volume (3h vs 20h) = {rel:.2f}x", f"last-candle volume z-score = {z:.2f}",
                   f"24h quote volume = {inp.quote_volume_24h:,.0f} USDT"]
        if spread is not None:
            reasons.append(f"spread = {spread:.4f}%")
        else:
            reasons.append("spread unavailable (bid/ask not fetched)")
        if bearish_spike:
            reasons.append("abnormal volume on a bearish candle (possible distribution)")
        if obv_rising is not None:
            reasons.append(f"OBV 20h trend {'rising' if obv_rising else 'falling'}")
        score = clamp01(0.4 * clamp01(rel / 1.5) + 0.3 * (1.0 if obv_rising else 0.0) + 0.3 * (1.0 if liquidity_ok else 0.0))
        if bearish_spike:
            score *= 0.3
        passed = liquidity_ok and rel >= 0.8 and not bearish_spike
        return VolumeOutput(passed=passed, score=round(score, 4), relative_volume=round(rel, 4), volume_zscore=round(z, 3),
                            abnormal=abnormal, abnormal_on_bearish_candle=bearish_spike, obv_rising=obv_rising,
                            spread_pct=spread, liquidity_ok=liquidity_ok, reasons=reasons)
