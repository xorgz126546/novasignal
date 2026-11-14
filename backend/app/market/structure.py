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


def bos_choch(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], window: int = 2) -> dict:
    """Break of Structure / Change of Character from closes crossing the latest confirmed swing points.

    A swing point is confirmed `window` bars after it forms (no look-ahead). Close above the last swing high
    is BOS up when the state was already up (or unknown), CHoCH up when the state was down; mirror for down.
    """
    n = len(closes)
    state = "range"
    last_sh = last_sl = None  # (index, price) of latest *confirmed* swings
    events: list[dict] = []
    broken_sh = broken_sl = None
    for i in range(n):
        j = i - window  # candidate swing confirmed at i
        if j - window >= 0:
            seg_h = highs[j - window:j + window + 1]
            seg_l = lows[j - window:j + window + 1]
            if highs[j] == max(seg_h) and seg_h.count(highs[j]) == 1:
                last_sh = (j, highs[j])
            if lows[j] == min(seg_l) and seg_l.count(lows[j]) == 1:
                last_sl = (j, lows[j])
        c = closes[i]
        if last_sh and c > last_sh[1] and broken_sh != last_sh[0]:
            kind = "CHoCH" if state == "down" else "BOS"
            events.append({"type": kind, "dir": "up", "level": last_sh[1], "index": i})
            state, broken_sh = "up", last_sh[0]
        elif last_sl and c < last_sl[1] and broken_sl != last_sl[0]:
            kind = "CHoCH" if state == "up" else "BOS"
            events.append({"type": kind, "dir": "down", "level": last_sl[1], "index": i})
            state, broken_sl = "down", last_sl[0]
    return {"bias": state, "events": events[-5:], "last_event": events[-1] if events else None,
            "swing_high": last_sh[1] if last_sh else None, "swing_low": last_sl[1] if last_sl else None,
            "bars": n}
