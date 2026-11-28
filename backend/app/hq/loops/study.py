"""Study hall — "whoever finishes their work goes to learn a new skill on GitHub" (owner's rule).

Every 20 minutes the agent that is idle, has no job due in the next 15 minutes and studied least recently takes a
study session on topics of ITS OWN job (risk → position sizing, structure → market structure, …):
1. GitHub search (public, active, non-archived); everything read is UNTRUSTED DATA — never executed or installed.
2. A knowledge note (URL, license, commit, techniques seen) and a line in the agent's study log.
3. Trading agents turn a technique into OUR OWN rule and the Backtester tests it on real data; only passed or
   clearly-labelled "promising" ideas reach the owner on Telegram (✅/❌). With an Anthropic key the agent may also
   propose a short lesson for its own prompt — also only after the owner approves.
4. The weekly Library session folds each agent's study log into its skills file (owner approval again).
Studying earns no XP by itself: XP only comes from verified results (gate passed, approval, TP hit…).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...market.binance import BinanceClient, get_client
from ...models import AppSetting, utcnow
from .. import bus, guard, idea_templates, llm, runtime
from ..github import GitHub, GitHubError
from ..models import HQAgent, HQKnowledge, HQProposal
from ..registry import load_defs
from .upgrade import Distillation, distill_prompt, process_proposal, proposal_from, slugify

log = logging.getLogger("cryptonex.hq.study")
STATE_KEY = "study_state"
MIN_GAP = timedelta(hours=4)
RETEST_AFTER = timedelta(days=30)

# Short GitHub queries per job (GitHub search matches name / description / topics).
STUDY_TOPICS: dict[str, list[str]] = {
    "manager": ["trading plan", "portfolio management crypto", "trading journal"],
    "scanner": ["crypto screener", "market scanner", "volume scanner"],
    "structure": ["market structure", "support resistance", "smart money concepts", "price action"],
    "strat_breakout": ["breakout strategy", "donchian", "squeeze momentum", "supertrend"],
    "strat_pullback": ["trend following", "ema strategy", "ichimoku", "golden cross"],
    "strat_sweep": ["mean reversion", "rsi divergence", "stochastic rsi", "liquidity sweep"],
    "risk": ["position sizing", "risk management trading", "kelly criterion", "drawdown"],
    "backtester": ["walk forward", "backtesting", "monte carlo trading", "overfitting"],
    "publisher": ["trading signals", "signal scoring", "telegram trading bot"],
    "auditor": ["data quality", "monitoring alerting", "anomaly detection timeseries"],
    "engineer": ["fastapi", "python testing", "asyncio"],
    "scout": ["algorithmic trading", "quant trading", "technical indicators python"],
    "trend_hunter": ["crypto momentum", "relative strength", "volume spike", "crypto pump detection"],
    "content": ["price alert", "breakout alert", "vwap strategy", "keltner channel"],
    "librarian": ["trading journal", "trade review", "knowledge base"],
}
# Agents whose studies may become trading rules tested by the Backtester.
RULE_AGENTS = {"strat_breakout", "strat_pullback", "strat_sweep", "trend_hunter", "content", "structure", "scanner", "scout"}


def _state(db: Session) -> tuple[AppSetting | None, dict]:
    row = db.get(AppSetting, STATE_KEY)
    return row, dict(row.value) if row else {}


def _save(db: Session, row: AppSetting | None, st: dict) -> None:
    if row:
        row.value = st
    else:
        db.add(AppSetting(key=STATE_KEY, value=st))
    db.commit()


def pick_student(db: Session, now: datetime | None = None) -> str | None:
    """The idle agent with no job due soon who studied least recently (and not in the last 4 hours)."""
    from ..scheduler import agent_duty

    now = now or utcnow()
    _, st = _state(db)
    duty = agent_duty(db, now)
    rows = {a.id: a for a in db.query(HQAgent).all()}
    best, best_last = None, None
    for aid in load_defs():
        if aid not in STUDY_TOPICS or (rows.get(aid) and rows[aid].status == "working"):
            continue
        nxt = (duty.get(aid) or {}).get("next_at")
        if nxt is not None and nxt - now < timedelta(minutes=15):
            continue  # has work coming: finish the job first
        last = datetime.fromisoformat(st[aid]["last"]) if st.get(aid, {}).get("last") else None
        if last and now - last < MIN_GAP:
            continue
        if best is None or (last or datetime.min.replace(tzinfo=now.tzinfo)) < (best_last or datetime.min.replace(tzinfo=now.tzinfo)):
            best, best_last = aid, last
    return best


DOMAIN = re.compile(r"trad|crypto|bitcoin|binance|quant|stock|market|financ|backtest|strateg|indicator|forex|invest|"
                    r"portfolio|signal|ohlc|candle|ta-lib|technical", re.I)
ENGINEERING = {"engineer", "auditor"}  # their skills are software skills, not trading


def _relevant(item: dict, agent_id: str = "") -> bool:
    """Only study projects that are about markets/trading (search results by stars can be anything)."""
    text = " ".join([item.get("full_name", ""), item.get("description") or "", " ".join(item.get("topics") or [])])
    return bool(DOMAIN.search(text))


def _recently_tested(db: Session, slug: str, now: datetime) -> bool:
    return any((p.spec or {}).get("slug") == slug
               for p in db.scalars(select(HQProposal).where(HQProposal.created_at >= now - RETEST_AFTER)))


async def study_session(db: Session, agent_id: str, gh: GitHub | None = None, client: BinanceClient | None = None,
                        now: datetime | None = None) -> dict:
    gh = gh or GitHub()
    client = client or get_client()
    now = now or utcnow()
    row, st = _state(db)
    me = dict(st.get(agent_id, {}))
    topics = STUDY_TOPICS[agent_id]
    topic = topics[int(me.get("i", 0)) % len(topics)]
    me.update(i=int(me.get("i", 0)) + 1, last=now.isoformat())
    st[agent_id] = me
    _save(db, row, st)
    name = load_defs()[agent_id].name["en"]
    out: dict = {"agent": agent_id, "topic": topic, "repo": None, "techniques": [], "tested": []}
    with runtime.task(db, agent_id, "study", f"📚 Free time → studying on GitHub: {topic}") as t:
        since = (now - timedelta(days=365)).strftime("%Y-%m-%d")
        it = None
        for q in (topic, f"{topic} trading"):
            try:
                items = await gh.search_repos(f"{guard.sanitize(q, 80)} archived:false pushed:>{since} stars:>10", 10)
            except GitHubError as e:
                t.result = {"summary": f"GitHub unavailable: {e}"}
                return out
            it = next((x for x in items if x.get("full_name") and not x.get("archived") and not x.get("fork") and (agent_id in ENGINEERING or _relevant(x))
                       and not db.scalar(select(HQKnowledge.id).where(HQKnowledge.slug == slugify(x["full_name"])))), None)
            if it:
                break
        if not it:
            t.result = {"summary": f"nothing new on '{topic}' (all top repos already studied)"}
            return out
        full = it["full_name"]
        spdx = ((it.get("license") or {}).get("spdx_id")) or "NONE"
        try:
            sha = await gh.latest_sha(full, it.get("default_branch", "main"))
            readme = await gh.readme(full)
        except GitHubError:
            sha, readme = "", ""
        flags = guard.scan(readme)
        desc = guard.sanitize(it.get("description") or "", 300)
        tids = idea_templates.match(" ".join([full, desc, " ".join(it.get("topics") or []), readme[:30000]]))
        k = HQKnowledge(slug=slugify(full), source_url=f"https://github.com/{full}", license=spdx,
                        license_class=guard.classify_license(spdx), commit_sha=sha, title=f"[{name}] {desc or full}"[:300],
                        idea=("Techniques mentioned: " + ", ".join(tids)) if tids else "Studied for general skill (no rule template matched).",
                        how_to_test="Our own rule template → walk-forward gate" if tids else "Lesson only",
                        injection_flags=flags, status="noted",
                        body_md="\n".join([f"# {full} — studied by {name}", "", f"- Topic: {topic}", f"- License: {spdx}",
                                           f"- Commit: {sha or 'n/a'}", f"- Stars: {it.get('stargazers_count', '?')}",
                                           f"- Techniques seen: {', '.join(tids) or 'none'}",
                                           f"- Injection flags: {', '.join(flags) if flags else 'none'}", "",
                                           "_Ideas only: nothing from the source is executed or installed._"]))
        db.add(k)
        db.commit()
        out.update(repo=full, techniques=tids, stars=it.get("stargazers_count"), license=spdx)
        if agent_id in RULE_AGENTS:
            for tid in tids:
                slug = f"tpl-{tid}"
                if _recently_tested(db, slug, now):
                    continue
                spec = idea_templates.spec_for(tid, full)
                p = HQProposal(agent_id=agent_id, kind="rule_strategy", title=f"{spec.name} (studied by {name} in {full})",
                               spec={"rule_spec": spec.model_dump(), "slug": slug, "source_url": k.source_url, "license": spdx,
                                     "template": tid}, knowledge_id=k.id)
                db.add(p)
                k.status = "proposed"
                db.commit()
                r = await process_proposal(db, client, p, allow_promising=True)
                s = (p.gate or {}).get("summary") or {}
                out["tested"].append({"title": spec.name, "status": r.get("status"), "evidence": r.get("evidence"),
                                      "trades": s.get("trades"), "expectancy_pct": s.get("expectancy_pct")})
                break  # one test per session (CPU + overfitting budget)
        if readme and llm.budget_status(db)["available"]:
            data = await llm.complete_json(db, agent_id, distill_prompt(full, spdx, k.license_class, readme) +
                                           f"\n\nYou are {name}. Prefer a 'prompt' lesson for YOUR OWN job (agent_id={agent_id}).",
                                           "study", 1200)
            try:
                dist = Distillation.model_validate(data) if data else None
            except Exception:
                dist = None
            if dist and dist.useful and dist.proposal_type != "none":
                if dist.prompt:
                    dist.prompt.agent_id = agent_id  # a lesson can only be for the studying agent itself
                p = proposal_from(db, k, dist)
                if p:
                    p.agent_id = agent_id
                    db.commit()
                    r = await process_proposal(db, client, p, allow_promising=True)
                    out["tested"].append({"title": p.title, "status": r.get("status"), "evidence": r.get("evidence")})
        log_line = (f"{now:%Y-%m-%d} studied {full} ({spdx}) on '{topic}': techniques {', '.join(tids) or 'none'}"
                    + "".join(f"; tested {x['title']} → {x['status']}" for x in out["tested"]))
        me = dict(st.get(agent_id, {}))
        me["log"] = (me.get("log", []) + [log_line])[-20:]
        st[agent_id] = me
        row2, _ = _state(db)
        _save(db, row2, st)
        t.result = {"summary": log_line[:300]}
    bus.publish(db, "study", agent_id, repo=out["repo"], topic=topic, techniques=tids)
    return out


def study_log(db: Session, agent_id: str, since: datetime | None = None) -> list[str]:
    _, st = _state(db)
    lines = (st.get(agent_id) or {}).get("log", [])
    if since:
        lines = [x for x in lines if x[:10] >= since.strftime("%Y-%m-%d")]
    return lines


def digest_md(db: Session, since: datetime) -> str | None:
    defs = load_defs()
    lines = []
    for aid, d in defs.items():
        for x in study_log(db, aid, since):
            lines.append(f"- {d.name['ar']}: {x[11:200]}")
    if not lines:
        return None
    return "\n".join(["# 📚 ما تعلّمه الفريق اليوم في GitHub", "", *lines[-20:], "",
                      "أي فكرة نجحت في الاختبار تصلك برسالة فيها ✅/❌. الكود من GitHub لا يُنفّذ أبدًا. ليست نصيحة مالية."])
