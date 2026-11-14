"""Deterministic market features shared by live analysis, strategies, the rule DSL and the backtester.

All values derive from closed Binance candles via market/indicators.py and market/structure.py.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from ..market import indicators as ta
from ..market.binance import Candle
from ..market.structure import bos_choch, swing_highs, swing_lows

TIMEFRAMES = ("4h", "1h", "15m")
LOOKBACK = 300  # bars per timeframe used for features (enough for EMA200)


@dataclass
class Context:
    symbol: str
    candles: dict[str, list[Candle]]
    features: dict[str, float]
    structure: dict[str, dict]
    last_price: float
    tick_size: float = 0.0
    step_size: float = 0.0
    min_notional: float = 5.0
    backtest: bool = False  # in backtests 15m data may be unavailable → strategies skip 15m confirmation
    extras: dict = field(default_factory=dict)


def _bias_num(b: str) -> float:
    return {"up": 1.0, "down": -1.0}.get(b, 0.0)


def tf_features(cs: list[Candle], tf: str) -> tuple[dict[str, float], dict]:
    cs = cs[-LOOKBACK:]
    c = [x.close for x in cs]
    h = [x.high for x in cs]
    lo = [x.low for x in cs]
    v = [x.volume for x in cs]
    f: dict[str, float] = {}
    if len(c) < 60:
        return f, {"bias": "range", "events": [], "last_event": None}
    f[f"close_{tf}"] = c[-1]
    for p in (9, 21, 50, 200):
        e = ta.last(ta.ema(c, p))
        if e is not None:
            f[f"ema{p}_{tf}"] = e
    r = ta.last(ta.rsi(c, 14))
    if r is not None:
        f[f"rsi_{tf}"] = r
        rp = ta.last(ta.rsi(c, 14), 3)
        if rp is not None:
            f[f"rsi_change3_{tf}"] = r - rp
    _, _, hist = ta.macd(c)
    if ta.last(hist) is not None:
        f[f"macd_hist_{tf}"] = ta.last(hist)
    a = ta.last(ta.atr(h, lo, c, 14))
    if a:
        f[f"atr_{tf}"] = a
        f[f"atr_pct_{tf}"] = a / c[-1] * 100
    adx = ta.last(ta.adx(h, lo, c, 14))
    if adx is not None:
        f[f"adx_{tf}"] = adx
    up, mid, low = ta.bollinger(c, 20, 2)
    if up[-1] is not None:
        f[f"bb_upper_{tf}"], f[f"bb_lower_{tf}"] = up[-1], low[-1]
        f[f"bb_pos_{tf}"] = (c[-1] - low[-1]) / (up[-1] - low[-1]) if up[-1] > low[-1] else 0.5
    base = v[-23:-3]
    if base and statistics.fmean(base) > 0:
        f[f"vol_rel_{tf}"] = statistics.fmean(v[-3:]) / statistics.fmean(base)
    if f.get(f"ema21_{tf}"):
        f[f"dist_ema21_pct_{tf}"] = (c[-1] - f[f"ema21_{tf}"]) / f[f"ema21_{tf}"] * 100
    st = bos_choch(h, lo, c, 2)
    f[f"bias_{tf}"] = _bias_num(st["bias"])
    recent = [e for e in st["events"] if e["index"] >= len(c) - 12]
    f[f"bos_up_recent_{tf}"] = float(any(e["type"] == "BOS" and e["dir"] == "up" for e in recent))
    f[f"choch_up_recent_{tf}"] = float(any(e["type"] == "CHoCH" and e["dir"] == "up" for e in recent))
    f[f"choch_down_recent_{tf}"] = float(any(e["type"] == "CHoCH" and e["dir"] == "down" for e in recent))
    st["swing_highs"] = [p for _, p in swing_highs(h[-150:], 3)][-6:]
    st["swing_lows"] = [p for _, p in swing_lows(lo[-150:], 3)][-6:]
    return f, st


def build_features(candles: dict[str, list[Candle]]) -> tuple[dict[str, float], dict[str, dict]]:
    feats: dict[str, float] = {}
    structure: dict[str, dict] = {}
    for tf in TIMEFRAMES:
        if tf in candles and candles[tf]:
            f, st = tf_features(candles[tf], tf)
            feats.update(f)
            structure[tf] = st
    return feats, structure


# Whitelist used by the declarative rule DSL. Anything else is rejected at validation time.
FEATURE_NAMES = sorted({f"{name}_{tf}" for tf in TIMEFRAMES for name in (
    "close", "ema9", "ema21", "ema50", "ema200", "rsi", "rsi_change3", "macd_hist", "atr", "atr_pct", "adx",
    "bb_upper", "bb_lower", "bb_pos", "vol_rel", "dist_ema21_pct", "bias", "bos_up_recent", "choch_up_recent",
    "choch_down_recent")})
