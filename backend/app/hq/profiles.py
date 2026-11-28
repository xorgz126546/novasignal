"""Trade horizons. Each strategy declares a profile; the profile overrides the risk/target rules it is judged by.

scalp  — 15m entries, small targets, tight stops, short horizon.
swing  — the original 1h/4h rules (3-5% target).
invest — daily trend/accumulation entries, wide stops, long horizon.
momentum — a move in its first days (+10% in 3 days on volume): 12% target, 6% stop, 3-day horizon.
Hard rules stay the same for all: Binance Spot, long only, ONE take-profit, one stop, min net R:R, risk veto.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..trading_config import RiskConfig


@dataclass(frozen=True)
class Profile:
    id: str
    label_en: str
    label_ar: str
    base_tf: str  # bar used to step backtests and to enter
    tfs: tuple[str, ...]  # timeframes the strategy may read
    res_tfs: tuple[str, ...]  # timeframes used to find overhead resistance
    overrides: dict


PROFILES: dict[str, Profile] = {
    "scalp": Profile("scalp", "Scalp", "سكالبينغ", "15m", ("15m", "1h"), ("15m", "1h"), dict(
        target_move_min_pct=1.0, target_move_max_pct=2.0, min_stop_distance_pct=0.4, max_stop_distance_pct=0.9,
        min_risk_reward=1.3, signal_horizon_hours=8, entry_validity_hours=0.5, symbol_cooldown_hours=6,
        min_quote_volume_usdt=50_000_000)),
    "swing": Profile("swing", "Swing", "سوينغ", "1h", ("1h", "4h", "15m"), ("1h", "4h"), {}),
    "momentum": Profile("momentum", "Momentum ignition", "بداية الترند (زخم)", "4h", ("4h", "1d"), (), dict(
        target_move_min_pct=12.0, target_move_max_pct=15.0, min_stop_distance_pct=4.0, max_stop_distance_pct=8.0,
        min_risk_reward=1.5, signal_horizon_hours=72, entry_validity_hours=4, symbol_cooldown_hours=72,
        min_quote_volume_usdt=10_000_000)),  # ignitions start in mid caps; 10M USDT/day still fills retail size
    "invest": Profile("invest", "Long-term", "استثمار طويل المدى", "1d", ("1d", "4h"), ("1d",), dict(
        target_move_min_pct=12.0, target_move_max_pct=35.0, min_stop_distance_pct=5.0, max_stop_distance_pct=12.0,
        min_risk_reward=2.0, signal_horizon_hours=24 * 60, entry_validity_hours=48, symbol_cooldown_hours=24 * 14)),
}


def profile_risk(risk: RiskConfig, profile_id: str) -> RiskConfig:
    """The owner's swing settings stay authoritative for swing; other profiles apply their own bands."""
    p = PROFILES.get(profile_id, PROFILES["swing"])
    if not p.overrides:
        return risk
    return risk.model_copy(update=p.overrides)


def horizon_bars(profile_id: str, risk: RiskConfig) -> int:
    per_hour = {"15m": 4, "1h": 1, "4h": 0.25, "1d": 1 / 24}[PROFILES[profile_id].base_tf]
    return max(4, int(risk.signal_horizon_hours * per_hour))


def prepare(ctx, strategy, risk: RiskConfig) -> RiskConfig | None:
    """Set profile context on ctx and return the profile's risk rules, or None if the market doesn't qualify
    for this profile (e.g. not liquid enough for scalping)."""
    prof = PROFILES.get(getattr(strategy, "profile", "swing"), PROFILES["swing"])
    pr = profile_risk(risk, prof.id)
    ctx.extras["res_tfs"] = prof.res_tfs
    ctx.extras["profile"] = prof.id
    qv = ctx.extras.get("quote_volume_24h")
    if qv is not None and qv < pr.min_quote_volume_usdt:
        return None
    return pr
