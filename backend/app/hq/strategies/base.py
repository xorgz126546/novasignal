"""Strategy plug-in contract + shared level planning (ONE take-profit, one stop, invalidation)."""
from __future__ import annotations

from dataclasses import dataclass, field

from ...agents.a10_entry_exit import round_to_tick
from ...trading_config import RiskConfig
from ..features import Context


@dataclass
class Setup:
    strategy_id: str
    symbol: str
    entry: float
    stop: float
    take_profit: float
    invalidation: float
    quality: float  # 0..1 deterministic evidence strength from the strategy's own checks
    reasons: list[str] = field(default_factory=list)
    rr: float = 0.0
    net_rr: float = 0.0
    move_pct: float = 0.0
    stop_pct: float = 0.0


@dataclass
class Reject:
    strategy_id: str
    reason: str


class Strategy:
    id: str = ""
    name: str = ""
    default_params: dict = {}

    def evaluate(self, ctx: Context, params: dict, risk: RiskConfig) -> Setup | Reject:  # pragma: no cover
        raise NotImplementedError


def nearest_resistance(ctx: Context, above: float) -> float | None:
    lv = []
    for tf in ("1h", "4h"):
        lv += [p for p in ctx.structure.get(tf, {}).get("swing_highs", []) if p > above * 1.002]
    return min(lv) if lv else None


def plan_levels(sid: str, ctx: Context, entry: float, stop: float, invalidation: float, quality: float,
                reasons: list[str], risk: RiskConfig) -> Setup | Reject:
    """Validate stop distance, then place ONE take-profit inside the target band, under resistance,
    at least far enough for the minimum net R:R. Rejects when that's impossible."""
    entry = round_to_tick(entry, ctx.tick_size)
    stop = round_to_tick(stop, ctx.tick_size, "down")
    if not (0 < stop < entry):
        return Reject(sid, "invalid stop (must be below entry for a spot BUY)")
    stop_pct = (entry - stop) / entry * 100
    if stop_pct < risk.min_stop_distance_pct:
        return Reject(sid, f"stop too tight ({stop_pct:.2f}% < {risk.min_stop_distance_pct}%)")
    if stop_pct > risk.max_stop_distance_pct:
        return Reject(sid, f"stop too wide ({stop_pct:.2f}% > {risk.max_stop_distance_pct}%)")
    cost = 2 * (risk.fee_rate_pct + risk.slippage_pct)
    need = risk.min_risk_reward * (stop_pct + cost) + cost
    target = max(need, risk.target_move_min_pct)
    if target > risk.target_move_max_pct + 1e-9:
        return Reject(sid, f"needed move {target:.2f}% exceeds max target {risk.target_move_max_pct}%")
    res = nearest_resistance(ctx, entry)
    if res is not None:
        room = (res * 0.997 - entry) / entry * 100
        if room < target:
            return Reject(sid, f"resistance {res:.8g} leaves only {room:.2f}% room (< {target:.2f}%)")
    tp = round_to_tick(entry * (1 + target / 100), ctx.tick_size, "up")
    move = (tp - entry) / entry * 100
    net_rr = (move - cost) / (stop_pct + cost)
    if net_rr < risk.min_risk_reward - 1e-9:
        return Reject(sid, f"net R:R 1:{net_rr:.2f} below 1:{risk.min_risk_reward}")
    return Setup(sid, ctx.symbol, entry, stop, tp, round_to_tick(invalidation, ctx.tick_size, "down"), quality,
                 reasons, rr=round(move / stop_pct, 3), net_rr=round(net_rr, 3), move_pct=round(move, 4),
                 stop_pct=round(stop_pct, 4))
