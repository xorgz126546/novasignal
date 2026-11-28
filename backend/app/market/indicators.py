"""Deterministic technical indicators (pure Python, no LLM involvement).

All functions return lists aligned with the input; positions without enough history are None.
Conventions follow the standard definitions (Wilder smoothing for RSI/ATR/ADX).
"""
from __future__ import annotations

import math
from collections.abc import Sequence

Series = list[float | None]


def sma(values: Sequence[float], period: int) -> Series:
    out: Series = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    s = sum(values[:period])
    out[period - 1] = s / period
    for i in range(period, len(values)):
        s += values[i] - values[i - period]
        out[i] = s / period
    return out


def ema(values: Sequence[float], period: int) -> Series:
    """EMA seeded with the SMA of the first `period` values."""
    out: Series = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    k = 2 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def _wilder(values: Sequence[float], period: int, start: int = 0) -> Series:
    out: Series = [None] * len(values)
    if len(values) - start < period:
        return out
    prev = sum(values[start:start + period]) / period
    out[start + period - 1] = prev
    for i in range(start + period, len(values)):
        prev = (prev * (period - 1) + values[i]) / period
        out[i] = prev
    return out


def rsi(closes: Sequence[float], period: int = 14) -> Series:
    out: Series = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = [0.0] + [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [0.0] + [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    ag = _wilder(gains, period, start=1)
    al = _wilder(losses, period, start=1)
    for i in range(len(closes)):
        if ag[i] is None or al[i] is None:
            continue
        if al[i] == 0:
            out[i] = 100.0 if ag[i] > 0 else 50.0
        else:
            out[i] = 100 - 100 / (1 + ag[i] / al[i])
    return out


def macd(closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[Series, Series, Series]:
    ef, es = ema(closes, fast), ema(closes, slow)
    line: Series = [f - s if f is not None and s is not None else None for f, s in zip(ef, es)]
    first = next((i for i, v in enumerate(line) if v is not None), None)
    sig: Series = [None] * len(closes)
    if first is not None:
        sub = ema([v for v in line[first:]], signal)  # type: ignore[misc]
        sig[first:] = sub
    hist: Series = [m - s if m is not None and s is not None else None for m, s in zip(line, sig)]
    return line, sig, hist


def bollinger(closes: Sequence[float], period: int = 20, num_std: float = 2.0) -> tuple[Series, Series, Series]:
    mid = sma(closes, period)
    upper: Series = [None] * len(closes)
    lower: Series = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1:i + 1]
        m = mid[i]
        sd = math.sqrt(sum((x - m) ** 2 for x in window) / period)  # population std (standard BB)
        upper[i], lower[i] = m + num_std * sd, m - num_std * sd
    return upper, mid, lower


def true_range(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]) -> list[float]:
    tr = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    return tr


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14) -> Series:
    if not closes:
        return []
    return _wilder(true_range(highs, lows, closes), period)


def adx(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14) -> Series:
    n = len(closes)
    out: Series = [None] * n
    if n < 2 * period + 1:
        return out
    pdm, ndm = [0.0], [0.0]
    for i in range(1, n):
        up, down = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
        pdm.append(up if up > down and up > 0 else 0.0)
        ndm.append(down if down > up and down > 0 else 0.0)
    tr = true_range(highs, lows, closes)
    s_tr, s_p, s_n = _wilder(tr, period, 1), _wilder(pdm, period, 1), _wilder(ndm, period, 1)
    dx: list[float] = [0.0] * n
    first = None
    for i in range(n):
        if s_tr[i] is None or s_tr[i] == 0:
            continue
        pdi, ndi = 100 * s_p[i] / s_tr[i], 100 * s_n[i] / s_tr[i]
        dx[i] = 0.0 if pdi + ndi == 0 else 100 * abs(pdi - ndi) / (pdi + ndi)
        first = i if first is None else first
    if first is None:
        return out
    sm = _wilder(dx, period, start=first)
    return sm


def last(series: Series, back: int = 0) -> float | None:
    idx = len(series) - 1 - back
    return series[idx] if 0 <= idx < len(series) else None


def pct(a: float, b: float) -> float:
    """Percent change from b to a."""
    return (a - b) / b * 100.0
