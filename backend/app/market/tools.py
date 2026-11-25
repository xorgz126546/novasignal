"""Extra analysis tools (indicator families) the research team can propose.

Every formula here is our own implementation of a public, well-known indicator definition (no code copied from
anywhere). A tool is LOCKED for live trading until the owner approves it (hq/tools_registry.py); backtests may use
any tool so the team can bring evidence before asking.
"""
from __future__ import annotations

from collections.abc import Sequence

from . import indicators as ta
from .binance import Candle

TOOLS: dict[str, dict] = {
    "supertrend": {"en": "Supertrend (ATR trend line)", "ar": "سوبرترند (خط الاتجاه بالـ ATR)",
                   "features": ("supertrend_dir", "supertrend_flip_up_recent", "supertrend_dist_pct")},
    "stoch_rsi": {"en": "Stochastic RSI", "ar": "ستوكاستيك RSI", "features": ("stoch_rsi_k", "stoch_rsi_d")},
    "vwap": {"en": "Rolling VWAP (24 bars)", "ar": "متوسط السعر المرجح بالحجم VWAP", "features": ("vwap_dist_pct",)},
    "obv": {"en": "On-Balance Volume slope", "ar": "ميل حجم التوازن OBV", "features": ("obv_slope",)},
    "mfi": {"en": "Money Flow Index", "ar": "مؤشر تدفق الأموال MFI", "features": ("mfi",)},
    "cci": {"en": "Commodity Channel Index", "ar": "مؤشر قناة السلع CCI", "features": ("cci",)},
    "williams_r": {"en": "Williams %R", "ar": "ويليامز %R", "features": ("willr",)},
    "ichimoku": {"en": "Ichimoku cloud", "ar": "سحابة إيشيموكو",
                 "features": ("ichi_above_cloud", "ichi_tk_cross_up_recent", "ichi_cloud_dist_pct")},
    "keltner": {"en": "Keltner channel + TTM squeeze", "ar": "قناة كيلتنر + ضغط TTM",
                "features": ("kc_pos", "ttm_squeeze_on", "ttm_release")},
}
ALL_TOOLS = frozenset(TOOLS)


def _supertrend(h, lo, c, period=10, mult=3.0) -> tuple[list[int | None], list[float | None]]:
    a = ta.atr(h, lo, c, period)
    n = len(c)
    direction: list[int | None] = [None] * n
    line: list[float | None] = [None] * n
    fu = fl = None
    d = 1
    for i in range(n):
        if a[i] is None:
            continue
        mid = (h[i] + lo[i]) / 2
        bu, bl = mid + mult * a[i], mid - mult * a[i]
        if fu is None:
            fu, fl = bu, bl
        else:
            fu = bu if (bu < fu or c[i - 1] > fu) else fu
            fl = bl if (bl > fl or c[i - 1] < fl) else fl
        if d == 1 and c[i] < fl:
            d = -1
        elif d == -1 and c[i] > fu:
            d = 1
        direction[i], line[i] = d, (fl if d == 1 else fu)
    return direction, line


def _stoch(values: Sequence[float | None], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    for i in range(len(values)):
        w = values[max(0, i - period + 1):i + 1]
        if len(w) < period or any(x is None for x in w):
            continue
        lo_, hi_ = min(w), max(w)
        out[i] = 50.0 if hi_ == lo_ else (values[i] - lo_) / (hi_ - lo_) * 100
    return out


def _smooth(values: list[float | None], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    for i in range(len(values)):
        w = values[max(0, i - period + 1):i + 1]
        if len(w) == period and all(x is not None for x in w):
            out[i] = sum(w) / period
    return out


def _mid(h, lo, i, p):
    return (max(h[i - p + 1:i + 1]) + min(lo[i - p + 1:i + 1])) / 2


def tool_features(cs: list[Candle], tf: str, tools: frozenset | set) -> dict[str, float]:
    """Features for the requested tools only (backtests ask only for what the strategy uses)."""
    f: dict[str, float] = {}
    if not tools or len(cs) < 60:
        return f
    c = [x.close for x in cs]
    h = [x.high for x in cs]
    lo = [x.low for x in cs]
    v = [x.volume for x in cs]
    n = len(c)
    if "supertrend" in tools:
        d, line = _supertrend(h, lo, c)
        if d[-1] is not None:
            f[f"supertrend_dir_{tf}"] = float(d[-1])
            f[f"supertrend_flip_up_recent_{tf}"] = float(any(d[k] == 1 and d[k - 1] == -1 for k in range(n - 3, n)))
            f[f"supertrend_dist_pct_{tf}"] = (c[-1] - line[-1]) / line[-1] * 100
    if "stoch_rsi" in tools:
        k = _smooth(_stoch(ta.rsi(c, 14), 14), 3)
        dd = _smooth(k, 3)
        if k[-1] is not None and dd[-1] is not None:
            f[f"stoch_rsi_k_{tf}"], f[f"stoch_rsi_d_{tf}"] = k[-1], dd[-1]
    if "vwap" in tools:
        tp = [(h[i] + lo[i] + c[i]) / 3 for i in range(n)]
        vol = sum(v[-24:])
        if vol > 0:
            vw = sum(tp[i] * v[i] for i in range(n - 24, n)) / vol
            f[f"vwap_dist_pct_{tf}"] = (c[-1] - vw) / vw * 100
    if "obv" in tools:
        obv = [0.0]
        for i in range(1, n):
            obv.append(obv[-1] + (v[i] if c[i] > c[i - 1] else -v[i] if c[i] < c[i - 1] else 0.0))
        denom = sum(v[-20:])
        if denom > 0:
            f[f"obv_slope_{tf}"] = (obv[-1] - obv[-21]) / denom  # -1..1: share of recent volume that was buying
    if "mfi" in tools and n > 15:
        tp = [(h[i] + lo[i] + c[i]) / 3 for i in range(n)]
        pos = sum(tp[i] * v[i] for i in range(n - 14, n) if tp[i] > tp[i - 1])
        neg = sum(tp[i] * v[i] for i in range(n - 14, n) if tp[i] < tp[i - 1])
        f[f"mfi_{tf}"] = 100.0 if neg == 0 else 100 - 100 / (1 + pos / neg)
    if "cci" in tools:
        tp = [(h[i] + lo[i] + c[i]) / 3 for i in range(n - 20, n)]
        m = sum(tp) / 20
        md = sum(abs(x - m) for x in tp) / 20
        if md > 0:
            f[f"cci_{tf}"] = (tp[-1] - m) / (0.015 * md)
    if "williams_r" in tools:
        hh, ll = max(h[-14:]), min(lo[-14:])
        if hh > ll:
            f[f"willr_{tf}"] = (hh - c[-1]) / (hh - ll) * -100
    if "ichimoku" in tools and n >= 80:
        def cloud_at(i):  # spans plotted 26 bars ahead → the cloud at bar i uses data up to i-26
            j = i - 26
            return (_mid(h, lo, j, 9) + _mid(h, lo, j, 26)) / 2, _mid(h, lo, j, 52)
        a_, b_ = cloud_at(n - 1)
        top = max(a_, b_)
        f[f"ichi_above_cloud_{tf}"] = float(c[-1] > top)
        f[f"ichi_cloud_dist_pct_{tf}"] = (c[-1] - top) / top * 100
        tk = [_mid(h, lo, i, 9) - _mid(h, lo, i, 26) for i in range(n - 4, n)]
        f[f"ichi_tk_cross_up_recent_{tf}"] = float(any(tk[k] > 0 >= tk[k - 1] for k in range(1, 4)))
    if "keltner" in tools:
        mid = ta.ema(c, 20)
        a = ta.atr(h, lo, c, 10)
        up, mi, lw = ta.bollinger(c, 20, 2)
        if mid[-1] is not None and a[-1]:
            ku, kl = mid[-1] + 2 * a[-1], mid[-1] - 2 * a[-1]
            f[f"kc_pos_{tf}"] = (c[-1] - kl) / (ku - kl) if ku > kl else 0.5

            def on(i):
                if mid[i] is None or not a[i] or up[i] is None:
                    return None
                return up[i] < mid[i] + 1.5 * a[i] and lw[i] > mid[i] - 1.5 * a[i]
            now = on(n - 1)
            if now is not None:
                f[f"ttm_squeeze_on_{tf}"] = float(now)
                f[f"ttm_release_{tf}"] = float(not now and any(on(k) for k in range(n - 7, n - 1)))
    return f


def tool_of(feature: str) -> str | None:
    base = feature.rsplit("_", 1)[0]
    for name, t in TOOLS.items():
        if base in t["features"]:
            return name
    return None
