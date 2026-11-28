"""AGENT 08 — RISK MANAGER: per-trade risk, position sizing, fees/slippage, exposure limits, veto."""
from __future__ import annotations

import math

from .base import AgentInput, AgentOutput, BaseAgent, clamp01


class RiskInput(AgentInput):
    symbol: str
    entry: float
    stop_loss: float
    take_profit: float
    equity_usdt: float = 1000.0
    risk_per_trade_pct: float = 1.0
    max_position_pct: float = 25.0
    fee_rate_pct: float = 0.10
    slippage_pct: float = 0.05
    min_risk_reward: float = 2.0
    min_stop_distance_pct: float = 0.8
    max_stop_distance_pct: float = 2.5
    step_size: float = 0.0
    min_notional: float = 5.0
    open_user_trades: int = 0
    max_open_user_trades: int = 1
    open_signals: int = 0
    max_open_signals: int = 3


class RiskOutput(AgentOutput):
    quantity: float | None = None
    notional_usdt: float | None = None
    risk_usdt: float | None = None  # loss if SL is hit incl. costs
    reward_usdt: float | None = None  # gain if TP is hit net of costs
    position_pct_of_equity: float | None = None
    net_risk_reward: float | None = None
    violations: list[str] = []


class RiskManager(BaseAgent[RiskInput, RiskOutput]):
    id = "risk_manager"
    number = 8
    name = "Risk Manager"
    responsibility = "Size the position from risk budget, include fees/slippage, enforce stop distance, R/R and exposure limits."
    Input = RiskInput
    Output = RiskOutput

    def execute(self, i: RiskInput) -> RiskOutput:
        v: list[str] = []
        if not (i.stop_loss < i.entry < i.take_profit):
            return RiskOutput(passed=False, violations=["levels not ordered SL < entry < TP (spot BUY only)"],
                              reasons=["invalid levels"])
        cost_rate = (i.fee_rate_pct + i.slippage_pct) / 100
        loss_per_unit = (i.entry - i.stop_loss) + (i.entry + i.stop_loss) * cost_rate
        gain_per_unit = (i.take_profit - i.entry) - (i.entry + i.take_profit) * cost_rate
        stop_pct = (i.entry - i.stop_loss) / i.entry * 100
        if stop_pct < i.min_stop_distance_pct:
            v.append(f"stop too tight ({stop_pct:.2f}% < {i.min_stop_distance_pct}%)")
        if stop_pct > i.max_stop_distance_pct:
            v.append(f"stop too wide ({stop_pct:.2f}% > {i.max_stop_distance_pct}%)")
        net_rr = gain_per_unit / loss_per_unit if loss_per_unit > 0 else 0
        if net_rr < i.min_risk_reward:
            v.append(f"net R/R 1:{net_rr:.2f} below 1:{i.min_risk_reward}")
        if i.open_user_trades >= i.max_open_user_trades:
            v.append(f"max open user-confirmed trades reached ({i.open_user_trades}/{i.max_open_user_trades})")
        if i.open_signals >= i.max_open_signals:
            v.append(f"max open signals reached ({i.open_signals}/{i.max_open_signals})")
        risk_budget = i.equity_usdt * i.risk_per_trade_pct / 100
        qty = risk_budget / loss_per_unit
        max_notional = i.equity_usdt * i.max_position_pct / 100
        if qty * i.entry > max_notional:
            qty = max_notional / i.entry
        if i.step_size > 0:
            qty = math.floor(qty / i.step_size + 1e-9) * i.step_size
        notional = qty * i.entry
        if notional < i.min_notional:
            v.append(f"position {notional:.2f} USDT below exchange min notional {i.min_notional}")
        reasons = [f"risk budget {risk_budget:.2f} USDT ({i.risk_per_trade_pct}% of {i.equity_usdt:.0f})",
                   f"size {qty:.8g} {i.symbol} ≈ {notional:.2f} USDT ({notional / i.equity_usdt * 100:.1f}% of equity)",
                   f"loss at SL incl. costs {qty * loss_per_unit:.2f} USDT; gain at TP net {qty * gain_per_unit:.2f} USDT"] + v
        return RiskOutput(passed=not v, score=round(clamp01(net_rr / (2 * i.min_risk_reward)) if not v else 0.0, 4),
                          quantity=qty, notional_usdt=round(notional, 4), risk_usdt=round(qty * loss_per_unit, 4),
                          reward_usdt=round(qty * gain_per_unit, 4),
                          position_pct_of_equity=round(notional / i.equity_usdt * 100, 3), net_risk_reward=round(net_rr, 3),
                          violations=v, reasons=reasons)
