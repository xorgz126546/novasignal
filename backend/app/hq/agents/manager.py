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
    (re.compile(r"\b(learn from|lessons? from|study (the )?(win|result))\w*|تعلم من|تعلّم من|يتعلم|يتعلمو|تتعلم", re.I), "lessons"),
    (re.compile(r"\b(best|pick|recommend\w*)\b|أفضل|توصية|افضل", re.I), "pick"),
    (re.compile(r"\b(tournament|leaderboard|compare strategies)\b|بطولة|ترتيب الاستراتيجيات|قارن", re.I), "tournament"),
    (re.compile(r"^(learn about|research|تعلم عن|تعلّم عن|ابحث عن)\s+(.+)$", re.I), "learn"),
    (re.compile(r"\b(research|new tools?|github)\b|باحث|ابحث|أدوات|ادوات|اداة|أداة", re.I), "research"),
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
    from ..models import HQTask
    from ..scheduler import agent_duty

    agents = {a.id: a for a in db.query(HQAgent).all()}
    defs = load_defs()
    duty = agent_duty(db)
    last = {}
    for d in defs.values():
        t = db.scalar(select(HQTask).where(HQTask.agent_id == d.id, HQTask.kind != "chat").order_by(HQTask.id.desc()).limit(1))
        if t:
            last[d.id] = {"title": t.title[:90], "at": t.finished_at or t.created_at, "status": t.status,
                          "summary": str((t.result or {}).get("summary", ""))[:90]}
    return {
        "agents": [{"id": d.id, "name": d.name["en"], "name_ar": d.name.get("ar", d.name["en"]),
                    "status": agents[d.id].status if d.id in agents else "idle",
                    "task": agents[d.id].current_task if d.id in agents else "",
                    "level": agents[d.id].level if d.id in agents else 1,
                    "last": last.get(d.id), "next_at": (duty.get(d.id) or {}).get("next_at")} for d in defs.values()],
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
    if intent == "pick":
        from ..loops.daily_pick import run_daily_pick
        from ...market.binance import get_client
        from ...telegram.bot import notify
        from ..notify import notify_owner

        _spawn(lambda d: run_daily_pick(d, get_client(), notify, notify_owner), "pick")
        return "pick"
    if intent == "tournament":
        from ..loops.daily_pick import run_tournament
        from ...market.binance import get_client
        from ..notify import notify_owner

        _spawn(lambda d: run_tournament(d, get_client(), notify_owner), "tournament")
        return "tournament"
    if intent == "brief":
        from ..reports import build_brief

        _spawn(lambda d: build_brief(d, send=False), "brief")
        return "brief"
    if intent == "lessons":
        from ..loops.learn import learn_from_outcomes
        from ..notify import notify_owner

        _spawn(lambda d: learn_from_outcomes(d, notify_owner=notify_owner), "lessons")
        return "lessons"
    if intent == "learn":
        from ..loops.research import research_run
        from ..notify import notify_owner

        _spawn(lambda d: research_run(d, topic=arg, notify_owner=notify_owner), "learn")
        return "learn"
    if intent == "research":
        from ..loops.research import research_run
        from ..notify import notify_owner

        _spawn(lambda d: research_run(d, notify_owner=notify_owner), "research")
        return "research"
    if intent == "growth":
        from ...config import get_settings
        from ..loops.growth import run_growth

        if not get_settings().hq_growth:
            return "growth_paused"

        _spawn(run_growth, "growth")
        return "growth"
    if intent == "library":
        from ..loops.library import run_library

        _spawn(run_library, "library")
        return "library"
    return None


CONFIRM = {
    "growth_paused": ("Content is paused (your decision). Tariq hunts coins moving up with real volume and Celine watches trigger "
                      "levels to re-check the moment a setup can form. Set HQ_GROWTH=true to bring content back.",
                      "المحتوى متوقف (قرارك). طارق يصطاد العملات الصاعدة بحجم حقيقي، وسيلين تراقب مستويات الدخول لتعيد الفحص لحظة "
                      "ظهور فرصة. لإرجاع المحتوى: HQ_GROWTH=true."),
    "lessons": ("Lina (Librarian) is studying every closed signal: she rebuilds the market fingerprint at signal time, Badr tests a "
                "'pattern twin' on real history, and anything that passes comes to you for approval. One win alone proves nothing.",
                "لينا (أمينة المكتبة) تدرس كل توصية أُغلقت: تعيد بناء بصمة السوق لحظة التوصية، وبدر يختبر «نمطًا مشابهًا» على البيانات "
                "التاريخية الحقيقية، وما ينجح يصلك للموافقة. صفقة رابحة واحدة لا تثبت شيئًا وحدها."),
    "pick": ("All strategy agents are scanning the market now. I'll publish the single best valid setup of the day, or NO TRADE with a watchlist.",
             "كل وكلاء الاستراتيجيات يفحصون السوق الآن. سأنشر أفضل توصية صالحة لليوم، أو NO TRADE مع قائمة مراقبة."),
    "tournament": ("Badr is running the strategy tournament on real history (a few minutes). The leaderboard will appear in Reports.",
                   "بدر يشغّل بطولة الاستراتيجيات على البيانات التاريخية الحقيقية (بضع دقائق). الترتيب سيظهر في التقارير."),
    "scan": ("I've asked Sami (Scanner), Nora (Structure), the three strategy agents and Rami (Risk) to run a full cycle on live Binance data. Watch the office; you'll get a signal or a NO TRADE with reasons.",
             "طلبت من سامي (الماسح) ونورا (الهيكل) ووكلاء الاستراتيجيات الثلاثة ورامي (المخاطر) تشغيل دورة كاملة على بيانات Binance الحقيقية. تابع المكتب؛ ستصلك إشارة أو NO TRADE مع الأسباب."),
    "brief": ("I'm writing your brief now. It will appear in Reports in a moment.",
              "أكتب لك الموجز الآن، سيظهر في التقارير بعد لحظات."),
    "learn": ("Hadi (Opportunity Researcher) is researching that on GitHub now. Every idea becomes our own rule, Badr tests it on real history, and anything worth it reaches you on Telegram with Approve/Reject buttons.",
              "هادي (باحث الفرص) يبحث عن ذلك في GitHub الآن. كل فكرة تتحول إلى قاعدة خاصة بنا، يختبرها بدر على البيانات الحقيقية، وما يستحق يصلك في Telegram مع زر موافق/رفض."),
    "research": ("Hadi (Opportunity Researcher) is searching GitHub for techniques and analysis tools that could bring more valid opportunities. Results come to you on Telegram; nothing goes live without your ✅.",
                 "هادي (باحث الفرص) يبحث الآن في GitHub عن تقنيات وأدوات تحليل تجلب فرصًا صالحة أكثر. النتائج تصلك في Telegram، ولا شيء يُفعَّل بدون ✅ منك."),
    "growth": ("Tariq is collecting trends and Celine will prepare drafts for your approval. Nothing is posted automatically.",
               "طارق يجمع الترندات وسيلين ستجهّز مسودات لموافقتك. لا يُنشر شيء تلقائيًا."),
    "library": ("Lina is running the Library session: lessons and skill updates will come to you for approval.",
                "لينا تدير جلسة المكتبة: الدروس وتحديثات المهارات ستصلك للموافقة."),
}


def _ago(dt, ar: bool) -> str:
    from ...models import utcnow

    if dt is None:
        return "—"
    m = int(abs((utcnow() - dt).total_seconds()) // 60)
    v = f"{m} د" if m < 90 else f"{m // 60} س" if m < 60 * 48 else f"{m // 1440} يوم"
    w = f"{m}m" if m < 90 else f"{m // 60}h" if m < 60 * 48 else f"{m // 1440}d"
    return v if ar else w


def _roster(facts: dict, ar: bool) -> list[str]:
    out = []
    for a in facts["agents"]:
        if a["id"] == "manager":
            continue
        name = a["name_ar"] if ar else a["name"]
        if a["status"] == "working":
            out.append(f"🟢 {name}: {'يعمل الآن' if ar else 'working now'} — {a['task'][:70]}")
            continue
        icon = "🔴" if a["status"] == "blocked" else "⚪"
        last = a.get("last")
        did = (f"{'آخر عمل قبل' if ar else 'last'} {_ago(last['at'], ar)}: {(last['summary'] or last['title'])[:60]}" if last
               else ("لم يعمل بعد" if ar else "not run yet"))
        nxt = a.get("next_at")
        from ...models import utcnow

        soon = nxt is not None and (nxt - utcnow()).total_seconds() < 90
        when = ((" → يبدأ الآن" if ar else " → starting now") if soon else
                f" → {'التالي بعد' if ar else 'next in'} {_ago(nxt, ar)}" if nxt else
                (" → عند الطلب" if ar else " → on demand"))
        out.append(f"{icon} {name}: {did}{when}")
    return out


def _template_reply(intent: str, facts: dict, ar: bool) -> str:
    if intent in CONFIRM:
        return CONFIRM[intent][1 if ar else 0]
    working = [a for a in facts["agents"] if a["status"] == "working" and a["id"] != "manager"]  # Maya is answering you
    if ar:
        lines = [f"الفريق: {len(facts['agents'])} وكيلًا. يعمل في هذه اللحظة: {len(working)} (غير مايا التي ترد عليك).",
                 "كل وكيل يشتغل في دوراته (ثوانٍ أو دقائق) ثم يرتاح حتى موعده التالي:", *_roster(facts, True), "",
                 f"ينتظر قرارك: {facts['pending_approvals']} • مشاكل مفتوحة: {facts['open_incidents']} • إشارات HQ: {facts['hq_signals_total']}."]
        if not facts["llm_budget"]["available"]:
            lines.append("الذكاء الاصطناعي غير مفعّل أو الميزانية انتهت، لذلك أرد بقوالب ثابتة.")
        lines.append("الأوامر: افحص السوق • أفضل توصية • ابحث • تعلم من النتائج • الموجز • تقارير • الحالة")
    else:
        lines = [f"Team: {len(facts['agents'])} agents. Working this second: {len(working)} (besides Maya answering you).",
                 "Each agent works in short runs on its schedule, then rests until the next one:", *_roster(facts, False), "",
                 f"Needs you: {facts['pending_approvals']} • open incidents: {facts['open_incidents']} • HQ signals: {facts['hq_signals_total']}."]
        if not facts["llm_budget"]["available"]:
            lines.append("LLM is off or today's budget is used up, so I answer with fixed templates.")
        lines.append("Commands: scan • best pick • research • learn from results • brief • reports • status")
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
            if _delegate(db, intent, arg) == "growth_paused":
                intent = "growth_paused"
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
