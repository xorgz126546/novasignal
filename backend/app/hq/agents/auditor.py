"""Auditor/Watchdog: verified outcomes → XP, NO TRADE audits, data-feed health, repeated errors → incidents."""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...market.binance import BinanceClient, MarketDataError, closed_candles
from ...models import AgentRun, ScanCycle, ServiceState, Signal, utcnow
from .. import bus, runtime
from ..models import HQDecision, HQEvent, HQIncident, HQTask
from ..xp import award

log = logging.getLogger("cryptonex.hq.auditor")
STAGE_AGENT = {"risk": "risk", "publisher": "publisher", "manager": "manager"}


def credit_outcomes(db: Session) -> int:
    """XP for HQ signals whose take-profit was reached (tracker-verified theoretical outcome)."""
    n = 0
    for s in db.scalars(select(Signal).where(Signal.source == "hq", Signal.theoretical_outcome.is_not(None))):
        agent = (s.agent_outputs or {}).get("hq", {}).get("agent_id")
        if s.theoretical_outcome == "TARGET_HIT":
            for a in filter(None, (agent, "publisher")):
                n += award(db, a, "signal_tp_hit", f"signal:{s.id}", note=f"{s.symbol} hit TP")
        elif s.theoretical_outcome == "STOP_LOSS_HIT" and agent:
            award(db, agent, "signal_sl_hit", f"signal:{s.id}", note=f"{s.symbol} hit SL")
    return n


async def audit_no_trades(db: Session, client: BinanceClient, horizon_h: float = 48) -> dict:
    """For setups stopped by risk/manager/publisher: did the stop hit first (avoided loss) or the target (missed win)?"""
    now = utcnow()
    counts = {"avoided_loss": 0, "missed_win": 0, "expired": 0}
    rows = list(db.scalars(select(HQDecision).where(HQDecision.decision == "NO_TRADE", HQDecision.audited.is_(False),
                                                     HQDecision.stage.in_(list(STAGE_AGENT)))))
    for d in rows:
        lv = d.levels or {}
        if not lv.get("stop") or not lv.get("take_profit"):
            d.audited, d.audit_result = True, "no_levels"
            continue
        start = int(d.created_at.timestamp() * 1000)
        try:
            cs = closed_candles(await client.klines_since(d.symbol, "1h", start, 100))
        except MarketDataError:
            continue
        result = None
        for c in cs:
            if c.low <= lv["stop"]:
                result = "avoided_loss"
                break
            if c.high >= lv["take_profit"]:
                result = "missed_win"
                break
        if result is None and now - d.created_at >= timedelta(hours=horizon_h):
            result = "expired"
        if result:
            d.audited, d.audit_result = True, result
            counts[result] += 1
            db.commit()
            if result == "avoided_loss":
                award(db, STAGE_AGENT[d.stage], "no_trade_avoided_loss", f"decision:{d.id}",
                      note=f"{d.symbol} {d.strategy_id}: stop would have been hit")
    db.commit()
    return counts


def _open_incident(db: Session, kind: str, key: str, title: str, details: dict) -> HQIncident | None:
    existing = db.scalar(select(HQIncident).where(HQIncident.key == key, HQIncident.status != "resolved"))
    if existing:
        return None
    inc = HQIncident(kind=kind, key=key, title=title, details=details)
    db.add(inc)
    db.commit()
    bus.publish(db, "incident", "auditor", incident_id=inc.id, kind=kind, title=title)
    return inc


def _resolve(db: Session, key: str, why: str) -> None:
    for inc in db.scalars(select(HQIncident).where(HQIncident.key == key, HQIncident.status != "resolved")):
        if inc.pr_number:  # a fix is in flight: the owner closes it by merging (or it stays visible)
            continue
        inc.status, inc.resolved_at = "resolved", utcnow()
        inc.details = {**inc.details, "resolved_because": why}
        bus.publish(db, "incident_resolved", "auditor", incident_id=inc.id, title=inc.title)
    db.commit()


def health_check(db: Session, client: BinanceClient, scan_interval_min: int = 15) -> list[HQIncident]:
    """Deterministic checks. Opens deduplicated incidents and auto-resolves them when the condition clears."""
    now = utcnow()
    opened: list[HQIncident] = []
    # 1) Binance feed
    if client.last_error:
        inc = _open_incident(db, "feed_down", "feed:binance", "Binance market-data feed is failing",
                             {"error": client.last_error, "last_ok_at": str(client.last_ok_at)})
        if inc:
            opened.append(inc)
    else:
        _resolve(db, "feed:binance", "feed healthy again")
    # 2) stale data: no successful scan (classic or HQ) for 3 intervals
    last_cycle = db.scalar(select(func.max(ScanCycle.started_at)).where(ScanCycle.status == "ok"))
    last_hq = db.scalar(select(func.max(HQEvent.at)).where(HQEvent.type == "cycle_done"))
    last = max([x for x in (last_cycle, last_hq) if x], default=None)
    if last and now - last > timedelta(minutes=3 * scan_interval_min):
        inc = _open_incident(db, "stale_data", "stale:scan", "No successful market scan recently",
                             {"last_success": str(last), "minutes": round((now - last).total_seconds() / 60)})
        if inc:
            opened.append(inc)
    elif last:
        _resolve(db, "stale:scan", "scans running again")
    # 3) repeated errors in the last hour
    since = now - timedelta(hours=1)
    rows = db.execute(select(AgentRun.agent_id, func.count()).where(AgentRun.status == "error", AgentRun.started_at >= since)
                      .group_by(AgentRun.agent_id)).all()
    for agent_id, n in rows:
        if n >= 5:
            sample = db.scalar(select(AgentRun.error).where(AgentRun.agent_id == agent_id, AgentRun.status == "error")
                               .order_by(AgentRun.id.desc()).limit(1))
            inc = _open_incident(db, "repeated_errors", f"errors:agent:{agent_id}", f"Agent {agent_id} failed {n}× in 1h",
                                 {"count": n, "sample_error": sample})
            if inc:
                opened.append(inc)
    failed = db.execute(select(HQTask.kind, func.count()).where(HQTask.status == "failed", HQTask.created_at >= since)
                        .group_by(HQTask.kind)).all()
    for kind, n in failed:
        if n >= 3:
            sample = db.scalar(select(HQTask.error).where(HQTask.kind == kind, HQTask.status == "failed").order_by(HQTask.id.desc()).limit(1))
            inc = _open_incident(db, "job_failed", f"jobs:{kind}", f"HQ job '{kind}' failed {n}× in 1h",
                                 {"count": n, "sample_error": sample})
            if inc:
                opened.append(inc)
    for st in db.scalars(select(ServiceState).where(ServiceState.status == "error")):
        inc = _open_incident(db, "job_failed", f"service:{st.name}", f"Service {st.name} is in error",
                             {"error": st.last_error, "since": str(st.last_error_at)})
        if inc:
            opened.append(inc)
    return opened


async def run_auditor(db: Session, client: BinanceClient, notify_owner=None) -> dict:
    with runtime.task(db, "auditor", "audit", "Checking outcomes, NO TRADE calls and data-feed health") as t:
        xp = credit_outcomes(db)
        audits = await audit_no_trades(db, client)
        incidents = health_check(db, client)
        t.result = {"summary": f"{len(incidents)} new incident(s); NO TRADE audits {audits}; outcome XP {xp}",
                    "incidents": [i.id for i in incidents]}
    if incidents and notify_owner:
        lines = "\n".join(f"• {i.title}" for i in incidents)
        await notify_owner(f"🚨 <b>CRYPTONEX HQ — critical alert</b>\n{lines}\nThe Engineer has been asked to investigate.")
    return {"incidents": [i.id for i in incidents], "audits": audits, "xp": xp}
