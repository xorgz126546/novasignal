"""XP and levels. XP comes ONLY from verified events with a unique source reference.

There is deliberately no API or agent tool to award XP directly: award() is called by code that has
verified the event (tracker outcome, merged PR checked on GitHub, backtest gate result, owner approval).
"""
from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import bus
from .models import HQAgent, HQXPEvent
from .registry import level_for_xp

XP_RULES = {
    "signal_tp_hit": 40,          # published signal reached its take-profit (tracker-verified)
    "no_trade_avoided_loss": 25,  # rejected setup would have hit its stop first (candle-verified)
    "bug_found": 20,              # watchdog incident confirmed by evidence
    "fix_merged": 60,             # Engineer PR merged by the owner (verified on GitHub)
    "backtest_passed": 30,        # proposal/strategy passed the walk-forward gate
    "proposal_approved": 50,      # owner approved a proposal/draft
    "signal_sl_hit": 0,           # recorded for stats; no XP
}


def award(db: Session, agent_id: str, source_type: str, source_ref: str, note: str = "") -> int:
    if source_type not in XP_RULES:
        raise ValueError(f"unverified XP source {source_type!r}")
    xp = XP_RULES[source_type]
    db.add(HQXPEvent(agent_id=agent_id, source_type=source_type, source_ref=source_ref[:128], xp=xp, note=note[:500]))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()  # already counted
        return 0
    row = db.get(HQAgent, agent_id) or HQAgent(id=agent_id)
    db.add(row)
    before = row.level or 1
    row.xp = (row.xp or 0) + xp
    row.level = level_for_xp(row.xp)
    db.commit()
    if xp:
        bus.publish(db, "xp", agent_id, xp=xp, total=row.xp, source=source_type, note=note[:200])
    if row.level > before:
        bus.publish(db, "level_up", agent_id, level=row.level, reason=f"{source_type}: {note}"[:300])
    return xp


def level_weight(level: int) -> float:
    """Scoring weight from level: Lv1 = 1.00 … Lv10 = 1.09 (bounded so levels can't dominate evidence)."""
    return 1.0 + 0.01 * (max(1, min(level, 10)) - 1)
