from __future__ import annotations

import csv
import io
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (AgentRun, AuditLog, Opportunity, PaperTrade, ScanCycle, ServiceState, Signal, SignalStatus,
                      Subscription, User, UserTrade, utcnow)
from ..security import audit, require_admin
from ..trading_config import load_config, save_config

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/stats")
def stats(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    since = utcnow() - timedelta(days=7)
    subs = dict(db.execute(select(Subscription.status, func.count()).group_by(Subscription.status)).all())
    active_paid = subs.get("active", 0)
    return {
        "users": db.scalar(select(func.count()).select_from(User)) or 0,
        "active_users_7d": db.scalar(select(func.count()).select_from(User).where(User.last_login_at >= since)) or 0,
        "telegram_linked": db.scalar(select(func.count()).select_from(User).where(User.telegram_chat_id.is_not(None))) or 0,
        "subscriptions": subs,
        "mrr_usd": active_paid * 25,  # computed from real subscription rows only
        "target": {"subscribers": 300, "mrr_usd": 7500},
        "signals_total": db.scalar(select(func.count()).select_from(Signal)) or 0,
        "scan_cycles_7d": db.scalar(select(func.count()).select_from(ScanCycle).where(ScanCycle.started_at >= since)) or 0,
        "agent_errors_7d": db.scalar(select(func.count()).select_from(AgentRun).where(
            AgentRun.status == "error", AgentRun.started_at >= since)) or 0,
        "paused": load_config(db).paused,
    }


@router.get("/users")
def users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    out = []
    for u in db.scalars(select(User).order_by(User.id)):
        n_trades = db.scalar(select(func.count()).select_from(UserTrade).where(UserTrade.user_id == u.id)) or 0
        out.append({"id": u.id, "email": u.email, "role": u.role, "is_active": u.is_active, "created_at": u.created_at,
                    "last_login_at": u.last_login_at, "telegram_linked": bool(u.telegram_chat_id),
                    "subscription": u.subscription.status if u.subscription else "none", "reports": n_trades})
    return out


class UserPatch(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    subscription_status: str | None = None


@router.patch("/users/{uid}")
def patch_user(uid: int, body: UserPatch, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404)
    if u.id == admin.id and (body.role == "user" or body.is_active is False):
        raise HTTPException(400, "You cannot demote or disable your own admin account")
    if body.role is not None:
        if body.role not in ("user", "admin"):
            raise HTTPException(422, "invalid role")
        u.role = body.role
    if body.is_active is not None:
        u.is_active = body.is_active
    if body.subscription_status is not None:
        if body.subscription_status not in ("none", "trial", "active", "past_due", "cancelled", "expired"):
            raise HTTPException(422, "invalid subscription status")
        sub = u.subscription or Subscription(user_id=u.id)
        sub.status = body.subscription_status
        if body.subscription_status in ("trial", "active"):
            sub.current_period_end = utcnow() + timedelta(days=30)
        db.add(sub)
    db.commit()
    audit(db, f"user:{admin.id}", "admin_patch_user", target=uid, changes=body.model_dump(exclude_none=True))
    return {"ok": True}


@router.post("/pause")
def pause(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    cfg = load_config(db)
    cfg.paused = True
    save_config(db, cfg)
    audit(db, f"user:{admin.id}", "pause_signals")
    return {"paused": True}


@router.post("/resume")
def resume(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    cfg = load_config(db)
    cfg.paused = False
    save_config(db, cfg)
    audit(db, f"user:{admin.id}", "resume_signals")
    return {"paused": False}


@router.post("/scan")
async def scan_now(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    from ..engine.pipeline import run_scan_cycle
    from ..market.binance import get_client
    from ..telegram.bot import notify

    audit(db, f"user:{admin.id}", "manual_scan")
    c = await run_scan_cycle(db, get_client(), notify)
    return {"cycle_id": c.id, "status": c.status, "candidates": c.candidates, "approved": c.approved,
            "rejected": c.rejected, "notes": c.notes, "error": c.error}


@router.post("/track")
async def track_now(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    from ..engine.tracker import track_open_signals
    from ..market.binance import get_client
    from ..telegram.bot import notify

    n = await track_open_signals(db, get_client(), notify)
    return {"resolved": n}


@router.post("/services/{name}/restart")
async def restart(name: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    from .. import scheduler
    from ..market import binance
    from ..telegram import bot

    if name == "binance":
        old = binance.get_client()
        binance.set_client(None)
        await old.close()
        binance.get_client()
    elif name == "telegram":
        if bot._client:
            await bot._client.close()
        bot.set_client(None)
        bot.get_client()
    elif name in ("scheduler", "scanner", "tracker"):
        await scheduler.restart()
    else:
        raise HTTPException(404, "unknown service")
    st = db.get(ServiceState, name) or ServiceState(name=name)
    st.restarts = (st.restarts or 0) + 1
    st.status = "ok"
    db.add(st)
    db.commit()
    audit(db, f"user:{admin.id}", "restart_service", service=name)
    return {"ok": True, "service": name}


@router.post("/signals/{sid}/cancel")
def cancel_signal(sid: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    from ..engine.lifecycle import LifecycleError, transition

    s = db.get(Signal, sid)
    if not s:
        raise HTTPException(404)
    try:
        transition(db, s, SignalStatus.CANCELLED, "cancelled by admin", actor=f"user:{admin.id}")
        if s.paper_trade and s.paper_trade.status == "OPEN":
            db.delete(s.paper_trade)  # cancelled before outcome: excluded from paper results (documented)
    except LifecycleError as e:
        raise HTTPException(400, str(e))
    db.commit()
    audit(db, f"user:{admin.id}", "cancel_signal", signal_id=sid)
    return {"ok": True}


@router.get("/cycles")
def cycles(limit: int = Query(50, le=500), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [{"id": c.id, "started_at": c.started_at, "finished_at": c.finished_at, "status": c.status,
             "candidates": c.candidates, "approved": c.approved, "rejected": c.rejected, "notes": c.notes, "error": c.error}
            for c in db.scalars(select(ScanCycle).order_by(ScanCycle.id.desc()).limit(limit))]


@router.get("/opportunities")
def opportunities(limit: int = Query(100, le=1000), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [{"id": o.id, "cycle_id": o.cycle_id, "symbol": o.symbol, "decision": o.decision, "confidence": o.confidence,
             "reasons": o.rejection_reasons, "at": o.created_at, "signal_id": o.signal_id}
            for o in db.scalars(select(Opportunity).order_by(Opportunity.id.desc()).limit(limit))]


@router.get("/errors")
def errors(limit: int = Query(100, le=1000), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    agent_errs = [{"source": f"agent:{r.agent_id}", "symbol": r.symbol, "at": r.started_at, "error": r.error}
                  for r in db.scalars(select(AgentRun).where(AgentRun.status == "error").order_by(AgentRun.id.desc()).limit(limit))]
    cyc = [{"source": "scan_cycle", "symbol": None, "at": c.started_at, "error": c.error}
           for c in db.scalars(select(ScanCycle).where(ScanCycle.status == "error").order_by(ScanCycle.id.desc()).limit(limit))]
    svc = [{"source": f"service:{s.name}", "symbol": None, "at": s.last_error_at, "error": s.last_error}
           for s in db.scalars(select(ServiceState).where(ServiceState.last_error.is_not(None)))]
    return sorted(agent_errs + cyc + svc, key=lambda x: str(x["at"]), reverse=True)[:limit]


@router.get("/audit")
def audit_log(limit: int = Query(200, le=2000), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [{"id": a.id, "at": a.at, "actor": a.actor, "action": a.action, "details": a.details}
            for a in db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit))]


EXPORTS = {
    "signals": (Signal, ["id", "symbol", "created_at", "entry_price", "take_profit", "stop_loss", "expected_move_pct",
                         "risk_reward", "confidence", "status", "theoretical_outcome", "theoretical_return_pct",
                         "theoretical_closed_at"]),
    "paper_trades": (PaperTrade, ["id", "signal_id", "status", "entry_price", "quantity", "exit_price", "outcome", "fees",
                                  "slippage_cost", "pnl", "return_pct", "opened_at", "closed_at"]),
    "user_trades": (UserTrade, ["id", "user_id", "signal_id", "decision", "status", "entry_price", "quantity", "exit_price",
                                "fees", "realized_pnl", "realized_pct", "slippage_pct", "entry_at", "exit_at"]),
    "opportunities": (Opportunity, ["id", "cycle_id", "symbol", "decision", "confidence", "rejection_reasons", "created_at"]),
}


@router.get("/export/{kind}.csv")
def export(kind: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if kind not in EXPORTS:
        raise HTTPException(404)
    model, cols = EXPORTS[kind]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for row in db.scalars(select(model).order_by(model.id)):
        w.writerow([getattr(row, c) for c in cols])
    audit(db, f"user:{admin.id}", "export", kind=kind)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="cryptonex_{kind}.csv"'})
