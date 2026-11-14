"""Five more strategy plug-ins, so the company has setups for trending, ranging AND falling markets.

All are long-only Binance Spot, deterministic, and go through the same plan_levels (ONE take-profit, stop
bounds, minimum net R:R) and the same risk veto. Their evidence comes from the Backtester tournament.
"""
from __future__ import annotations

from ...trading_config import RiskConfig
from ..features import Context
from .base import Reject, Setup, Strategy, plan_levels


def _q(checks: dict) -> float:
    return (1.0 + sum(float(v) for v in checks.values())) / (1 + len(checks))


def _yn(checks: dict) -> list[str]:
    return [f"{k}: {'yes' if v else 'no'}" for k, v in checks.items()]


class SqueezeBreakout(Strategy):
    """Bollinger-band squeeze (volatility compression) followed by a 1H close above the 20-bar high on volume."""
    id = "squeeze_breakout"
    name = "Volatility Squeeze Breakout"
    default_params = {"min_vol_rel": 1.3, "stop_atr": 0.2, "rsi_max": 75}

    def evaluate(self, ctx: Context, p: dict, risk: RiskConfig) -> Setup | Reject:
        f = ctx.features
        cs = ctx.candles.get("1h", [])
        need = ("squeeze_recent_1h", "donchian_high20_1h", "atr_1h", "vol_rel_1h", "rsi_1h")
        if len(cs) < 80 or any(k not in f for k in need):
            return Reject(self.id, "insufficient data")
        if not f["squeeze_recent_1h"]:
            return Reject(self.id, "no recent volatility squeeze (BB width not in its lowest 20%)")
        if cs[-1].close <= f["donchian_high20_1h"]:
            return Reject(self.id, "no close above the 20-bar high yet")
        if f["vol_rel_1h"] < p["min_vol_rel"]:
            return Reject(self.id, f"breakout volume {f['vol_rel_1h']:.2f}x < {p['min_vol_rel']}x")
        if f["rsi_1h"] > p["rsi_max"] or f.get("bias_4h", 0) < 0:
            return Reject(self.id, "overextended (RSI) or 4H structure bearish")
        checks = {"4h_bias_up": f.get("bias_4h", 0) > 0, "macd_1h_positive": f.get("macd_hist_1h", 0) > 0,
                  "volume_strong": f["vol_rel_1h"] >= 2.0}
        stop = min(c.low for c in cs[-3:]) - p["stop_atr"] * f["atr_1h"]
        return plan_levels(self.id, ctx, cs[-1].close, stop, f["donchian_high20_1h"] * 0.995, _q(checks),
                           [f"volatility squeeze resolved upward: close above 20-bar high {f['donchian_high20_1h']:.8g}",
                            f"breakout volume {f['vol_rel_1h']:.2f}x"] + _yn(checks), risk)


class DonchianBreakout(Strategy):
    """Classic 4H channel breakout (Turtle-style) in a trending 4H market, entered on the 1H."""
    id = "donchian_breakout"
    name = "4H Donchian Breakout"
    default_params = {"adx_min": 20, "stop_atr": 1.5}

    def evaluate(self, ctx: Context, p: dict, risk: RiskConfig) -> Setup | Reject:
        f = ctx.features
        cs = ctx.candles.get("1h", [])
        need = ("donchian_high20_4h", "close_4h", "adx_4h", "ema21_4h", "ema50_4h", "atr_1h")
        if len(cs) < 80 or any(k not in f for k in need):
            return Reject(self.id, "insufficient data")
        if f["close_4h"] <= f["donchian_high20_4h"]:
            return Reject(self.id, "4H close not above the 20-bar 4H high")
        if f["adx_4h"] < p["adx_min"] or f["ema21_4h"] <= f["ema50_4h"]:
            return Reject(self.id, f"4H trend too weak (ADX {f['adx_4h']:.1f}) or EMA21 below EMA50")
        checks = {"volume_4h": f.get("vol_rel_4h", 0) >= 1.0, "rsi_4h_not_extreme": f.get("rsi_4h", 100) < 78,
                  "1h_above_ema21": cs[-1].close > f.get("ema21_1h", float("inf"))}
        stop = cs[-1].close - p["stop_atr"] * f["atr_1h"]
        return plan_levels(self.id, ctx, cs[-1].close, stop, f["donchian_high20_4h"] * 0.99, _q(checks),
                           [f"4H breakout above the 20-bar high {f['donchian_high20_4h']:.8g}",
                            f"4H ADX {f['adx_4h']:.1f}, EMA21 > EMA50"] + _yn(checks), risk)


class RelativeStrength(Strategy):
    """Coins clearly outperforming BTC over 7 days, bought on a pullback to the 1H EMA21.
    These 'decoupled leaders' are the only longs the Risk Manager may allow when BTC is risk-off (at half risk)."""
    id = "relative_strength"
    name = "Relative Strength Leader"
    default_params = {"min_outperformance_pct": 5.0, "max_dist_ema21_pct": 1.5, "stop_atr": 0.25}

    def evaluate(self, ctx: Context, p: dict, risk: RiskConfig) -> Setup | Reject:
        f = ctx.features
        cs = ctx.candles.get("1h", [])
        btc = ctx.extras.get("btc_ret_7d")
        need = ("ret_168_1h", "close_4h", "ema50_4h", "ema21_1h", "ema50_1h", "atr_1h", "rsi_1h", "dist_ema21_pct_1h")
        if len(cs) < 180 or any(k not in f for k in need) or btc is None:
            return Reject(self.id, "insufficient data (needs 7 days and BTC context)")
        if ctx.symbol == "BTCUSDT":
            return Reject(self.id, "BTC cannot outperform itself")
        out = f["ret_168_1h"] - btc
        if out < p["min_outperformance_pct"]:
            return Reject(self.id, f"7-day outperformance vs BTC {out:+.2f}% < {p['min_outperformance_pct']}%")
        if not (f["close_4h"] > f["ema50_4h"] and f["ema21_1h"] > f["ema50_1h"]):
            return Reject(self.id, "own trend not up (4H close > EMA50 and 1H EMA21 > EMA50 required)")
        if not (-0.5 <= f["dist_ema21_pct_1h"] <= p["max_dist_ema21_pct"]) or not (45 <= f["rsi_1h"] <= 68):
            return Reject(self.id, "not at a pullback entry (price vs EMA21 / RSI band)")
        checks = {"4h_bias_up": f.get("bias_4h", 0) > 0, "macd_1h_positive": f.get("macd_hist_1h", 0) > 0,
                  "strong_outperformance": out >= 2 * p["min_outperformance_pct"]}
        stop = min(c.low for c in cs[-6:]) - p["stop_atr"] * f["atr_1h"]
        return plan_levels(self.id, ctx, cs[-1].close, stop, f["ema50_1h"] * 0.997, _q(checks),
                           [f"outperformed BTC by {out:+.2f}% over 7 days (decoupled leader)",
                            "pullback to the 1H EMA21 inside its own uptrend"] + _yn(checks), risk)


class OversoldBounce(Strategy):
    """Mean reversion: 1H RSI was oversold (<30), has recovered, and a bullish candle closed back inside the bands."""
    id = "oversold_bounce"
    name = "Oversold Bounce"
    default_params = {"rsi_oversold": 30, "rsi_recovered": 35, "stop_atr": 0.2, "lookback": 8}

    def evaluate(self, ctx: Context, p: dict, risk: RiskConfig) -> Setup | Reject:
        f = ctx.features
        cs = ctx.candles.get("1h", [])
        need = ("rsi_min6_1h", "rsi_1h", "bb_lower_1h", "atr_1h", "rsi_change3_1h")
        if len(cs) < 80 or any(k not in f for k in need):
            return Reject(self.id, "insufficient data")
        if f["rsi_min6_1h"] >= p["rsi_oversold"]:
            return Reject(self.id, f"RSI(1h) was not oversold recently (min {f['rsi_min6_1h']:.1f})")
        if f["rsi_1h"] < p["rsi_recovered"] or f["rsi_change3_1h"] <= 0:
            return Reject(self.id, "RSI has not recovered / still falling")
        last = cs[-1]
        if not (last.close > last.open and last.close > f["bb_lower_1h"] and last.close > cs[-2].high):
            return Reject(self.id, "no bullish reversal candle closing above the prior high")
        h4 = ctx.candles.get("4h", [])
        if len(h4) >= 3 and h4[-1].low < min(c.low for c in h4[-4:-1]) * 0.99:
            return Reject(self.id, "4H still printing fresh lows (falling knife)")
        checks = {"volume_on_reversal": f.get("vol_rel_1h", 0) >= 1.2, "4h_rsi_oversold_context": f.get("rsi_4h", 50) < 45,
                  "bullish_choch_15m_or_1h": bool(f.get("choch_up_recent_15m") or f.get("choch_up_recent_1h"))}
        low = min(c.low for c in cs[-p["lookback"]:])
        stop = low - p["stop_atr"] * f["atr_1h"]
        return plan_levels(self.id, ctx, last.close, stop, low, _q(checks),
                           [f"RSI(1h) recovered from oversold ({f['rsi_min6_1h']:.1f} → {f['rsi_1h']:.1f})",
                            "bullish candle closed above the previous high"] + _yn(checks), risk)


class RangeSupport(Strategy):
    """In a sideways 4H market, buy a held test of 1H range support with a bullish close."""
    id = "range_support"
    name = "Range Support Bounce"
    default_params = {"adx_max": 20, "touch_tol_pct": 0.5, "stop_atr": 0.4}

    def evaluate(self, ctx: Context, p: dict, risk: RiskConfig) -> Setup | Reject:
        f = ctx.features
        cs = ctx.candles.get("1h", [])
        if len(cs) < 80 or "adx_4h" not in f or "atr_1h" not in f:
            return Reject(self.id, "insufficient data")
        if f["adx_4h"] >= p["adx_max"]:
            return Reject(self.id, f"4H is trending (ADX {f['adx_4h']:.1f}), not a range")
        price = cs[-1].close
        supports = sorted([lv for lv in ctx.structure.get("1h", {}).get("swing_lows", []) if lv < price], reverse=True)
        for S in supports[:3]:
            touched = any(c.low <= S * (1 + p["touch_tol_pct"] / 100) and c.close > S for c in cs[-3:])
            if not touched:
                continue
            if not (cs[-1].close > cs[-1].open):
                return Reject(self.id, f"touched support {S:.8g} but no bullish close")
            checks = {"rsi_below_50": f.get("rsi_1h", 50) < 50, "volume_ok": f.get("vol_rel_1h", 0) >= 1.0,
                      "15m_turning_up": f.get("rsi_change3_15m", 0) > 0}
            stop = S - p["stop_atr"] * f["atr_1h"]
            return plan_levels(self.id, ctx, price, stop, S * 0.997, _q(checks),
                               [f"sideways 4H market (ADX {f['adx_4h']:.1f})", f"held 1H range support {S:.8g}"] + _yn(checks), risk)
        return Reject(self.id, "price is not testing a 1H range support")


EXTRA = [SqueezeBreakout(), DonchianBreakout(), RelativeStrength(), OversoldBounce(), RangeSupport()]
