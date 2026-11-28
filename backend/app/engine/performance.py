"""Performance metrics. Pure functions over closed-trade records — one track at a time.

Tracks are NEVER combined: theoretical (signal levels, no costs), paper (simulated costs),
actual (user-reported fills).
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TradeResult:
    closed_at: datetime
    return_pct: float  # net % return on the position
    pnl: float | None = None  # quote currency, if known
    fees: float = 0.0
    slippage_cost: float = 0.0
    symbol: str = ""


def max_drawdown_pct(returns_pct: list[float]) -> float:
    """Max peak-to-trough decline of an equity curve that compounds each trade's % return.
    Note: assumes full equity per trade — a conservative upper bound vs. risk-sized positions."""
    equity, peak, mdd = 1.0, 1.0, 0.0
    for r in returns_pct:
        equity *= 1 + r / 100
        peak = max(peak, equity)
        mdd = max(mdd, (peak - equity) / peak * 100)
    return mdd


def compute_metrics(trades: list[TradeResult]) -> dict:
    trades = sorted(trades, key=lambda t: t.closed_at)
    rets = [t.return_pct for t in trades]
    n = len(rets)
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    mean = sum(rets) / n if n else None
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (n - 1)) if n > 1 else None
    ci = None
    if n >= 2 and sd is not None:
        half = 1.96 * sd / math.sqrt(n)
        ci = [mean - half, mean + half]
    pnls = [t.pnl for t in trades if t.pnl is not None]
    eq = 1.0
    for r in rets:
        eq *= 1 + r / 100
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / n if n else None,
        "loss_rate": len(losses) / n if n else None,
        "avg_win_pct": sum(wins) / len(wins) if wins else None,
        "avg_loss_pct": sum(losses) / len(losses) if losses else None,
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else (None if not wins else math.inf),
        "expectancy_pct": mean,
        "expectancy_ci95_pct": ci,
        "stdev_pct": sd,
        "sum_return_pct": sum(rets),
        "compounded_return_pct": (eq - 1) * 100 if n else 0.0,
        "max_drawdown_pct": max_drawdown_pct(rets),
        "net_pnl": sum(pnls) if pnls else None,
        "total_fees": sum(t.fees for t in trades),
        "total_slippage_cost": sum(t.slippage_cost for t in trades),
    }


def bucket(trades: list[TradeResult], period: str) -> list[dict]:
    """Aggregate per day / ISO week / month."""
    groups: dict[str, list[TradeResult]] = defaultdict(list)
    for t in trades:
        d = t.closed_at
        key = d.strftime("%Y-%m-%d") if period == "day" else \
            f"{d.isocalendar().year}-W{d.isocalendar().week:02d}" if period == "week" else d.strftime("%Y-%m")
        groups[key].append(t)
    out = []
    for k in sorted(groups):
        g = groups[k]
        rets = [x.return_pct for x in g]
        out.append({"period": k, "trades": len(g), "wins": sum(1 for r in rets if r > 0), "sum_return_pct": sum(rets),
                    "net_pnl": sum(x.pnl for x in g if x.pnl is not None)})
    return out


def json_safe(d):
    if isinstance(d, dict):
        return {k: json_safe(v) for k, v in d.items()}
    if isinstance(d, list):
        return [json_safe(v) for v in d]
    if isinstance(d, float) and (math.isinf(d) or math.isnan(d)):
        return None if math.isnan(d) else "inf"
    return d
