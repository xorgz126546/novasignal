"""Deterministic backtest + walk-forward gate for HQ strategies and proposals.

- Bar-by-bar on real closed 1H Binance candles with matching 4H context (no look-ahead: a 4H candle is used
  only after it closed). 15M data is not replayed; strategies run with ctx.backtest=True.
- Entry at the NEXT bar's open + slippage; exits: stop first if a bar touches both levels; take-profit;
  timeout at the horizon (exit at close). Fees on both sides. Non-overlapping trades per symbol.
- Gate: minimum trades, positive out-of-sample net expectancy, max drawdown, fold consistency, baseline
  comparison, and an overfitting penalty that raises the required t-statistic with the number of variants tried.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..engine.performance import max_drawdown_pct
from ..hq.features import Context, build_features
from ..hq.strategies.base import Setup, Strategy
from ..trading_config import RiskConfig
from .binance import Candle

WARMUP = 220
WINDOW = 260


@dataclass
class Trade:
    symbol: str
    entry_time: int
    exit_time: int
    entry: float
    exit: float
    outcome: str
    net_pct: float


@dataclass
class GateThresholds:
    min_trades: int = 20
    max_drawdown_pct: float = 20.0
    min_oos_expectancy_pct: float = 0.0
    min_positive_folds: int = 2
    folds: int = 4
    oos_fraction: float = 0.3
    base_t_stat: float = 1.0
    min_improvement_vs_baseline_pct: float = 0.0


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    bars: int = 0

    def returns(self) -> list[float]:
        return [t.net_pct for t in sorted(self.trades, key=lambda t: t.entry_time)]


def _ctx_at(symbol: str, c1h: list[Candle], c4h: list[Candle], i: int, risk_tick: float) -> Context:
    window = c1h[max(0, i - WINDOW + 1):i + 1]
    t_close = c1h[i].close_time
    h4 = [c for c in c4h if c.close_time <= t_close][-WINDOW:]
    feats, st = build_features({"1h": window, "4h": h4})
    return Context(symbol, {"1h": window, "4h": h4}, feats, st, window[-1].close, risk_tick, 0.0, 0.0, backtest=True)


def simulate(strategy: Strategy, params: dict, data: dict[str, tuple[list[Candle], list[Candle]]], risk: RiskConfig,
             horizon_bars: int | None = None, step: int = 1) -> BacktestResult:
    horizon = horizon_bars or int(risk.signal_horizon_hours)
    fee, slip = risk.fee_rate_pct / 100, risk.slippage_pct / 100
    res = BacktestResult(symbols=list(data))
    for sym, (c1h, c4h) in data.items():
        res.bars += max(0, len(c1h) - WARMUP)
        i = WARMUP
        while i < len(c1h) - 2:
            r = strategy.evaluate(_ctx_at(sym, c1h, c4h, i, 0.0), params, risk)
            if not isinstance(r, Setup):
                i += step
                continue
            entry = c1h[i + 1].open * (1 + slip)  # next bar open: no look-ahead
            sl, tp = r.stop, r.take_profit
            outcome, exit_px, j = "timeout", None, i + 1
            while j < min(len(c1h), i + 1 + horizon):
                c = c1h[j]
                if c.low <= sl:
                    outcome, exit_px = "sl", sl * (1 - 2 * slip)
                    break
                if c.high >= tp:
                    outcome, exit_px = "tp", tp
                    break
                j += 1
            if exit_px is None:
                if j >= len(c1h):
                    break  # not enough future data: discard (never extrapolate)
                exit_px, j = c1h[j - 1].close * (1 - slip), j - 1
            net = ((exit_px * (1 - fee)) - entry * (1 + fee)) / (entry * (1 + fee)) * 100
            res.trades.append(Trade(sym, c1h[i + 1].open_time, c1h[j].close_time, entry, exit_px, outcome, net))
            i = j + 1  # non-overlapping
    return res


def _t_stat(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return 0.0 if sd == 0 else m / (sd / math.sqrt(n))


def summarize(res: BacktestResult) -> dict:
    r = res.returns()
    n = len(r)
    wins = [x for x in r if x > 0]
    losses = [x for x in r if x <= 0]
    return {"trades": n, "win_rate": len(wins) / n if n else None,
            "expectancy_pct": sum(r) / n if n else None,
            "profit_factor": (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else None,
            "max_drawdown_pct": max_drawdown_pct(r), "t_stat": _t_stat(r), "symbols": res.symbols, "bars": res.bars,
            "outcomes": {k: sum(1 for t in res.trades if t.outcome == k) for k in ("tp", "sl", "timeout")}}


def walk_forward(res: BacktestResult, th: GateThresholds) -> dict:
    trades = sorted(res.trades, key=lambda t: t.entry_time)
    if not trades:
        return {"folds": [], "oos": {"trades": 0, "expectancy_pct": None}}
    t0, t1 = trades[0].entry_time, trades[-1].entry_time + 1
    span = (t1 - t0) / th.folds
    folds = []
    for k in range(th.folds):
        seg = [t.net_pct for t in trades if t0 + k * span <= t.entry_time < t0 + (k + 1) * span]
        folds.append({"fold": k + 1, "trades": len(seg), "expectancy_pct": sum(seg) / len(seg) if seg else None})
    cut = t0 + (t1 - t0) * (1 - th.oos_fraction)
    oos = [t.net_pct for t in trades if t.entry_time >= cut]
    return {"folds": folds, "oos": {"trades": len(oos), "expectancy_pct": sum(oos) / len(oos) if oos else None}}


def required_t(th: GateThresholds, variants_tried: int) -> float:
    """Overfitting penalty: the more variants tried, the stronger the evidence required."""
    return th.base_t_stat * math.sqrt(1 + math.log(max(1, variants_tried)))


def gate(res: BacktestResult, th: GateThresholds, variants_tried: int = 1, baseline: dict | None = None) -> dict:
    s = summarize(res)
    wf = walk_forward(res, th)
    need_t = required_t(th, variants_tried)
    pos_folds = sum(1 for f in wf["folds"] if f["expectancy_pct"] is not None and f["expectancy_pct"] > 0)
    checks = {
        "min_trades": s["trades"] >= th.min_trades,
        "oos_positive": (wf["oos"]["expectancy_pct"] or -1) > th.min_oos_expectancy_pct and wf["oos"]["trades"] > 0,
        "max_drawdown": s["max_drawdown_pct"] <= th.max_drawdown_pct,
        "fold_consistency": pos_folds >= th.min_positive_folds,
        "overfitting_penalty": s["t_stat"] >= need_t,
    }
    if baseline and baseline.get("expectancy_pct") is not None and s["expectancy_pct"] is not None:
        checks["beats_baseline"] = s["expectancy_pct"] >= baseline["expectancy_pct"] + th.min_improvement_vs_baseline_pct
    reasons = [f"{k}: {'pass' if v else 'FAIL'}" for k, v in checks.items()]
    return {"passed": all(checks.values()), "checks": checks, "reasons": reasons, "summary": s, "walk_forward": wf,
            "variants_tried": variants_tried, "required_t_stat": round(need_t, 3), "baseline": baseline,
            "costs": "fees both sides + slippage on entry and stops (from risk settings)",
            "evidence_note": "Only this deterministic replay counts as evidence; README/marketing claims are ignored."}
