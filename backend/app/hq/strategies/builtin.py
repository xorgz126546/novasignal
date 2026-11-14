"""Built-in strategy plug-ins. Each is independent: it reads the shared Context and returns Setup or Reject."""
from __future__ import annotations

import statistics

from ...trading_config import RiskConfig
from ..features import Context
from .base import Reject, Setup, Strategy, plan_levels


def _avg(xs):
    return sum(xs) / len(xs) if xs else 0.0


class BreakoutRetest(Strategy):
    id = "breakout_retest"
    name = "Breakout + Retest"
    default_params = {"lookback": 24, "retest_tol_pct": 0.4, "stop_atr": 0.25, "min_breakout_vol": 1.1}

    def evaluate(self, ctx: Context, p: dict, risk: RiskConfig) -> Setup | Reject:
        f = ctx.features
        cs = ctx.candles.get("1h", [])
        if len(cs) < 80 or "atr_1h" not in f:
            return Reject(self.id, "insufficient 1h data")
        if f.get("bias_4h", 0) < 0 or f.get("close_4h", 0) <= f.get("ema50_4h", float("inf")):
            return Reject(self.id, "4H trend not supportive (bias down or below EMA50)")
        levels = sorted({lv for lv in ctx.structure.get("1h", {}).get("swing_highs", []) if lv < cs[-1].close}, reverse=True)
        recent = cs[-p["lookback"]:]
        vol_avg = statistics.fmean(c.volume for c in cs[-60:-p["lookback"]]) or 1.0
        for L in levels:
            brk = next((i for i, c in enumerate(recent) if c.close > L * 1.002), None)
            if brk is None or brk >= len(recent) - 1:
                continue
            after = recent[brk + 1:]
            touched = [c for c in after if c.low <= L * (1 + p["retest_tol_pct"] / 100)]
            held = all(c.close > L * 0.998 for c in after)
            if not touched or not held or cs[-1].close <= L:
                continue
            bvol = recent[brk].volume / vol_avg
            checks = {"breakout_volume": bvol >= p["min_breakout_vol"], "bias_4h_up": f.get("bias_4h", 0) > 0,
                      "rsi_1h_healthy": 50 <= f.get("rsi_1h", 0) <= 70, "macd_1h_positive": f.get("macd_hist_1h", 0) > 0}
            stop = min(c.low for c in touched) - p["stop_atr"] * f["atr_1h"]
            reasons = [f"1H close broke key level {L:.8g} and retested it (low within {p['retest_tol_pct']}%)",
                       f"breakout volume {bvol:.2f}x average"] + [f"{k}: {'yes' if v else 'no'}" for k, v in checks.items()]
            return plan_levels(self.id, ctx, cs[-1].close, stop, L * 0.997, _avg([1.0] + [float(v) for v in checks.values()]),
                               reasons, risk)
        return Reject(self.id, "no breakout of a 1H swing high followed by a held retest")


class TrendPullback(Strategy):
    id = "trend_pullback"
    name = "Trend Pullback"
    default_params = {"pullback_bars": 6, "zone_tol_pct": 0.4, "stop_atr": 0.25, "rsi_min": 40, "rsi_max": 65}

    def evaluate(self, ctx: Context, p: dict, risk: RiskConfig) -> Setup | Reject:
        f = ctx.features
        cs = ctx.candles.get("1h", [])
        need = ("close_4h", "ema50_4h", "ema21_1h", "ema50_1h", "atr_1h", "rsi_1h")
        if len(cs) < 80 or any(k not in f for k in need):
            return Reject(self.id, "insufficient data")
        if not (f["close_4h"] > f["ema50_4h"] and f.get("ema200_4h", 0) < f["ema50_4h"] and f.get("bias_4h", 0) >= 0):
            return Reject(self.id, "no established 4H uptrend (close > EMA50 > EMA200)")
        if f["ema21_1h"] <= f["ema50_1h"]:
            return Reject(self.id, "1H EMA21 not above EMA50")
        bars = cs[-p["pullback_bars"]:]
        low = min(c.low for c in bars)
        zone_hi = f["ema21_1h"] * (1 + p["zone_tol_pct"] / 100)
        if not (f["ema50_1h"] * 0.995 <= low <= zone_hi):
            return Reject(self.id, "no pullback into the EMA21–EMA50 zone")
        if cs[-1].close <= f["ema21_1h"]:
            return Reject(self.id, "1H close not back above EMA21")
        if not (p["rsi_min"] <= f["rsi_1h"] <= p["rsi_max"]):
            return Reject(self.id, f"RSI(1h) {f['rsi_1h']:.1f} outside {p['rsi_min']}–{p['rsi_max']}")
        confirm15 = True
        if not ctx.backtest:
            confirm15 = bool(f.get("choch_up_recent_15m")) or (f.get("rsi_change3_15m", 0) > 0 and
                                                               f.get("close_15m", 0) > f.get("ema21_15m", float("inf")))
            if not confirm15:
                return Reject(self.id, "no 15M bullish confirmation")
        checks = {"bias_4h_up": f.get("bias_4h", 0) > 0, "macd_1h_turning_up": f.get("macd_hist_1h", 0) > 0,
                  "adx_4h_trending": f.get("adx_4h", 0) >= 20, "15m_confirmation": confirm15}
        stop = low - p["stop_atr"] * f["atr_1h"]
        reasons = ["4H uptrend: close > EMA50 > EMA200",
                   f"1H pullback low {low:.8g} reached the EMA21–EMA50 zone and price reclaimed EMA21",
                   f"RSI(1h) {f['rsi_1h']:.1f}"] + [f"{k}: {'yes' if v else 'no'}" for k, v in checks.items()]
        return plan_levels(self.id, ctx, cs[-1].close, stop, f["ema50_1h"] * 0.997,
                           _avg([1.0] + [float(v) for v in checks.values()]), reasons, risk)


class LiquiditySweep(Strategy):
    id = "liquidity_sweep"
    name = "Liquidity Sweep Reversal"
    default_params = {"sweep_bars": 6, "stop_atr": 0.15}

    def evaluate(self, ctx: Context, p: dict, risk: RiskConfig) -> Setup | Reject:
        f = ctx.features
        cs = ctx.candles.get("1h", [])
        if len(cs) < 80 or "atr_1h" not in f:
            return Reject(self.id, "insufficient 1h data")
        if f.get("close_4h", 0) < f.get("ema200_4h", 0) and f.get("bias_4h", 0) < 0:
            return Reject(self.id, "4H in a downtrend below EMA200 (no knife-catching)")
        recent = cs[-p["sweep_bars"]:]
        older_lows = [lv for lv in ctx.structure.get("1h", {}).get("swing_lows", [])]
        for SL in sorted(older_lows, reverse=True):
            sweep = next((c for c in recent if c.low < SL * 0.999 and c.close > SL), None)
            if sweep is None or cs[-1].close <= SL:
                continue
            if ctx.backtest:
                confirmed = bool(f.get("choch_up_recent_1h")) or bool(f.get("bos_up_recent_1h"))
            else:
                confirmed = bool(f.get("choch_up_recent_15m")) or bool(f.get("choch_up_recent_1h"))
            if not confirmed:
                return Reject(self.id, f"swept {SL:.8g} but no bullish CHoCH yet")
            checks = {"rsi_1h_rising": f.get("rsi_change3_1h", 0) > 0, "volume_on_sweep": f.get("vol_rel_1h", 0) >= 1.0,
                      "4h_not_down": f.get("bias_4h", 0) >= 0}
            stop = sweep.low - p["stop_atr"] * f["atr_1h"]
            reasons = [f"1H swept the prior low {SL:.8g} (wick to {sweep.low:.8g}) and closed back above it",
                       "bullish change of character confirmed"] + [f"{k}: {'yes' if v else 'no'}" for k, v in checks.items()]
            return plan_levels(self.id, ctx, cs[-1].close, stop, sweep.low, _avg([1.0] + [float(v) for v in checks.values()]),
                               reasons, risk)
        return Reject(self.id, "no sweep-and-reclaim of a prior 1H low")


from .extra import EXTRA  # noqa: E402  (new strategies: squeeze, donchian, relative strength, oversold, range)

BUILTINS: dict[str, Strategy] = {s.id: s for s in (BreakoutRetest(), TrendPullback(), LiquiditySweep(), *EXTRA)}
