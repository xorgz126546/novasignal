"""Manager (Chief of Staff): the owner's only contact. Routes chat to agents and answers from DB facts."""
from __future__ import annotations

import asyncio
import logging
import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...models import Signal
from .. import bus, llm, runtime
from ..models import HQAgent, HQApproval, HQChatMessage, HQIncident
from ..registry import load_defs

log = logging.getLogger("cryptonex.hq.manager")

# (pattern, intent). Arabic + English. Deterministic routing first; the LLM only words the reply.
INTENTS = [
    (re.compile(r"^(learn about|research|تعلم عن|تعلّم عن|ابحث عن)\s+(.+)$", re.I), "learn"),
    (re.compile(r"\b(scan|analy[sz]e market)\b|افحص|حلل السوق|فحص", re.I), "scan"),
    (re.compile(r"\b(brief|morning)\b|موجز|ملخص الصباح", re.I), "brief"),
    (re.compile(r"\b(report|reports)\b|تقرير|تقارير", re.I), "report"),
    (re.compile(r"\b(trends?|content|post)\b|ترند|محتوى|منشور", re.I), "growth"),
    (re.compile(r"\b(library|lessons|weekly)\b|المكتبة|دروس", re.I), "library"),
    (re.compile(r"\b(status|team|agents)\b|الحالة|الفريق|الوكلاء", re.I), "status"),
    (re.compile(r"\b(needs you|approvals?|pending)\b|ينتظر|موافق", re.I), "approvals"),
]
_background: set[asyncio.Task] = set()


def detect_intent(text: str) -> tuple[str, str]:
    t = text.strip()
    for rx, intent in INTENTS:
        m = rx.search(t)
        if m:
            return intent, (m.group(2).strip() if intent == "learn" else "")
    return "chat", ""


def is_arabic(text: str) -> bool:
    return bool(re.search(r"[؀-ۿ]", text))


def status_facts(db: Session) -> dict:
    agents = {a.id: a for a in db.query(HQAgent).all()}
    defs = load_defs()
    return {
        "agents": [{"id": d.id, "name": d.name["en"], "status": agents[d.id].status if d.id in agents else "idle",
                    "task": agents[d.id].current_task if d.id in agents else "",
                    "level": agents[d.id].level if d.id in agents else 1} for d in defs.values()],
        "pending_approvals": db.scalar(select(func.count()).select_from(HQApproval).where(HQApproval.status == "pending")) or 0,
        "open_incidents": db.scalar(select(func.count()).select_from(HQIncident).where(HQIncident.status != "resolved")) or 0,
        "hq_signals_total": db.scalar(select(func.count()).select_from(Signal).where(Signal.source == "hq")) or 0,
        "llm_budget": llm.budget_status(db),
    }


def _spawn(coro_factory, label: str) -> None:
    """Run delegated work in the background with its own DB session; the chat replies immediately."""
    from ...db import SessionLocal

    async def runner():
        with SessionLocal() as db2:
            try:
                await coro_factory(db2)
            except Exception:
                log.exception("delegated job %s failed", label)

    t = asyncio.get_running_loop().create_task(runner())
    _background.add(t)
    t.add_done_callback(_background.discard)


def _delegate(db: Session, intent: str, arg: str) -> str | None:
    """Start the job for an intent; returns a short deterministic confirmation (or None for chat)."""
    if intent == "scan":
        from ..loops.trading import run_hq_cycle
        from ...market.binance import get_client

        _spawn(lambda d: run_hq_cycle(d, get_client()), "scan")
        return "scan"
    if intent == "brief":
        from ..reports import build_brief

        _spawn(lambda d: build_brief(d, send=False), "brief")
        return "brief"
    if intent == "learn":
        from ..loops.upgrade import scout_run

        _spawn(lambda d: scout_run(d, topic=arg), "learn")
        return "learn"
    if intent == "growth":
        from ..loops.growth import run_growth

        _spawn(run_growth, "growth")
        return "growth"
    if intent == "library":
        from ..loops.library import run_library

        _spawn(run_library, "library")
        return "library"
    return None


CONFIRM = {
    "scan": ("I've asked Sami (Scanner), Nora (Structure), the three strategy agents and Rami (Risk) to run a full cycle on live Binance data. Watch the office; you'll get a signal or a NO TRADE with reasons.",
             "طلبت من سامي (الماسح) ونورا (الهيكل) ووكلاء الاستراتيجيات الثلاثة ورامي (المخاطر) تشغيل دورة كاملة على بيانات Binance الحقيقية. تابع المكتب؛ ستصلك إشارة أو NO TRADE مع الأسباب."),
    "brief": ("I'm writing your brief now. It will appear in Reports in a moment.",
              "أكتب لك الموجز الآن، سيظهر في التقارير بعد لحظات."),
    "learn": ("Sora (GitHub Scout) is researching that now. Anything useful becomes a knowledge note; any proposal must pass Badr's backtest gate and your approval.",
              "سورا (كشافة GitHub) تبحث الآن. أي فكرة مفيدة تصبح ملاحظة معرفة، وأي اقتراح يجب أن ينجح في اختبار بدر ثم موافقتك."),
    "growth": ("Tariq is collecting trends and Celine will prepare drafts for your approval. Nothing is posted automatically.",
               "طارق يجمع الترندات وسيلين ستجهّز مسودات لموافقتك. لا يُنشر شيء تلقائيًا."),
    "library": ("Lina is running the Library session: lessons and skill updates will come to you for approval.",
                "لينا تدير جلسة المكتبة: الدروس وتحديثات المهارات ستصلك للموافقة."),
}


def _template_reply(intent: str, facts: dict, ar: bool) -> str:
    if intent in CONFIRM:
        return CONFIRM[intent][1 if ar else 0]
    working = [a for a in facts["agents"] if a["status"] == "working"]
    if ar:
        lines = [f"الفريق: {len(facts['agents'])} وكيلًا، يعمل الآن {len(working)}.",
                 f"ينتظر قرارك: {facts['pending_approvals']} • مشاكل مفتوحة: {facts['open_incidents']} • إشارات HQ: {facts['hq_signals_total']}."]
        if not facts["llm_budget"]["available"]:
            lines.append("الذكاء الاصطناعي غير مفعّل أو الميزانية انتهت، لذلك أرد بقوالب ثابتة.")
        lines.append("الأوامر: افحص السوق • الموجز • تقارير • تعلم عن <موضوع> • ترندات • المكتبة • الحالة")
    else:
        lines = [f"Team: {len(facts['agents'])} agents, {len(working)} working now.",
                 f"Needs you: {facts['pending_approvals']} • open incidents: {facts['open_incidents']} • HQ signals: {facts['hq_signals_total']}."]
        if not facts["llm_budget"]["available"]:
            lines.append("LLM is off or today's budget is used up, so I answer with fixed templates.")
        lines.append("Commands: scan • brief • reports • learn about <topic> • trends • library • status")
    return "\n".join(lines)


async def handle_chat(db: Session, text: str) -> HQChatMessage:
    text = text.strip()[:4000]
    ar = is_arabic(text)
    db.add(HQChatMessage(role="owner", text=text))
    db.commit()
    bus.publish(db, "chat", "manager", role="owner", text=text[:500])
    intent, arg = detect_intent(text)
    with runtime.task(db, "manager", "chat", f"Owner: {text[:120]}", handover=False) as t:
        try:
            _delegate(db, intent, arg)
        except ImportError:  # loop not installed in this build
            intent = "chat"
        facts = status_facts(db)
        fallback = _template_reply(intent, facts, ar)
        if intent in CONFIRM:
            reply = fallback
        else:
            instruction = (f"The owner wrote: <owner>{text}</owner>\nReply as the Manager in "
                           f"{'Arabic' if ar else 'English'}, in at most 6 short lines, using only the facts. "
                           f"If they ask for something an agent can do, name the command they can send.")
            reply, _ = await llm.write_text(db, "manager", instruction, facts, "chat", fallback, 600)
        t.result = {"intent": intent, "summary": reply[:200]}
    msg = HQChatMessage(role="manager", text=reply, meta={"intent": intent})
    db.add(msg)
    db.commit()
    bus.publish(db, "chat", "manager", role="manager", text=reply[:500])
    return msg
