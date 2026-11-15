"""AGENT 10 — ENTRY AND EXIT ENGINE: exactly one entry, ONE take-profit and ONE stop-loss, net of fees."""
from __future__ import annotations

import math

from .base import AgentInput, AgentOutput, BaseAgent, clamp01


class EntryExitInput(AgentInput):
    symbol: str
    last_price: float
    atr_1h: float
    atr_4h: float | None = None
    invalidation_level: float | None = None
    nearest_resistance: float | None = None
    tick_size: float = 0.0
    target_min_pct: float = 3.0
    target_max_pct: float = 5.0
    min_stop_distance_pct: float = 0.8
    max_stop_distance_pct: float = 2.5
    fee_rate_pct: float = 0.10
    slippage_pct: float = 0.05
    min_risk_reward: float = 2.0


class EntryExitOutput(AgentOutput):
    entry: float | None = None
    take_profit: float | None = None
    stop_loss: float | None = None
    expected_move_pct: float | None = None
    stop_distance_pct: float | None = None
    risk_reward: float | None = None  # gross, on price levels
    net_risk_reward: float | None = None  # after round-trip fees + slippage
    net_expected_return_pct: float | None = None  # if TP is hit, after costs
    round_trip_cost_pct: float | None = None


def round_to_tick(price: float, tick: float, mode: str = "nearest") -> float:
    if tick <= 0:
        return price
    n = price / tick
    n = math.floor(n + 1e-9) if mode == "down" else math.ceil(n - 1e-9) if mode == "up" else round(n)
    decimals = max(0, -int(math.floor(math.log10(tick)))) if tick < 1 else 0
    return round(n * tick, decimals)


class EntryExitEngine(BaseAgent[EntryExitInput, EntryExitOutput]):
    id = "entry_exit"
    number = 10
    name = "Entry & Exit Engine"
    responsibility = "Calculate entry, exactly one TP and one SL from structure/ATR, round to exchange tick size, account for fees and slippage."
    Input = EntryExitInput
    Output = EntryExitOutput

    def execute(self, i: EntryExitInput) -> EntryExitOutput:
        if i.last_price <= 0 or i.atr_1h <= 0:
            return EntryExitOutput(passed=False, reasons=["invalid price/ATR"])
        entry = round_to_tick(i.last_price, i.tick_size)
        reasons = []
        # --- Stop loss: structural invalidation, floored by a minimum noise distance (1.2x ATR or min %) ---
        min_dist = max(i.min_stop_distance_pct, 1.2 * i.atr_1h / entry * 100)
        if i.invalidation_level and i.invalidation_level < entry:
            sl_raw = i.invalidation_level
            reasons.append("stop placed below structural support")
        else:
            sl_raw = entry - 1.5 * i.atr_1h
            reasons.append("stop placed at 1.5x ATR(1h) (no structural level)")
        if (entry - sl_raw) / entry * 100 < min_dist:
            sl_raw = entry * (1 - min_dist / 100)
            reasons.append(f"stop widened to minimum distance {min_dist:.2f}%")
        sl = round_to_tick(sl_raw, i.tick_size, "down")
        stop_pct = (entry - sl) / entry * 100
        if stop_pct > i.max_stop_distance_pct:
            return EntryExitOutput(passed=False, entry=entry, stop_loss=sl, stop_distance_pct=round(stop_pct, 4),
                                   reasons=reasons + [f"stop distance {stop_pct:.2f}% exceeds max {i.max_stop_distance_pct}%"])
        # --- Take profit: within [min,max] target band, kept below the next resistance ---
        if i.nearest_resistance:
            room = (i.nearest_resistance * 0.997 - entry) / entry * 100
            if room < i.target_min_pct:
                return EntryExitOutput(passed=False, entry=entry, stop_loss=sl,
                                       reasons=reasons + [f"only {room:.2f}% room below resistance"])
            target = min(room, i.target_max_pct)
            reasons.append(f"target capped below resistance {i.nearest_resistance:.8g}")
        else:
            atr4 = (i.atr_4h or i.atr_1h * 2) / entry * 100
            target = min(max(2.5 * atr4, i.target_min_pct), i.target_max_pct)
            reasons.append("target from 2.5x ATR(4h) within band (no overhead resistance)")
        cost = 2 * (i.fee_rate_pct + i.slippage_pct)
        needed = i.min_risk_reward * (stop_pct + cost) + cost  # move required for the minimum net R/R
        atr4_pct = (i.atr_4h or i.atr_1h * 2) / entry * 100
        reach = min(room, i.target_max_pct) if i.nearest_resistance else min(i.target_max_pct, 3.5 * atr4_pct)
        if target < needed <= reach:
            target = needed  # still inside the band, below resistance, and within ~3.5x ATR(4h) of plausible reach
            reasons.append(f"target raised to {needed:.2f}% to meet minimum net R/R")
        tp = round_to_tick(entry * (1 + target / 100), i.tick_size, "up" if target == needed else "down")
        move = (tp - entry) / entry * 100
        if move < i.target_min_pct - 1e-6:
            return EntryExitOutput(passed=False, entry=entry, take_profit=tp, stop_loss=sl, reasons=reasons + ["target below band after rounding"])
        rr = move / stop_pct
        net_rr = (move - cost) / (stop_pct + cost)
        passed = net_rr >= i.min_risk_reward - 1e-9
        reasons.append(f"entry {entry:.8g}, TP {tp:.8g} (+{move:.2f}%), SL {sl:.8g} (-{stop_pct:.2f}%)")
        reasons.append(f"R/R gross 1:{rr:.2f}, net of {cost:.2f}% costs 1:{net_rr:.2f}")
        if not passed:
            reasons.append(f"net R/R below minimum 1:{i.min_risk_reward}")
        return EntryExitOutput(
            passed=passed, score=round(clamp01(net_rr / (2 * i.min_risk_reward)), 4), entry=entry, take_profit=tp,
            stop_loss=sl, expected_move_pct=round(move, 4), stop_distance_pct=round(stop_pct, 4), risk_reward=round(rr, 3),
            net_risk_reward=round(net_rr, 3), net_expected_return_pct=round(move - cost, 4), round_trip_cost_pct=cost,
            reasons=reasons,
        )
