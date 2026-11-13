"""Price-structure helpers: swing points, level clustering, candlestick patterns."""
from __future__ import annotations

from collections.abc import Sequence

from .binance import Candle


def swing_highs(highs: Sequence[float], window: int = 3) -> list[tuple[int, float]]:
    out = []
    for i in range(window, len(highs) - window):
        h = highs[i]
        if all(h >= highs[j] for j in range(i - window, i + window + 1) if j != i) and h > highs[i - 1]:
            out.append((i, h))
    return out


def swing_lows(lows: Sequence[float], window: int = 3) -> list[tuple[int, float]]:
    out = []
    for i in range(window, len(lows) - window):
        lo = lows[i]
        if all(lo <= lows[j] for j in range(i - window, i + window + 1) if j != i) and lo < lows[i - 1]:
            out.append((i, lo))
    return out


def cluster_levels(prices: Sequence[float], tolerance_pct: float = 0.5) -> list[dict]:
    """Group nearby prices; strength = number of touches."""
    clusters: list[list[float]] = []
    for p in sorted(prices):
        if clusters and (p - clusters[-1][-1]) / clusters[-1][-1] * 100 <= tolerance_pct:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    return [{"price": sum(c) / len(c), "touches": len(c)} for c in clusters]


def _body(c: Candle) -> float:
    return abs(c.close - c.open)


def _range(c: Candle) -> float:
    return max(c.high - c.low, 1e-12)


def is_bullish_engulfing(prev: Candle, cur: Candle) -> bool:
    return prev.close < prev.open and cur.close > cur.open and cur.close >= prev.open and cur.open <= prev.close \
        and _body(cur) > _body(prev)


def is_bearish_engulfing(prev: Candle, cur: Candle) -> bool:
    return prev.close > prev.open and cur.close < cur.open and cur.open >= prev.close and cur.close <= prev.open \
        and _body(cur) > _body(prev)


def is_hammer(c: Candle) -> bool:
    lower = min(c.open, c.close) - c.low
    upper = c.high - max(c.open, c.close)
    return _body(c) / _range(c) <= 0.35 and lower >= 2 * max(_body(c), 1e-12) and upper <= _body(c) + 0.1 * _range(c)


def is_shooting_star(c: Candle) -> bool:
    lower = min(c.open, c.close) - c.low
    upper = c.high - max(c.open, c.close)
    return _body(c) / _range(c) <= 0.35 and upper >= 2 * max(_body(c), 1e-12) and lower <= _body(c) + 0.1 * _range(c)


def detect_patterns(candles: Sequence[Candle]) -> list[dict]:
    """Patterns on the last two closed candles."""
    out: list[dict] = []
    if len(candles) < 2:
        return out
    prev, cur = candles[-2], candles[-1]
    if is_bullish_engulfing(prev, cur):
        out.append({"name": "bullish_engulfing", "bias": "bullish"})
    if is_bearish_engulfing(prev, cur):
        out.append({"name": "bearish_engulfing", "bias": "bearish"})
    if is_hammer(cur):
        out.append({"name": "hammer", "bias": "bullish"})
    if is_shooting_star(cur):
        out.append({"name": "shooting_star", "bias": "bearish"})
    if len(candles) >= 3 and all(c.close < c.open for c in candles[-3:]) and \
            candles[-1].close < candles[-2].close < candles[-3].close:
        out.append({"name": "three_black_crows", "bias": "bearish"})
    return out
