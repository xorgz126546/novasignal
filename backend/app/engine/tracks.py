"""Load each performance track from the DB (kept strictly separate)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PaperTrade, Signal, UserTrade
from .performance import TradeResult


def theoretical(db: Session, start: datetime | None = None, end: datetime | None = None) -> list[TradeResult]:
    q = select(Signal).where(Signal.theoretical_outcome.is_not(None))
    if start:
        q = q.where(Signal.theoretical_closed_at >= start)
    if end:
        q = q.where(Signal.theoretical_closed_at < end)
    return [TradeResult(s.theoretical_closed_at, s.theoretical_return_pct or 0.0, symbol=s.symbol) for s in db.scalars(q)]


def paper(db: Session, start: datetime | None = None, end: datetime | None = None) -> list[TradeResult]:
    q = select(PaperTrade).where(PaperTrade.status == "CLOSED")
    if start:
        q = q.where(PaperTrade.closed_at >= start)
    if end:
        q = q.where(PaperTrade.closed_at < end)
    return [TradeResult(p.closed_at, p.return_pct or 0.0, p.pnl, p.fees, p.slippage_cost, p.signal.symbol)
            for p in db.scalars(q)]


def actual(db: Session, user_id: int | None = None, start: datetime | None = None, end: datetime | None = None) -> list[TradeResult]:
    q = select(UserTrade).where(UserTrade.status == "CLOSED")
    if user_id is not None:
        q = q.where(UserTrade.user_id == user_id)
    if start:
        q = q.where(UserTrade.exit_at >= start)
    if end:
        q = q.where(UserTrade.exit_at < end)
    return [TradeResult(t.exit_at, t.realized_pct or 0.0, t.realized_pnl, t.fees) for t in db.scalars(q)]


def load(db: Session, track: str, user_id: int | None = None, start=None, end=None) -> list[TradeResult]:
    if track == "theoretical":
        return theoretical(db, start, end)
    if track == "paper":
        return paper(db, start, end)
    if track == "actual":
        return actual(db, user_id, start, end)
    raise ValueError(f"unknown track {track}")
