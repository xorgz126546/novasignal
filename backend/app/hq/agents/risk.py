"""HQ Risk Manager: BTC market filter, exposure limits, position sizing. Its VETO is final for the cycle."""
from __future__ import annotations

from dataclasses import dataclass, field

from ...agents import RiskManager
from ...trading_config import RiskConfig
from ..features import Context
from ..strategies.base import Setup

REFERENCE_EQUITY = 1000.0


@dataclass
class RiskVerdict:
    approved: bool
    reasons: list[str] = field(default_factory=list)
    sizing: dict = field(default_factory=dict)


def btc_filter(btc: Context | None) -> tuple[bool, str]:
    """Risk-off when BTC is in a confirmed downtrend or dumping. Returns (ok, reason)."""
    if btc is None:
        return False, "BTC market filter: BTC data unavailable — no new longs without market context"
    f = btc.features
    below200 = f.get("close_4h", 0) < f.get("ema200_4h", float("inf"))
    trend4h_down = f.get("bias_4h", 0) < 0 or f.get("ema50_4h", 0) < f.get("ema200_4h", 0)
    h1_down = f.get("bias_1h", 0) < 0 or f.get("close_1h", 0) < f.get("ema50_1h", 0)
    if below200 and trend4h_down and h1_down:  # structure OR EMA trend: smooth declines have no swing breaks
        return False, "BTC market filter: BTC below 4H EMA200 with a bearish 4H and 1H trend (risk-off)"
    cs = btc.candles.get("1h", [])
    if len(cs) >= 5:
        drop = (cs[-1].close - cs[-5].open) / cs[-5].open * 100
        if drop <= -3.0:
            return False, f"BTC market filter: BTC fell {drop:.2f}% in the last 4 hours"
    return True, "BTC market filter: OK"


def assess(setup: Setup, ctx: Context, risk: RiskConfig, btc_ok: tuple[bool, str], open_signals: int,
           open_symbols: set[str], signals_today: int) -> RiskVerdict:
    reasons: list[str] = [btc_ok[1]]
    veto = not btc_ok[0]
    limits = [
        (setup.symbol in open_symbols, "exposure: symbol already has an open signal"),
        (open_signals >= risk.max_open_signals, f"exposure: {open_signals}/{risk.max_open_signals} signals already open"),
        (signals_today >= risk.max_signals_per_day, f"daily limit reached ({signals_today}/{risk.max_signals_per_day})"),
    ]
    for hit, why in limits:
        if hit:
            veto = True
            reasons.append(why)
    rm = RiskManager().run(dict(
        symbol=setup.symbol, entry=setup.entry, stop_loss=setup.stop, take_profit=setup.take_profit,
        equity_usdt=REFERENCE_EQUITY, risk_per_trade_pct=risk.risk_per_trade_pct, max_position_pct=risk.max_position_pct,
        fee_rate_pct=risk.fee_rate_pct, slippage_pct=risk.slippage_pct, min_risk_reward=risk.min_risk_reward,
        min_stop_distance_pct=risk.min_stop_distance_pct, max_stop_distance_pct=risk.max_stop_distance_pct,
        step_size=ctx.step_size, min_notional=ctx.min_notional, open_user_trades=0, max_open_user_trades=1,
        open_signals=0, max_open_signals=99))
    if not rm.passed:
        veto = True
        reasons += [f"sizing: {v}" for v in rm.violations] or [rm.error or "sizing failed"]
    sizing = {"quantity": rm.quantity, "notional_usdt": rm.notional_usdt, "risk_usdt": rm.risk_usdt,
              "reward_usdt": rm.reward_usdt, "net_risk_reward": rm.net_risk_reward,
              "reference_equity_usdt": REFERENCE_EQUITY,
              "risk_pct": round((rm.risk_usdt or 0) / REFERENCE_EQUITY * 100, 3)}
    if not veto:
        reasons.append(f"risk {sizing['risk_pct']}% of equity, net R:R 1:{rm.net_risk_reward}")
    return RiskVerdict(not veto, reasons, sizing)
