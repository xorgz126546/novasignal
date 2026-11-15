"""AGENT 02 — TECHNICAL ANALYST: RSI, MACD, EMA 9/21/50/200, Bollinger Bands, ATR on multiple timeframes."""
from __future__ import annotations

from ..market import indicators as ta
from ..trading_config import IndicatorConfig
from .base import AgentOutput, BaseAgent, MarketSnapshot


class TechnicalInput(MarketSnapshot):
    config: IndicatorConfig = IndicatorConfig()


class TechnicalOutput(AgentOutput):
    indicators: dict[str, dict] = {}
    checks: dict[str, bool] = {}


def compute_indicators(snap: MarketSnapshot, tf: str, cfg: IndicatorConfig) -> dict:
    c, h, lo = snap.closes(tf), snap.highs(tf), snap.lows(tf)
    line, sig, hist = ta.macd(c, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
    up, mid, low = ta.bollinger(c, cfg.bb_period, cfg.bb_std)
    a = ta.atr(h, lo, c, cfg.atr_period)
    out = {
        "close": c[-1],
        "rsi": ta.last(ta.rsi(c, cfg.rsi_period)),
        "macd": ta.last(line), "macd_signal": ta.last(sig), "macd_hist": ta.last(hist), "macd_hist_prev": ta.last(hist, 1),
        "bb_upper": ta.last(up), "bb_mid": ta.last(mid), "bb_lower": ta.last(low),
        "atr": ta.last(a),
    }
    for p in cfg.ema_periods:
        out[f"ema{p}"] = ta.last(ta.ema(c, p))
    out["atr_pct"] = out["atr"] / c[-1] * 100 if out["atr"] else None
    return out


class TechnicalAnalyst(BaseAgent[TechnicalInput, TechnicalOutput]):
    id = "technical_analyst"
    number = 2
    name = "Technical Analyst"
    responsibility = "Compute RSI, MACD, EMA 9/21/50/200, Bollinger Bands and ATR across timeframes and score bullish confluence."
    Input = TechnicalInput
    Output = TechnicalOutput

    def execute(self, inp: TechnicalInput) -> TechnicalOutput:
        cfg = inp.config
        ptf = cfg.primary_timeframe
        inds = {tf: compute_indicators(inp, tf, cfg) for tf in inp.candles if len(inp.candles[tf]) >= 30}
        p = inds.get(ptf)
        if not p or any(p.get(k) is None for k in ("rsi", "macd_hist", "ema21", "ema50", "atr")):
            return TechnicalOutput(passed=False, reasons=[f"insufficient {ptf} history"], indicators=inds)
        h4 = inds.get("4h", {})
        checks = {
            "rsi_in_bullish_band": cfg.rsi_min <= p["rsi"] <= cfg.rsi_max,
            "macd_hist_positive": p["macd_hist"] > 0,
            "macd_hist_rising": p["macd_hist_prev"] is not None and p["macd_hist"] > p["macd_hist_prev"],
            "ema_stack_bullish": p["close"] > p["ema21"] > p["ema50"],
            "above_ema200": p.get("ema200") is not None and p["close"] > p["ema200"],
            "not_overextended_bb": p["bb_upper"] is not None and p["close"] < p["bb_upper"],
            "htf_4h_above_ema50": bool(h4.get("ema50")) and h4["close"] > h4["ema50"],
            "htf_4h_rsi_not_overbought": h4.get("rsi") is not None and h4["rsi"] < 75,
        }
        score = sum(checks.values()) / len(checks)
        hard_fail = not checks["rsi_in_bullish_band"] or not checks["ema_stack_bullish"]
        passed = score >= 0.625 and not hard_fail
        reasons = [f"{k}: {'yes' if v else 'no'}" for k, v in checks.items()]
        reasons.insert(0, f"RSI({cfg.rsi_period}) {ptf}={p['rsi']:.1f}, MACD hist={p['macd_hist']:.6g}, ATR%={p['atr_pct']:.2f}")
        return TechnicalOutput(passed=passed, score=round(score, 4), indicators=inds, checks=checks, reasons=reasons)
