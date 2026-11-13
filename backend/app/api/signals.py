from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..engine.lifecycle import LifecycleError, report_closed, report_entered, report_skipped
from ..market.binance import MarketDataError, get_client
from ..models import AgentRun, Signal, User, UserTrade
from ..security import audit, require_subscriber
from ..trading_config import load_config

router = APIRouter(prefix="/api", tags=["signals"])


def signal_summary(s: Signal, my_trade: UserTrade | None = None) -> dict:
    return {
        "id": s.id, "symbol": s.symbol, "pair": f"{s.base_asset}/{s.quote_asset}", "direction": s.direction,
        "created_at": s.created_at, "entry_price": s.entry_price, "take_profit": s.take_profit, "stop_loss": s.stop_loss,
        "expected_move_pct": s.expected_move_pct, "net_expected_return_pct": s.net_expected_return_pct,
        "risk_reward": s.risk_reward, "confidence": s.confidence, "status": s.status,
        "entry_valid_until": s.entry_valid_until, "expires_at": s.expires_at, "closed_at": s.closed_at,
        "last_price": s.last_price, "justification": s.justification, "narrative": s.narrative,
        "theoretical_outcome": s.theoretical_outcome, "theoretical_return_pct": s.theoretical_return_pct,
        "paper_return_pct": s.paper_trade.return_pct if s.paper_trade else None,
        "my_decision": my_trade.decision if my_trade else None,
        "my_trade_status": my_trade.status if my_trade else None,
    }


def _my_trades(db: Session, user: User, ids: list[int]) -> dict[int, UserTrade]:
    if not ids:
        return {}
    return {t.signal_id: t for t in db.scalars(select(UserTrade).where(UserTrade.user_id == user.id,
                                                                       UserTrade.signal_id.in_(ids)))}


@router.get("/signals")
def list_signals(status: str | None = None, limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                 user: User = Depends(require_subscriber), db: Session = Depends(get_db)):
    q = select(Signal).order_by(Signal.id.desc()).limit(limit).offset(offset)
    if status:
        q = q.where(Signal.status.in_([x.strip().upper() for x in status.split(",")]))
    sigs = list(db.scalars(q))
    mine = _my_trades(db, user, [s.id for s in sigs])
    return [signal_summary(s, mine.get(s.id)) for s in sigs]


@router.get("/signals/{sid}")
def get_signal(sid: int, user: User = Depends(require_subscriber), db: Session = Depends(get_db)):
    s = db.get(Signal, sid)
    if not s:
        raise HTTPException(404, "Signal not found")
    mine = _my_trades(db, user, [s.id]).get(s.id)
    out = signal_summary(s, mine)
    out |= {
        "confidence_breakdown": s.confidence_breakdown, "agent_outputs": s.agent_outputs,
        "position_sizing": s.position_sizing,
        "events": [{"at": e.at, "from": e.from_status, "to": e.to_status, "note": e.note, "actor": e.actor} for e in s.events],
        "paper_trade": None if not s.paper_trade else {
            k: getattr(s.paper_trade, k) for k in ("status", "entry_price", "quantity", "exit_price", "outcome", "fees",
                                                   "slippage_cost", "pnl", "return_pct", "opened_at", "closed_at")},
        "my_trade": None if not mine else {k: getattr(mine, k) for k in (
            "decision", "status", "entry_price", "quantity", "exit_price", "fees", "realized_pnl", "realized_pct",
            "slippage_pct", "notes", "entry_at", "exit_at")},
    }
    return out


@router.get("/signals/{sid}/candles")
async def signal_candles(sid: int, interval: str = Query("1h", pattern="^(15m|1h|4h)$"),
                         user: User = Depends(require_subscriber), db: Session = Depends(get_db)):
    s = db.get(Signal, sid)
    if not s:
        raise HTTPException(404, "Signal not found")
    start = int((s.created_at - timedelta(hours=72)).timestamp() * 1000)
    try:
        cs = await get_client().klines_since(s.symbol, interval, start, 500)
    except MarketDataError as e:
        raise HTTPException(503, f"Market data unavailable: {e}")
    return [{"t": c.open_time, "o": c.open, "h": c.high, "l": c.low, "c": c.close, "v": c.volume} for c in cs]


@router.get("/signals/{sid}/agents")
def signal_agent_runs(sid: int, user: User = Depends(require_subscriber), db: Session = Depends(get_db)):
    s = db.get(Signal, sid)
    if not s:
        raise HTTPException(404, "Signal not found")
    runs = db.scalars(select(AgentRun).where(AgentRun.cycle_id == s.cycle_id,
                                             (AgentRun.symbol == s.symbol) | (AgentRun.symbol.is_(None))))
    return [{"agent_id": r.agent_id, "passed": r.passed, "score": r.score, "status": r.status, "duration_ms": r.duration_ms,
             "reasons": r.output.get("reasons", []), "error": r.error} for r in runs]


class ReportIn(BaseModel):
    action: str = Field(pattern="^(ENTERED|SKIPPED|CLOSED)$")
    price: float | None = Field(None, gt=0)
    quantity: float | None = Field(None, gt=0)
    fees: float = Field(0.0, ge=0)
    notes: str = Field("", max_length=2000)


@router.post("/signals/{sid}/report")
def report(sid: int, body: ReportIn, user: User = Depends(require_subscriber), db: Session = Depends(get_db)):
    s = db.get(Signal, sid)
    if not s:
        raise HTTPException(404, "Signal not found")
    try:
        if body.action == "ENTERED":
            if body.price is None:
                raise LifecycleError("actual entry price is required")
            t = report_entered(db, user, s, body.price, body.quantity, body.fees, body.notes, load_config(db).risk)
        elif body.action == "SKIPPED":
            t = report_skipped(db, user, s, body.notes)
        else:
            if body.price is None:
                raise LifecycleError("actual exit price is required")
            t = report_closed(db, user, s, body.price, body.fees, body.quantity, body.notes)
    except LifecycleError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    audit(db, f"user:{user.id}", f"report_{body.action.lower()}", signal_id=sid, price=body.price)
    return {"ok": True, "trade_id": t.id, "status": t.status}


@router.get("/trades")
def journal(user: User = Depends(require_subscriber), db: Session = Depends(get_db)):
    rows = db.execute(select(UserTrade, Signal).join(Signal, Signal.id == UserTrade.signal_id)
                      .where(UserTrade.user_id == user.id).order_by(UserTrade.id.desc())).all()
    out = []
    for t, s in rows:
        dur = (t.exit_at - t.entry_at).total_seconds() / 3600 if t.exit_at and t.entry_at else None
        out.append({"id": t.id, "signal_id": s.id, "pair": f"{s.base_asset}/{s.quote_asset}", "decision": t.decision,
                    "status": t.status, "signal_entry": s.entry_price, "take_profit": s.take_profit, "stop_loss": s.stop_loss,
                    "entry_price": t.entry_price, "exit_price": t.exit_price, "quantity": t.quantity, "fees": t.fees,
                    "realized_pnl": t.realized_pnl, "realized_pct": t.realized_pct, "slippage_pct": t.slippage_pct,
                    "duration_hours": dur, "notes": t.notes, "entry_at": t.entry_at, "exit_at": t.exit_at,
                    "created_at": t.created_at, "signal_status": s.status})
    return out


class NotesIn(BaseModel):
    notes: str = Field(max_length=4000)


@router.patch("/trades/{tid}")
def update_notes(tid: int, body: NotesIn, user: User = Depends(require_subscriber), db: Session = Depends(get_db)):
    t = db.get(UserTrade, tid)
    if not t or t.user_id != user.id:
        raise HTTPException(404, "Trade not found")
    t.notes = body.notes
    db.commit()
    return {"ok": True}
