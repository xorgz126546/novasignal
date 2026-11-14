"""Reports: per-agent daily reports, the 08:00 morning brief, Telegram formatting.

All numbers come from the database. The LLM may only write the short Manager intro, verified by check_numbers.
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Signal, UserTrade, utcnow
from . import bus, llm, runtime
from .models import (HQApproval, HQBacktestRun, HQChecklistItem, HQDecision, HQDraft, HQEvent, HQIncident,
                     HQKnowledge, HQReport, HQTask, HQXPEvent)
from .notify import notify_owner
from .registry import load_defs

DISCLAIMER_AR = "تنبيه: هذه تحليلات وليست نصيحة مالية، ولا يوجد ربح مضمون. أنت من يقرر وينفّذ."


def tz() -> ZoneInfo:
    return ZoneInfo(get_settings().hq_timezone)


def local_day(now: datetime | None = None) -> str:
    return (now or utcnow()).astimezone(tz()).strftime("%Y-%m-%d")


def md_to_telegram(md: str) -> str:
    out = []
    for line in md.splitlines():
        esc = html.escape(line)
        if line.startswith("#"):
            out.append(f"<b>{html.escape(line.lstrip('#').strip())}</b>")
        else:
            out.append(re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc))
    return "\n".join(out)


def save_report(db: Session, kind: str, title: str, body: str, data: dict, agent_id: str | None = None,
                day: str | None = None) -> HQReport:
    r = HQReport(day=day or local_day(), kind=kind, agent_id=agent_id, title=title, body_md=body, data=data)
    db.add(r)
    db.commit()
    bus.publish(db, "report", agent_id or "manager", report_id=r.id, kind=kind, title=title)
    return r


# ---------------- per-agent daily reports ----------------
def agent_numbers(db: Session, agent_id: str, since: datetime) -> dict:
    tasks = db.execute(select(HQTask.status, func.count()).where(HQTask.agent_id == agent_id, HQTask.created_at >= since)
                       .group_by(HQTask.status)).all()
    n = {"tasks": dict(tasks), "xp_gained": db.scalar(select(func.coalesce(func.sum(HQXPEvent.xp), 0)).where(
        HQXPEvent.agent_id == agent_id, HQXPEvent.at >= since)) or 0}
    d = load_defs()[agent_id]
    if d.strategy:
        n["signals_published"] = db.scalar(select(func.count()).select_from(Signal).where(
            Signal.source == "hq", Signal.strategy_id == d.strategy, Signal.created_at >= since)) or 0
        n["tp_hits_all_time"] = db.scalar(select(func.count()).select_from(Signal).where(
            Signal.strategy_id == d.strategy, Signal.theoretical_outcome == "TARGET_HIT")) or 0
        n["sl_hits_all_time"] = db.scalar(select(func.count()).select_from(Signal).where(
            Signal.strategy_id == d.strategy, Signal.theoretical_outcome == "STOP_LOSS_HIT")) or 0
    if agent_id == "risk":
        n["vetoes"] = db.scalar(select(func.count()).select_from(HQDecision).where(
            HQDecision.stage == "risk", HQDecision.created_at >= since)) or 0
        n["avoided_losses_all_time"] = db.scalar(select(func.count()).select_from(HQDecision).where(
            HQDecision.stage == "risk", HQDecision.audit_result == "avoided_loss")) or 0
    if agent_id == "publisher":
        n["published"] = db.scalar(select(func.count()).select_from(Signal).where(
            Signal.source == "hq", Signal.created_at >= since)) or 0
        n["no_trade_cycles"] = db.scalar(select(func.count()).select_from(HQDecision).where(
            HQDecision.symbol == "*", HQDecision.created_at >= since)) or 0
    if agent_id == "backtester":
        rows = db.execute(select(HQBacktestRun.passed, func.count()).where(HQBacktestRun.created_at >= since)
                          .group_by(HQBacktestRun.passed)).all()
        n["gate_runs"] = {("passed" if k else "failed"): v for k, v in rows}
    if agent_id == "auditor":
        n["incidents_opened"] = db.scalar(select(func.count()).select_from(HQIncident).where(HQIncident.created_at >= since)) or 0
        n["incidents_open_now"] = db.scalar(select(func.count()).select_from(HQIncident).where(HQIncident.status != "resolved")) or 0
    if agent_id == "scout":
        n["knowledge_notes"] = db.scalar(select(func.count()).select_from(HQKnowledge).where(HQKnowledge.created_at >= since)) or 0
    if agent_id == "content":
        n["drafts"] = db.scalar(select(func.count()).select_from(HQDraft).where(HQDraft.created_at >= since)) or 0
    if agent_id == "engineer":
        n["fix_prs_open"] = db.scalar(select(func.count()).select_from(HQIncident).where(HQIncident.status == "fix_proposed")) or 0
    return n


def agent_daily_report(db: Session, agent_id: str, now: datetime | None = None) -> HQReport:
    now = now or utcnow()
    since = now - timedelta(hours=24)
    d = load_defs()[agent_id]
    nums = agent_numbers(db, agent_id, since)
    problems = [t.error for t in db.scalars(select(HQTask).where(HQTask.agent_id == agent_id, HQTask.status == "failed",
                                                                 HQTask.created_at >= since).limit(5)) if t.error]
    needs = [a.title for a in db.scalars(select(HQApproval).where(HQApproval.status == "pending")) if agent_id in (a.agents or [])]
    done = [t.title for t in db.scalars(select(HQTask).where(HQTask.agent_id == agent_id, HQTask.status == "done",
                                                             HQTask.created_at >= since).order_by(HQTask.id.desc()).limit(5))]
    solutions = []
    if problems:
        solutions.append("Auditor tracks these errors; repeated failures open an incident for the Engineer.")
    body = "\n".join([
        f"# {d.name['en']} — daily report ({local_day(now)})", "",
        "## What I did", *([f"- {t}" for t in done] or ["- nothing scheduled ran"]), "",
        "## Numbers (from the database)", *[f"- {k}: {v}" for k, v in nums.items()], "",
        "## Problems", *([f"- {p[:300]}" for p in problems] or ["- none"]), "",
        "## Proposed solutions", *([f"- {s}" for s in solutions] or ["- none needed"]), "",
        "## What I need from the owner", *([f"- {n}" for n in needs] or ["- nothing"]),
    ])
    return save_report(db, "agent_daily", f"{d.name['en']} — daily report", body, {"numbers": nums}, agent_id, local_day(now))


# ---------------- checklist ----------------
def add_checklist(db: Session, text: str, ref: str, source: str = "manager") -> None:
    if db.scalar(select(HQChecklistItem).where(HQChecklistItem.ref == ref)):
        return
    db.add(HQChecklistItem(text=text, ref=ref, source=source))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()


def refresh_checklist(db: Session) -> None:
    """Things only the owner can do, generated from real state (deduplicated by ref)."""
    s = get_settings()
    for a in db.scalars(select(HQApproval).where(HQApproval.status == "pending")):
        add_checklist(db, f"Decide: {a.title}", f"approval:{a.id}")
    for inc in db.scalars(select(HQIncident).where(HQIncident.status == "fix_proposed", HQIncident.pr_url.is_not(None))):
        add_checklist(db, f"Review & merge fix PR for: {inc.title} ({inc.pr_url})", f"pr:{inc.id}")
    open_sigs = list(db.scalars(select(Signal).where(Signal.status == "ACTIVE")))
    reported = {t.signal_id for t in db.scalars(select(UserTrade))}
    for sg in open_sigs:
        if sg.id not in reported:
            add_checklist(db, f"Report ENTERED or SKIPPED for signal #{sg.id} {sg.symbol}", f"report:{sg.id}")
    if not s.anthropic_api_key:
        add_checklist(db, "Add ANTHROPIC_API_KEY in Render → Environment so agents can write and learn", "setup:anthropic")
    if not s.telegram_chat_id:
        add_checklist(db, "Add TELEGRAM_CHAT_ID in Render → Environment to receive the brief in Telegram", "setup:chat_id")
    if not s.github_token:
        add_checklist(db, "Add a fine-grained GITHUB_TOKEN (this repo only) for the Scout and Engineer", "setup:github")
    # auto-complete items whose condition is gone
    for item in db.scalars(select(HQChecklistItem).where(HQChecklistItem.done.is_(False))):
        r = item.ref or ""
        gone = (r.startswith("approval:") and (db.get(HQApproval, int(r.split(":")[1])) or HQApproval(status="x")).status != "pending") or \
               (r == "setup:anthropic" and s.anthropic_api_key) or (r == "setup:chat_id" and s.telegram_chat_id) or \
               (r == "setup:github" and s.github_token) or \
               (r.startswith("report:") and int(r.split(":")[1]) in reported)
        if gone:
            item.done, item.done_at = True, utcnow()
    db.commit()


# ---------------- morning brief ----------------
def brief_facts(db: Session, now: datetime) -> dict:
    since = now - timedelta(hours=24)
    sigs = list(db.scalars(select(Signal).where(Signal.created_at >= since)))
    resolved = list(db.scalars(select(Signal).where(Signal.theoretical_closed_at >= since)))
    no_trade_cycles = db.scalar(select(func.count()).select_from(HQDecision).where(
        HQDecision.symbol == "*", HQDecision.created_at >= since)) or 0
    lvl = [e for e in db.scalars(select(HQEvent).where(HQEvent.type == "level_up", HQEvent.at >= since))]
    defs = load_defs()
    trend = db.scalar(select(HQReport).where(HQReport.kind == "trends").order_by(HQReport.id.desc()).limit(1))
    return {
        "checklist": [c.text for c in db.scalars(select(HQChecklistItem).where(HQChecklistItem.done.is_(False))
                                                 .order_by(HQChecklistItem.id).limit(12))],
        "needs_you": [{"id": a.id, "title": a.title, "kind": a.kind} for a in db.scalars(
            select(HQApproval).where(HQApproval.status == "pending").order_by(HQApproval.id).limit(12))],
        "what_broke": [{"title": i.title, "status": i.status, "fix": i.diagnosis[:200] or "Engineer investigating",
                        "pr": i.pr_url} for i in db.scalars(select(HQIncident).where(HQIncident.status != "resolved").limit(10))],
        "level_ups": [{"agent": defs[e.agent_id].name["en"] if e.agent_id in defs else e.agent_id,
                       "level": e.data.get("level"), "why": e.data.get("reason", "")} for e in lvl],
        "signals_24h": len(sigs), "hq_signals_24h": sum(1 for s in sigs if s.source == "hq"),
        "resolved_24h": {k: sum(1 for s in resolved if s.theoretical_outcome == k) for k in ("TARGET_HIT", "STOP_LOSS_HIT", "EXPIRED")},
        "no_trade_cycles_24h": no_trade_cycles,
        "top_trends": (trend.data.get("top", [])[:5] if trend else []),
        "llm_budget": llm.budget_status(db),
    }


def brief_markdown(f: dict, day: str, intro: str) -> str:
    r = f["resolved_24h"]
    L = [f"# CRYPTONEX HQ — موجز الصباح / Morning brief ({day})", "", intro, "",
         "## ✅ دورك اليوم / Your part today", *([f"- [ ] {c}" for c in f["checklist"]] or ["- لا شيء / nothing"]), "",
         "## 🟡 ينتظر قرارك / Needs you", *([f"- #{a['id']} {a['title']}" for a in f["needs_you"]] or ["- لا شيء / nothing"]), "",
         "## 🔴 ما الذي تعطل / What broke",
         *([f"- {w['title']} → {w['fix']}" + (f" (PR: {w['pr']})" if w["pr"] else "") for w in f["what_broke"]] or ["- لا شيء / nothing"]), "",
         "## ⭐ ترقيات / Level-ups", *([f"- {x['agent']} → Lv{x['level']} ({x['why']})" for x in f["level_ups"]] or ["- لا شيء / none"]), "",
         "## 📈 أمس / Yesterday",
         f"- إشارات جديدة: {f['signals_24h']} (منها HQ: {f['hq_signals_24h']}) • دورات NO TRADE: {f['no_trade_cycles_24h']}",
         f"- نتائج مغلقة: هدف {r['TARGET_HIT']} • وقف {r['STOP_LOSS_HIT']} • انتهت {r['EXPIRED']}",
         f"- New signals {f['signals_24h']} (HQ {f['hq_signals_24h']}) • TP {r['TARGET_HIT']} / SL {r['STOP_LOSS_HIT']} / expired {r['EXPIRED']}", ""]
    if f["top_trends"]:
        L += ["## 🔥 الترندات / Top trends", *[f"- {t}" for t in f["top_trends"]], ""]
    L += [f"_{DISCLAIMER_AR}_"]
    return "\n".join(L)


async def build_brief(db: Session, send: bool = True, now: datetime | None = None) -> HQReport:
    now = now or utcnow()
    with runtime.task(db, "manager", "brief", "Writing the morning brief") as t:
        refresh_checklist(db)
        f = brief_facts(db, now)
        fallback = (f"صباح الخير. لديك {len(f['needs_you'])} قرار(ات) بانتظارك و{len(f['what_broke'])} مشكلة مفتوحة. "
                    f"صدرت {f['signals_24h']} إشارة خلال 24 ساعة.")
        intro, _ = await llm.write_text(db, "manager", "Write a warm 2-sentence Arabic opening for the owner's morning "
                                        "brief: what needs them first. No promises, no new numbers.", f, "brief", fallback, 300)
        body = brief_markdown(f, local_day(now), intro)
        rep = save_report(db, "brief", f"Morning brief {local_day(now)}", body, f, "manager", local_day(now))
        t.result = {"summary": f"brief #{rep.id}: {len(f['needs_you'])} decisions, {len(f['what_broke'])} issues"}
    if send and await notify_owner(md_to_telegram(body)):
        rep.sent_telegram = True
        db.commit()
    return rep


async def write_all_agent_reports(db: Session, now: datetime | None = None) -> list[HQReport]:
    out = []
    for aid in load_defs():
        if aid != "manager":
            out.append(agent_daily_report(db, aid, now))
    return out
