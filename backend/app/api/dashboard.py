from __future__ import annotations

from collections import Counter
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..agents import ALL_AGENTS
from ..db import get_db
from ..engine import tracks
from ..engine.performance import bucket, compute_metrics, json_safe
from ..models import (TERMINAL_STATUSES, AgentRun, Opportunity, PaperTrade, ScanCycle, ServiceState, Signal, User,
                      UserTrade, utcnow)
from ..security import require_subscriber
from ..trading_config import load_config

router = APIRouter(prefix="/api", tags=["dashboard"])


def _metrics(db, track, user_id=None, start=None):
    rows = tracks.load(db, track, user_id, start)
    return json_safe(compute_metrics(rows)), rows


@router.get("/overview")
def overview(user: User = Depends(require_subscriber), db: Session = Depends(get_db)):
    now = utcnow()
    total_opps = db.scalar(select(func.count()).select_from(Opportunity)) or 0
    approved = db.scalar(select(func.count()).select_from(Signal)) or 0
    open_trade = db.execute(select(UserTrade, Signal).join(Signal, Signal.id == UserTrade.signal_id)
                            .where(UserTrade.user_id == user.id, UserTrade.status == "OPEN")).first()
    out = {"opportunities_evaluated": total_opps, "signals_approved": approved,
           "open_signals": db.scalar(select(func.count()).select_from(Signal).where(
               Signal.status.not_in([s.value for s in TERMINAL_STATUSES]))) or 0,
           "current_position": None, "tracks": {}, "periods": {}}
    if open_trade:
        t, s = open_trade
        out["current_position"] = {"signal_id": s.id, "pair": f"{s.base_asset}/{s.quote_asset}", "entry_price": t.entry_price,
                                   "take_profit": s.take_profit, "stop_loss": s.stop_loss, "last_price": s.last_price,
                                   "unrealized_pct": (s.last_price - t.entry_price) / t.entry_price * 100
                                   if s.last_price else None}
    for track in ("theoretical", "paper", "actual"):
        m, _ = _metrics(db, track, user.id if track == "actual" else None)
        out["tracks"][track] = m
        out["periods"][track] = {p: json_safe(compute_metrics(tracks.load(db, track, user.id if track == "actual" else None,
                                                                          now - timedelta(days=d))))
                                 for p, d in (("day", 1), ("week", 7), ("month", 30))}
    last_cycle = db.scalar(select(ScanCycle).order_by(ScanCycle.id.desc()).limit(1))
    out["last_scan"] = None if not last_cycle else {"at": last_cycle.started_at, "status": last_cycle.status,
                                                    "candidates": last_cycle.candidates, "approved": last_cycle.approved,
                                                    "notes": last_cycle.notes}
    out["agents"] = agent_status(db)
    out["paused"] = load_config(db).paused
    return out


def agent_status(db: Session) -> list[dict]:
    since = utcnow() - timedelta(hours=24)
    rows = db.execute(select(AgentRun.agent_id, func.count(), func.max(AgentRun.started_at))
                      .group_by(AgentRun.agent_id)).all()
    total = {r[0]: (r[1], r[2]) for r in rows}
    recent = db.execute(select(AgentRun.agent_id, AgentRun.status, AgentRun.passed)
                        .where(AgentRun.started_at >= since)).all()
    errs24 = Counter(r[0] for r in recent if r[1] == "error")
    runs24 = Counter(r[0] for r in recent)
    out = []
    for a in ALL_AGENTS:
        n, last = total.get(a.id, (0, None))
        status = "idle" if n == 0 else ("error" if errs24.get(a.id, 0) and errs24[a.id] == runs24[a.id] else
                                        "degraded" if errs24.get(a.id) else "ok")
        out.append({"id": a.id, "number": a.number, "name": a.name, "status": status, "last_run": last, "runs": n,
                    "runs_24h": runs24.get(a.id, 0), "errors_24h": errs24.get(a.id, 0)})
    return out


@router.get("/agents")
def agents(user: User = Depends(require_subscriber), db: Session = Depends(get_db)):
    base = {a["id"]: a for a in agent_status(db)}
    stats = db.execute(select(AgentRun.agent_id, AgentRun.passed, func.count(), func.avg(AgentRun.duration_ms))
                       .group_by(AgentRun.agent_id, AgentRun.passed)).all()
    for aid, passed, n, avg in stats:
        b = base.get(aid)
        if not b:
            continue
        b.setdefault("approved", 0)
        b.setdefault("rejected", 0)
        b["approved" if passed else "rejected"] += n
        b["avg_ms"] = avg
    # Contribution: avg score of each agent on signals that hit TP vs SL (theoretical track).
    resolved = list(db.scalars(select(Signal).where(Signal.theoretical_outcome.in_(["TARGET_HIT", "STOP_LOSS_HIT"]))))
    for a in ALL_AGENTS:
        w = [s.agent_outputs.get(a.id, {}).get("score") for s in resolved if s.theoretical_outcome == "TARGET_HIT"]
        l = [s.agent_outputs.get(a.id, {}).get("score") for s in resolved if s.theoretical_outcome == "STOP_LOSS_HIT"]
        w, l = [x for x in w if x is not None], [x for x in l if x is not None]
        base[a.id]["contribution"] = {"avg_score_winners": sum(w) / len(w) if w else None,
                                      "avg_score_losers": sum(l) / len(l) if l else None,
                                      "resolved_signals": len(w) + len(l)}
        base[a.id]["responsibility"] = a.responsibility
    return list(base.values())


@router.get("/agents/{agent_id}/runs")
def agent_runs(agent_id: str, limit: int = Query(50, le=500), errors_only: bool = False,
               user: User = Depends(require_subscriber), db: Session = Depends(get_db)):
    if agent_id not in {a.id for a in ALL_AGENTS}:
        raise HTTPException(404, "Unknown agent")
    q = select(AgentRun).where(AgentRun.agent_id == agent_id).order_by(AgentRun.id.desc()).limit(limit)
    if errors_only:
        q = q.where(AgentRun.status == "error")
    return [{"id": r.id, "cycle_id": r.cycle_id, "symbol": r.symbol, "at": r.started_at, "status": r.status,
             "passed": r.passed, "score": r.score, "duration_ms": r.duration_ms, "reasons": r.output.get("reasons", [])[:6],
             "error": r.error} for r in db.scalars(q)]


@router.get("/agents/schema")
def agent_schemas(user: User = Depends(require_subscriber)):
    return [a.describe() for a in ALL_AGENTS]


@router.get("/analytics")
def analytics(track: str = Query("paper", pattern="^(theoretical|paper|actual)$"), days: int = Query(90, ge=1, le=3650),
              user: User = Depends(require_subscriber), db: Session = Depends(get_db)):
    start = utcnow() - timedelta(days=days)
    rows = tracks.load(db, track, user.id if track == "actual" else None, start)
    m = json_safe(compute_metrics(rows))
    eq, curve = 1.0, []
    for r in sorted(rows, key=lambda x: x.closed_at):
        eq *= 1 + r.return_pct / 100
        curve.append({"t": r.closed_at, "equity": eq * 100, "ret": r.return_pct})
    gen = db.scalar(select(func.count()).select_from(Signal).where(Signal.created_at >= start)) or 0
    executed = db.scalar(select(func.count()).select_from(UserTrade).where(
        UserTrade.user_id == user.id, UserTrade.decision == "ENTERED", UserTrade.created_at >= start)) or 0
    skipped = db.scalar(select(func.count()).select_from(UserTrade).where(
        UserTrade.user_id == user.id, UserTrade.decision == "SKIPPED", UserTrade.created_at >= start)) or 0
    paper_open = db.scalar(select(func.count()).select_from(PaperTrade).where(PaperTrade.status == "OPEN")) or 0
    return {"track": track, "metrics": m, "daily": bucket(rows, "day"), "weekly": bucket(rows, "week"),
            "monthly": bucket(rows, "month"), "equity_curve": curve,
            "funnel": {"generated": gen, "executed": executed, "skipped": skipped, "paper_open": paper_open},
            "note": "Track metrics are computed independently. Equity curve compounds full-size % returns."}


@router.get("/system/status")
def system_status(user: User = Depends(require_subscriber), db: Session = Depends(get_db)):
    from ..market.binance import get_client

    c = get_client()
    return {"services": [{"name": s.name, "status": s.status, "last_ok_at": s.last_ok_at, "last_error_at": s.last_error_at,
                          "last_error": s.last_error if user.role == "admin" else None}
                         for s in db.scalars(select(ServiceState))],
            "binance": {"base_url": c.base_url, "last_ok_at": c.last_ok_at, "last_error": c.last_error,
                        "public_data_only": True}}
