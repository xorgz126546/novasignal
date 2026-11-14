"""CRYPTONEX HQ API. The HQ is the owner's company → every endpoint requires an admin."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..hq import approvals, bus, llm, registry, versions
from ..hq.models import (HQAgent, HQApproval, HQChatMessage, HQChecklistItem, HQConfigVersion, HQDecision, HQDraft,
                         HQEvent, HQIncident, HQKnowledge, HQProposal, HQReport, HQTask, HQXPEvent)
from ..models import Role, User, utcnow
from ..security import ALGO, audit, rate_limit, require_admin

router = APIRouter(prefix="/api/hq", tags=["hq"])


def _row(o, *fields):
    return {f: getattr(o, f) for f in fields}


@router.get("/agents")
def agents(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    registry.ensure_agents(db)
    rows = {a.id: a for a in db.query(HQAgent).all()}
    return [registry.agent_view(d, rows.get(d.id)) for d in registry.load_defs().values()]


@router.get("/agents/{agent_id}")
def agent_detail(agent_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    defs = registry.load_defs()
    if agent_id not in defs:
        raise HTTPException(404, "unknown agent")
    d = defs[agent_id]
    out = registry.agent_view(d, db.get(HQAgent, agent_id))
    out["tasks"] = [_row(t, "id", "kind", "title", "status", "created_at", "finished_at", "error")
                    for t in db.scalars(select(HQTask).where(HQTask.agent_id == agent_id).order_by(HQTask.id.desc()).limit(20))]
    out["logs"] = [_row(e, "id", "at", "type", "data")
                   for e in db.scalars(select(HQEvent).where(HQEvent.agent_id == agent_id).order_by(HQEvent.id.desc()).limit(40))]
    out["xp_events"] = [_row(x, "at", "source_type", "xp", "note")
                        for x in db.scalars(select(HQXPEvent).where(HQXPEvent.agent_id == agent_id).order_by(HQXPEvent.id.desc()).limit(20))]
    skills = versions.active_data(db, f"skills:{agent_id}")
    out["skills_md"] = (skills or {}).get("text", "")
    rep = db.scalar(select(HQReport).where(HQReport.agent_id == agent_id).order_by(HQReport.id.desc()).limit(1))
    out["latest_report"] = None if not rep else _row(rep, "id", "day", "title", "body_md")
    out["prompt"] = llm.system_prompt(db, agent_id)
    return out


@router.get("/events")
def events(limit: int = Query(100, le=500), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [{"id": e.id, "at": e.at, "type": e.type, "agent_id": e.agent_id, "data": e.data}
            for e in db.scalars(select(HQEvent).order_by(HQEvent.id.desc()).limit(limit))]


def _user_from_token(token: str, db: Session) -> User:
    try:
        uid = int(jwt.decode(token, get_settings().secret_key, algorithms=[ALGO])["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(401, "invalid token")
    u = db.get(User, uid)
    if not u or not u.is_active or u.role != Role.ADMIN.value:
        raise HTTPException(403, "admin required")
    return u


@router.get("/stream")
async def stream(request: Request, token: str = Query(...), db: Session = Depends(get_db)):
    """Server-Sent Events (EventSource can't send headers, so the JWT comes as a query parameter)."""
    _user_from_token(token, db)
    db.close()
    q = bus.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"id: {ev['id']}\nevent: hq\ndata: {json.dumps(ev, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.get("/chat")
def chat_history(limit: int = Query(50, le=200), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = list(db.scalars(select(HQChatMessage).order_by(HQChatMessage.id.desc()).limit(limit)))
    return [_row(m, "id", "at", "role", "text", "meta") for m in reversed(rows)]


@router.post("/chat", dependencies=[Depends(rate_limit(30, 60, "hqchat"))])
async def chat(body: ChatIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    from ..hq.agents.manager import handle_chat

    registry.ensure_agents(db)
    msg = await handle_chat(db, body.text)
    return _row(msg, "id", "at", "role", "text", "meta")


@router.get("/budget")
def budget(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return llm.budget_status(db)


# ---------------- Needs you (approvals) ----------------
@router.get("/approvals")
def list_approvals(status: str = "pending", admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    q = select(HQApproval).order_by(HQApproval.id.desc()).limit(200)
    if status != "all":
        q = q.where(HQApproval.status == status)
    return [_row(a, "id", "created_at", "kind", "title", "summary", "payload", "status", "decided_at", "agents")
            for a in db.scalars(q)]


class DecisionIn(BaseModel):
    reason: str = Field("", max_length=1000)


@router.post("/approvals/{aid}/approve")
def approve(aid: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    try:
        a = approvals.approve(db, aid, f"user:{admin.id}")
    except approvals.ApprovalError as e:
        raise HTTPException(400, str(e))
    audit(db, f"user:{admin.id}", "hq_approve", approval_id=aid, kind=a.kind, title=a.title)
    return {"ok": True, "status": a.status}


@router.post("/approvals/{aid}/reject")
def reject(aid: int, body: DecisionIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    try:
        a = approvals.reject(db, aid, f"user:{admin.id}", body.reason)
    except approvals.ApprovalError as e:
        raise HTTPException(400, str(e))
    audit(db, f"user:{admin.id}", "hq_reject", approval_id=aid, kind=a.kind)
    return {"ok": True, "status": a.status}


# ---------------- versions / rollback ----------------
@router.get("/versions")
def list_versions(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [_row(v, "id", "key", "version", "active", "created_at", "created_by", "approval_id", "note")
            for v in db.scalars(select(HQConfigVersion).order_by(HQConfigVersion.key, HQConfigVersion.version.desc()))]


class RollbackIn(BaseModel):
    key: str = Field(max_length=96)


@router.post("/versions/rollback")
def rollback(body: RollbackIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    try:
        v = versions.rollback(db, body.key)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit(db, f"user:{admin.id}", "hq_rollback", key=body.key, now_active=v.version if v else None)
    bus.publish(db, "rollback", "manager", key=body.key, active_version=v.version if v else None)
    return {"ok": True, "active_version": v.version if v else None}


# ---------------- reports / checklist / knowledge / drafts / incidents / decisions ----------------
@router.get("/reports")
def reports(kind: str | None = None, limit: int = Query(50, le=300), admin: User = Depends(require_admin),
            db: Session = Depends(get_db)):
    q = select(HQReport).order_by(HQReport.id.desc()).limit(limit)
    if kind:
        q = q.where(HQReport.kind == kind)
    return [_row(r, "id", "created_at", "day", "kind", "agent_id", "title", "body_md", "sent_telegram") for r in db.scalars(q)]


@router.get("/reports/{rid}.md", response_class=PlainTextResponse)
def report_md(rid: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    r = db.get(HQReport, rid)
    if not r:
        raise HTTPException(404)
    return PlainTextResponse(r.body_md, media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="{r.day}-{r.kind}-{r.id}.md"'})


@router.get("/checklist")
def checklist(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [_row(c, "id", "created_at", "text", "source", "done", "done_at")
            for c in db.scalars(select(HQChecklistItem).order_by(HQChecklistItem.done, HQChecklistItem.id.desc()).limit(100))]


class CheckIn(BaseModel):
    done: bool


@router.patch("/checklist/{cid}")
def check(cid: int, body: CheckIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    c = db.get(HQChecklistItem, cid)
    if not c:
        raise HTTPException(404)
    c.done, c.done_at = body.done, utcnow() if body.done else None
    db.commit()
    bus.publish(db, "checklist", "manager", id=cid, done=body.done)
    return {"ok": True}


@router.get("/knowledge")
def knowledge(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [_row(k, "id", "created_at", "slug", "source_url", "license", "license_class", "commit_sha", "title", "idea",
                 "how_to_test", "injection_flags", "status", "body_md")
            for k in db.scalars(select(HQKnowledge).order_by(HQKnowledge.id.desc()).limit(200))]


@router.get("/proposals")
def proposals(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [_row(p, "id", "created_at", "agent_id", "kind", "title", "spec", "status", "gate", "approval_id", "knowledge_id")
            for p in db.scalars(select(HQProposal).order_by(HQProposal.id.desc()).limit(200))]


@router.get("/drafts")
def drafts(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [_row(d, "id", "created_at", "kind", "lang", "title", "body", "facts", "status")
            for d in db.scalars(select(HQDraft).order_by(HQDraft.id.desc()).limit(100))]


@router.get("/incidents")
def incidents(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [_row(i, "id", "created_at", "kind", "title", "details", "status", "diagnosis", "pr_url", "branch", "resolved_at")
            for i in db.scalars(select(HQIncident).order_by(HQIncident.id.desc()).limit(100))]


@router.get("/decisions")
def decisions(limit: int = Query(100, le=500), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [_row(d, "id", "created_at", "cycle_ref", "symbol", "strategy_id", "decision", "stage", "reasons", "levels",
                 "score", "signal_id", "audit_result")
            for d in db.scalars(select(HQDecision).order_by(HQDecision.id.desc()).limit(limit))]


# ---------------- loops (manual triggers; the scheduler runs them when HQ_ENABLED=true) ----------------
@router.post("/cycle")
async def run_cycle(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    from ..hq.loops.trading import run_hq_cycle
    from ..market.binance import get_client
    from ..telegram.bot import notify

    audit(db, f"user:{admin.id}", "hq_cycle")
    return await run_hq_cycle(db, get_client(), notify)


@router.post("/audit")
async def run_audit(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    from ..hq.agents.auditor import run_auditor
    from ..hq.notify import notify_owner
    from ..market.binance import get_client

    return await run_auditor(db, get_client(), notify_owner)


@router.post("/backtest/{strategy_id}")
async def run_backtest(strategy_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    from ..hq.agents.backtester import run_gate
    from ..hq.strategies.registry import get
    from ..market.binance import get_client

    act = get(strategy_id, db)
    if act is None:
        raise HTTPException(404, "unknown strategy")
    run = await run_gate(db, get_client(), act.strategy, act.params, f"strategy:{strategy_id}@manual")
    return {"id": run.id, "passed": run.passed, "result": run.result}


@router.get("/backtests")
def backtests(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    from ..hq.models import HQBacktestRun

    return [{"id": r.id, "created_at": r.created_at, "subject": r.subject, "passed": r.passed,
             "summary": r.result.get("summary"), "reasons": r.result.get("reasons"), "proposal_id": r.proposal_id}
            for r in db.scalars(select(HQBacktestRun).order_by(HQBacktestRun.id.desc()).limit(100))]


@router.post("/brief")
async def make_brief(send: bool = False, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    from ..hq.reports import build_brief

    r = await build_brief(db, send=send)
    return {"id": r.id, "title": r.title, "body_md": r.body_md, "sent_telegram": r.sent_telegram}


@router.post("/reports/agents")
async def make_agent_reports(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    from ..hq.reports import write_all_agent_reports

    return [{"id": r.id, "agent_id": r.agent_id, "title": r.title} for r in await write_all_agent_reports(db)]


@router.get("/status")
def hq_status(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    from ..hq.scheduler import _state, jobs

    s = get_settings()
    return {"hq_enabled": s.hq_enabled, "timezone": s.hq_timezone, "jobs": [{"name": n, "schedule": sc, "last_run": _state(db).get(n)}
                                                                         for n, sc in jobs()],
            "llm": llm.budget_status(db), "telegram_owner_chat": bool(s.telegram_chat_id),
            "github_token": bool(s.github_token), "live_subscribers": bus.subscriber_count()}


# ---------------- upgrade loop / repair loop ----------------
class LearnIn(BaseModel):
    topic: str = Field(min_length=3, max_length=120)


@router.post("/scout")
async def scout(body: LearnIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    from ..hq.loops.upgrade import scout_run

    return {"knowledge_ids": await scout_run(db, topic=body.topic)}


@router.get("/provenance.md", response_class=PlainTextResponse)
def provenance(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    from ..hq.loops.upgrade import provenance_md

    return PlainTextResponse(provenance_md(db), media_type="text/markdown")


class InjectIn(BaseModel):
    calls: int = Field(20, ge=1, le=200)


@router.post("/inject-failure")
async def inject_failure(body: InjectIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Owner-only fault injection: the next N Binance requests fail, so the real detection → repair path runs."""
    from ..hq.agents.auditor import run_auditor
    from ..hq.loops.repair import handle_new_incidents
    from ..hq.notify import notify_owner
    from ..market.binance import MarketDataError, get_client

    client = get_client()
    client.inject_failures = body.calls
    audit(db, f"user:{admin.id}", "hq_inject_failure", calls=body.calls)
    try:
        await client.price("BTCUSDT")
    except MarketDataError:
        pass
    r = await run_auditor(db, client, notify_owner)
    await handle_new_incidents(db, r["incidents"])
    client.inject_failures = 0
    return {"incidents": r["incidents"], "note": "Injection cleared; the incident auto-resolves on the next healthy check."}


@router.post("/growth")
async def growth(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    from ..hq.loops.growth import run_content, run_trends

    rep = await run_trends(db)
    drafts = await run_content(db)
    return {"trends_report": rep.id, "drafts": [d.id for d in drafts]}


@router.get("/drafts/{did}/card.svg")
def draft_card(did: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    from fastapi.responses import Response

    d = db.get(HQDraft, did)
    if not d or d.kind != "signal_card":
        raise HTTPException(404)
    return Response(d.body, media_type="image/svg+xml", headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})
