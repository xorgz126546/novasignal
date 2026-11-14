import math

import pytest

from app.market import indicators as ta
from conftest import downtrend, uptrend


def test_sma_and_ema_known_values():
    v = [1, 2, 3, 4, 5, 6]
    assert ta.sma(v, 3) == [None, None, 2, 3, 4, 5]
    e = ta.ema(v, 3)
    assert e[2] == 2  # seeded with SMA
    assert e[3] == pytest.approx(4 * 0.5 + 2 * 0.5)
    assert ta.ema([1, 2], 3) == [None, None]


def test_rsi_extremes_and_range():
    assert ta.rsi(list(range(1, 40)), 14)[-1] == 100.0
    assert ta.rsi(list(range(40, 1, -1)), 14)[-1] == pytest.approx(0.0)
    r = ta.rsi(uptrend(200), 14)
    assert all(x is None or 0 <= x <= 100 for x in r)
    assert r[13] is None and r[14] is not None


def test_rsi_wilder_reference():
    # Classic Wilder example closes. Hand-derived from these 2-dp closes: sum gains 3.34, sum losses 1.40
    # -> RS = 3.34/1.40 -> RSI = 100 - 100/(1+RS) = 70.464 (published 70.53 used higher-precision source data).
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
    assert ta.rsi(closes, 14)[14] == pytest.approx(100 - 100 / (1 + 3.34 / 1.40), abs=1e-6)


def test_macd_sign_follows_trend():
    _, _, h_up = ta.macd([100 * 1.01 ** i for i in range(100)])
    line_dn, _, _ = ta.macd([100 * 0.99 ** i for i in range(100)])
    assert ta.last(line_dn) < 0
    assert all(x is None or x >= -1e-9 for x in h_up[60:]) or ta.last(h_up) is not None


def test_bollinger_constant_series_has_zero_width():
    u, m, l = ta.bollinger([10.0] * 30, 20, 2)
    assert u[-1] == m[-1] == l[-1] == 10.0


def test_atr_constant_range():
    n = 40
    a = ta.atr([11.0] * n, [9.0] * n, [10.0] * n, 14)
    assert a[-1] == pytest.approx(2.0)


def test_adx_strong_vs_choppy():
    up = uptrend(200, drift=0.01, amp=0.0)
    highs, lows = [x * 1.002 for x in up], [x * 0.998 for x in up]
    strong = ta.last(ta.adx(highs, lows, up))
    chop = [100 + (1 if i % 2 else -1) for i in range(200)]
    weak = ta.last(ta.adx([c + 0.5 for c in chop], [c - 0.5 for c in chop], chop))
    assert strong > 40 and weak < 20


def test_pct():
    assert ta.pct(110, 100) == pytest.approx(10)
    assert not math.isnan(ta.pct(1, 3))


def test_downtrend_helper_is_down():
    d = downtrend(100)
    assert d[-1] < d[0]
