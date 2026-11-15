"""Scalping (15m) and long-term investment (daily) strategy plug-ins.

They use the same deterministic features and the same plan_levels, judged by their profile's rules
(hq/profiles.py): scalp = 1-2% target, invest = 12-35% target. Long only, Binance Spot.
"""
from __future__ import annotations

from ...trading_config import RiskConfig
from ..features import Context
from .base import Reject, Setup, Strategy, plan_levels
from .extra import _q, _yn


# ---------------- scalping (15m) ----------------
class ScalpMomentumPullback(Strategy):
    """15m pullback to EMA21 inside a 15m and 1H uptrend, entered on the bullish reclaim."""
    id = "scalp_pullback"
    name = "Scalp: 15m Momentum Pullback"
    profile = "scalp"
    default_params = {"stop_atr": 0.15, "rsi_min": 48, "rsi_max": 70}

    def evaluate(self, ctx: Context, p: dict, risk: RiskConfig) -> Setup | Reject:
        f = ctx.features
        cs = ctx.candles.get("15m", [])
        need = ("ema9_15m", "ema21_15m", "ema50_15m", "rsi_15m", "atr_15m", "ema21_1h", "ema50_1h")
        if len(cs) < 80 or any(k not in f for k in need):
            return Reject(self.id, "insufficient 15m/1h data")
        if f["ema21_1h"] <= f["ema50_1h"]:
            return Reject(self.id, "1H trend not up (EMA21 ≤ EMA50)")
        if not (f["ema9_15m"] > f["ema21_15m"] > f["ema50_15m"]):
            return Reject(self.id, "15m EMAs not stacked up (9 > 21 > 50)")
        low3 = min(c.low for c in cs[-3:])
        if low3 > f["ema21_15m"] * 1.002:
            return Reject(self.id, "no pullback to the 15m EMA21")
        last = cs[-1]
        if not (last.close > last.open and last.close > f["ema9_15m"]):
            return Reject(self.id, "no bullish 15m reclaim candle")
        if not (p["rsi_min"] <= f["rsi_15m"] <= p["rsi_max"]):
            return Reject(self.id, f"RSI(15m) {f['rsi_15m']:.1f} outside {p['rsi_min']}–{p['rsi_max']}")
        checks = {"1h_rsi_ok": 50 <= f.get("rsi_1h", 0) <= 70, "volume_15m": f.get("vol_rel_15m", 0) >= 1.0,
                  "macd_15m_positive": f.get("macd_hist_15m", 0) > 0}
        stop = low3 - p["stop_atr"] * f["atr_15m"]
        return plan_levels(self.id, ctx, last.close, stop, low3, _q(checks),
                           ["SCALP · 15m pullback to EMA21 reclaimed inside a 1H uptrend"] + _yn(checks), risk)


class ScalpRangeBreakout(Strategy):
    """15m close above the 20-bar high on strong volume, with the 1H not bearish."""
    id = "scalp_breakout"
    name = "Scalp: 15m Range Breakout"
    profile = "scalp"
    default_params = {"min_vol_rel": 1.5, "stop_atr": 1.0}

    def evaluate(self, ctx: Context, p: dict, risk: RiskConfig) -> Setup | Reject:
        f = ctx.features
        cs = ctx.candles.get("15m", [])
        need = ("donchian_high20_15m", "atr_15m", "vol_rel_15m", "rsi_15m")
        if len(cs) < 80 or any(k not in f for k in need):
            return Reject(self.id, "insufficient 15m data")
        if cs[-1].close <= f["donchian_high20_15m"]:
            return Reject(self.id, "no 15m close above the 20-bar high")
        if f["vol_rel_15m"] < p["min_vol_rel"]:
            return Reject(self.id, f"breakout volume {f['vol_rel_15m']:.2f}x < {p['min_vol_rel']}x")
        if f.get("bias_1h", 0) < 0 or f["rsi_15m"] > 78:
            return Reject(self.id, "1H structure bearish or 15m overextended")
        checks = {"1h_ema_up": f.get("ema21_1h", 0) > f.get("ema50_1h", 0), "squeeze_before": bool(f.get("squeeze_recent_15m")),
                  "volume_2x": f["vol_rel_15m"] >= 2.0}
        stop = cs[-1].close - p["stop_atr"] * f["atr_15m"]
        return plan_levels(self.id, ctx, cs[-1].close, stop, f["donchian_high20_15m"] * 0.998, _q(checks),
                           [f"SCALP · 15m breakout above {f['donchian_high20_15m']:.8g} on {f['vol_rel_15m']:.2f}x volume"] + _yn(checks), risk)


# ---------------- long-term investment (daily) ----------------
class InvestTrendReclaim(Strategy):
    """Daily close back above the 200-day EMA after being below it — a classic regime change for spot holders."""
    id = "invest_200d_reclaim"
    name = "Invest: 200-Day Trend Reclaim"
    profile = "invest"
    default_params = {"reclaim_days": 5, "stop_atr": 1.0}

    def evaluate(self, ctx: Context, p: dict, risk: RiskConfig) -> Setup | Reject:
        f = ctx.features
        cs = ctx.candles.get("1d", [])
        if len(cs) < 210 or "ema200_1d" not in f or "atr_1d" not in f:
            return Reject(self.id, "needs 210+ daily candles")
        e200 = f["ema200_1d"]
        if cs[-1].close <= e200:
            return Reject(self.id, f"daily close below the 200-day EMA ({e200:.6g})")
        was_below = any(c.close < e200 for c in cs[-(p["reclaim_days"] + 10):-p["reclaim_days"]])
        recent_cross = any(c.close > e200 for c in cs[-p["reclaim_days"]:])
        if not (was_below and recent_cross):
            return Reject(self.id, "no fresh reclaim of the 200-day EMA")
        checks = {"rsi_1d_healthy": 50 <= f.get("rsi_1d", 0) <= 70, "ema50_1d_rising": f.get("ema50_1d", 0) > f.get("ema200_1d", 0) * 0.97,
                  "weekly_momentum": f.get("macd_hist_1d", 0) > 0}
        low = min(c.low for c in cs[-p["reclaim_days"] - 3:])
        stop = min(low, e200 * 0.97) - p["stop_atr"] * f["atr_1d"] * 0.5
        return plan_levels(self.id, ctx, cs[-1].close, stop, e200 * 0.98, _q(checks),
                           ["INVEST · daily close reclaimed the 200-day EMA (bear → bull regime change)"] + _yn(checks), risk)


class InvestGoldenCross(Strategy):
    """Daily EMA50 crossing above EMA200 recently, bought on a pullback toward the EMA50."""
    id = "invest_golden_cross"
    name = "Invest: Golden Cross Pullback"
    profile = "invest"
    default_params = {"cross_days": 20, "max_dist_ema50_pct": 6.0}

    def evaluate(self, ctx: Context, p: dict, risk: RiskConfig) -> Setup | Reject:
        from ...market import indicators as ta

        f = ctx.features
        cs = ctx.candles.get("1d", [])
        if len(cs) < 230 or "atr_1d" not in f:
            return Reject(self.id, "needs 230+ daily candles")
        closes = [c.close for c in cs]
        e50, e200 = ta.ema(closes, 50), ta.ema(closes, 200)
        n = p["cross_days"]
        crossed = any(e50[-k - 1] is not None and e200[-k - 1] is not None and e50[-k - 1] <= e200[-k - 1] and e50[-k] > e200[-k]
                      for k in range(1, n + 1))
        if not crossed:
            return Reject(self.id, f"no golden cross (EMA50 over EMA200) in the last {n} days")
        dist = (closes[-1] - e50[-1]) / e50[-1] * 100
        if not (-2.0 <= dist <= p["max_dist_ema50_pct"]):
            return Reject(self.id, f"price {dist:+.1f}% from EMA50 (wait for a pullback)")
        checks = {"rsi_1d_ok": 45 <= f.get("rsi_1d", 0) <= 68, "4h_bias_up": f.get("bias_4h", 0) >= 0,
                  "volume_1d": f.get("vol_rel_1d", 0) >= 0.9}
        stop = min(e50[-1], min(c.low for c in cs[-10:])) - f["atr_1d"]
        return plan_levels(self.id, ctx, closes[-1], stop, e200[-1], _q(checks),
                           ["INVEST · daily golden cross (EMA50 > EMA200), entry near the EMA50"] + _yn(checks), risk)


class InvestBullDip(Strategy):
    """Buying a deep daily dip (RSI < 40) while the long-term trend is still up (price above the 200-day EMA)."""
    id = "invest_bull_dip"
    name = "Invest: Bull-Market Dip"
    profile = "invest"
    default_params = {"rsi_max": 40, "stop_atr": 1.5}

    def evaluate(self, ctx: Context, p: dict, risk: RiskConfig) -> Setup | Reject:
        f = ctx.features
        cs = ctx.candles.get("1d", [])
        need = ("ema200_1d", "rsi_min6_1d", "rsi_1d", "atr_1d", "ema50_1d")
        if len(cs) < 210 or any(k not in f for k in need):
            return Reject(self.id, "needs 210+ daily candles")
        if cs[-1].close <= f["ema200_1d"] or f["ema50_1d"] <= f["ema200_1d"]:
            return Reject(self.id, "long-term trend not up (below 200-day EMA or EMA50 < EMA200)")
        if f["rsi_min6_1d"] >= p["rsi_max"]:
            return Reject(self.id, f"no deep dip (daily RSI min {f['rsi_min6_1d']:.1f} ≥ {p['rsi_max']})")
        last = cs[-1]
        if not (last.close > last.open and f.get("rsi_change3_1d", 0) > 0):
            return Reject(self.id, "dip not turning up yet (no bullish daily close)")
        checks = {"near_ema200": (last.close - f["ema200_1d"]) / f["ema200_1d"] * 100 < 10,
                  "4h_turning_up": bool(f.get("choch_up_recent_4h") or f.get("bias_4h", 0) > 0),
                  "volume_1d": f.get("vol_rel_1d", 0) >= 1.0}
        low = min(c.low for c in cs[-8:])
        stop = low - p["stop_atr"] * f["atr_1d"] * 0.5
        return plan_levels(self.id, ctx, last.close, stop, low, _q(checks),
                           [f"INVEST · deep dip (daily RSI {f['rsi_min6_1d']:.1f}) inside a long-term uptrend, now turning up"] + _yn(checks), risk)


HORIZON = [ScalpMomentumPullback(), ScalpRangeBreakout(), InvestTrendReclaim(), InvestGoldenCross(), InvestBullDip()]
