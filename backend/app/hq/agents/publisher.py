"""HQ Publisher: dedup, 0-100 score with configurable minimum, persistence + in-app + Telegram."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...engine import lifecycle
from ...models import TERMINAL_STATUSES, Signal, SignalEvent, SignalStatus
from ...trading_config import RiskConfig
from ..features import Context
from ..models import HQAgent
from ..strategies.base import Setup
from ..versions import active_data
from ..xp import level_weight

DEFAULT_MIN_SCORE = 60.0
DISCLAIMER = ("Not financial advice. No profit is guaranteed. Spot only — you decide and execute manually on Binance. "
              "ليست نصيحة مالية ولا يوجد ربح مضمون.")


def min_score(db: Session) -> float:
    return float((active_data(db, "publisher") or {}).get("min_score", DEFAULT_MIN_SCORE))


def score(setup: Setup, ctx: Context, risk: RiskConfig, agent_level: int) -> tuple[float, dict]:
    f = ctx.features
    parts = {
        "strategy_quality": 45 * setup.quality,
        "structure_alignment": 10 * (f.get("bias_4h", 0) > 0) + 10 * (f.get("bias_1h", 0) > 0),
        "reward_to_risk": 20 * min(setup.net_rr / (2 * risk.min_risk_reward), 1.0),
        "volume": 15 * min(f.get("vol_rel_1h", 0) / 1.5, 1.0),
    }
    raw = sum(parts.values())
    w = level_weight(agent_level)
    return round(min(raw * w, 100.0), 2), {k: round(v, 2) for k, v in parts.items()} | {"level_weight": w}


def is_duplicate(db: Session, symbol: str, strategy_id: str, now: datetime, cooldown_h: float) -> str | None:
    open_sig = db.scalar(select(Signal).where(Signal.symbol == symbol,
                                              Signal.status.not_in([s.value for s in TERMINAL_STATUSES])))
    if open_sig:
        return f"duplicate: {symbol} already has open signal #{open_sig.id}"
    recent = db.scalar(select(Signal).where(Signal.symbol == symbol, Signal.created_at >= now - timedelta(hours=cooldown_h)))
    if recent:
        return f"duplicate: {symbol} was signalled within the last {cooldown_h:.0f}h (#{recent.id})"
    return None


def agent_level(db: Session, agent_id: str) -> int:
    row = db.get(HQAgent, agent_id)
    return row.level if row else 1


def publish(db: Session, setup: Setup, ctx: Context, risk: RiskConfig, sizing: dict, sc: float, breakdown: dict,
            reasons: list[str], now: datetime, cycle_ref: str) -> Signal:
    sig = Signal(
        symbol=setup.symbol, base_asset=setup.symbol.removesuffix("USDT"), quote_asset="USDT", direction="BUY",
        created_at=now, entry_price=setup.entry, take_profit=setup.take_profit, stop_loss=setup.stop,
        expected_move_pct=setup.move_pct, risk_reward=setup.rr,
        net_expected_return_pct=round(setup.move_pct - 2 * (risk.fee_rate_pct + risk.slippage_pct), 4),
        confidence=sc, confidence_breakdown=breakdown, justification=reasons + [DISCLAIMER],
        agent_outputs={"hq": {"cycle_ref": cycle_ref, "strategy": setup.strategy_id, "quality": setup.quality,
                              "features": {k: v for k, v in ctx.features.items() if k.endswith(("_4h", "_1h"))}}},
        position_sizing=sizing, status=SignalStatus.ANALYZING.value,
        entry_valid_until=now + timedelta(hours=risk.entry_validity_hours),
        expires_at=now + timedelta(hours=risk.signal_horizon_hours), last_price=ctx.last_price,
        source="hq", strategy_id=setup.strategy_id, invalidation_price=setup.invalidation,
        risk_pct=sizing.get("risk_pct"), score=sc)
    db.add(sig)
    db.flush()
    db.add(SignalEvent(signal_id=sig.id, from_status=None, to_status=SignalStatus.ANALYZING.value, note=f"HQ {cycle_ref}"))
    lifecycle.transition(db, sig, SignalStatus.APPROVED, f"HQ: risk approved, score {sc:.0f}", actor="hq:publisher")
    lifecycle.open_paper_trade(db, sig, ctx.last_price, risk)
    lifecycle.transition(db, sig, SignalStatus.ACTIVE, "published by HQ Publisher", actor="hq:publisher")
    db.commit()
    return sig
