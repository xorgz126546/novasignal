"""Loop F — Opportunity Research (agent #16, Hadi).

Runs all the time (every few hours, even when HQ_ENABLED=false) looking on public GitHub for techniques that could
bring MORE valid trading opportunities:
1. Searches a rotating list of opportunity topics (indicators, strategies, screeners); public, active, non-archived
   repos only. Everything fetched is UNTRUSTED DATA: never executed, never installed, only regex-matched / LLM-distilled.
2. Writes a knowledge note (URL, license, commit SHA, techniques seen).
3. Maps each technique to OUR OWN rule template (idea_templates.py) — works without any LLM. With an Anthropic key it
   also distils one extra idea through the strict JSON schema.
4. The Backtester replays it on real Binance history with fees (walk-forward, out-of-sample, overfitting penalty).
5. Anything that passes — or is clearly positive but not yet proven — goes to the owner on Telegram with
   ✅/❌ buttons. A new analysis tool is unlocked only by that approval. Failures are reported honestly.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...market.binance import BinanceClient, get_client
from ...market.tools import TOOLS
from ...models import AppSetting, utcnow
from .. import bus, guard, idea_templates, llm, runtime
from ..github import GitHub, GitHubError
from ..models import HQKnowledge, HQProposal
from ..reports import md_to_telegram, save_report
from ..strategies.rules import spec_tools
from .upgrade import Distillation, distill_prompt, process_proposal, proposal_from, slugify

log = logging.getLogger("cryptonex.hq.research")
TOPICS = [  # short queries: GitHub search matches name/description/topics, so long phrases return nothing
    "supertrend", "stochastic rsi", "vwap strategy", "ichimoku", "keltner channel", "cci indicator", "macd strategy",
    "breakout strategy", "crypto momentum", "crypto screener", "freqtrade strategy", "technical indicators python",
    "trading strategies crypto", "mean reversion", "money flow index", "williams r",
]
RETEST_AFTER = timedelta(days=30)
STATE_KEY = "research_state"


def _next_topics(db: Session, n: int = 2) -> list[str]:
    row = db.get(AppSetting, STATE_KEY)
    i = int((row.value or {}).get("i", 0)) if row else 0
    out = [TOPICS[(i + k) % len(TOPICS)] for k in range(n)]
    if row:
        row.value = {"i": i + n}
    else:
        db.add(AppSetting(key=STATE_KEY, value={"i": i + n}))
    db.commit()
    return out


def _recently_tested(db: Session, slug: str, now) -> bool:
    for p in db.scalars(select(HQProposal).where(HQProposal.created_at >= now - RETEST_AFTER)):
        if (p.spec or {}).get("slug") == slug:
            return True
    return False


async def research_run(db: Session, gh: GitHub | None = None, client: BinanceClient | None = None, notify_owner=None,
                       topic: str | None = None, max_repos: int = 3, max_tests: int = 3, universe: list[str] | None = None,
                       now=None) -> dict:
    gh = gh or GitHub()
    client = client or get_client()
    now = now or utcnow()
    since = (now - timedelta(days=365)).strftime("%Y-%m-%d")
    topics = [topic] if topic else _next_topics(db)
    repos, tested = [], []
    for tp in topics:
        with runtime.task(db, "researcher", "research", f"GitHub research: {tp[:80]}") as t:
            try:
                items = await gh.search_repos(f"{guard.sanitize(tp, 120)} archived:false pushed:>{since} stars:>10", 10)
            except GitHubError as e:
                t.result = {"summary": f"GitHub unavailable: {e}"}
                continue
            done = 0
            for it in items:
                if done >= max_repos or len(tested) >= max_tests:
                    break
                full = it.get("full_name", "")
                if not full or it.get("archived") or it.get("fork"):
                    continue
                if db.scalar(select(HQKnowledge.id).where(HQKnowledge.slug == slugify(full))):
                    continue
                spdx = ((it.get("license") or {}).get("spdx_id")) or "NONE"
                try:
                    sha = await gh.latest_sha(full, it.get("default_branch", "main"))
                    readme = await gh.readme(full)
                except GitHubError as e:  # rate limit / access: still learn from the public description + topics
                    log.info("README unavailable for %s: %s", full, e)
                    sha, readme = "", ""
                done += 1
                flags = guard.scan(readme)
                desc = guard.sanitize(it.get("description") or "", 300)
                tids = idea_templates.match(" ".join([full, desc, " ".join(it.get("topics") or []), readme[:30000]]))
                k = HQKnowledge(slug=slugify(full), source_url=f"https://github.com/{full}", license=spdx,
                                license_class=guard.classify_license(spdx), commit_sha=sha, title=desc or full,
                                idea=("Techniques mentioned: " + ", ".join(tids)) if tids else "No known technique matched.",
                                how_to_test="Our own rule template per technique → walk-forward gate", injection_flags=flags,
                                status="noted")
                k.body_md = "\n".join([f"# {full}", "", f"- Source: {k.source_url}", f"- License: {spdx} ({k.license_class})",
                                       f"- Commit: {sha}", f"- Stars: {it.get('stargazers_count', '?')}",
                                       f"- Injection flags in source: {', '.join(flags) if flags else 'none'}",
                                       f"- Techniques seen: {', '.join(tids) or 'none'}", "",
                                       "_Ideas only: we never copy or run code from the source; each technique is re-expressed as "
                                       "our own rule and tested on real data. README claims are not evidence._"])
                db.add(k)
                db.commit()
                repos.append({"repo": full, "stars": it.get("stargazers_count"), "license": spdx, "techniques": tids})
                bus.publish(db, "knowledge", "researcher", knowledge_id=k.id, title=k.title, license=spdx, flags=flags)
                for tid in tids:
                    if len(tested) >= max_tests:
                        break
                    slug = f"tpl-{tid}"
                    if _recently_tested(db, slug, now):
                        continue
                    spec = idea_templates.spec_for(tid, full)
                    p = HQProposal(agent_id="researcher", kind="rule_strategy", title=f"{spec.name} (idea seen in {full})",
                                   spec={"rule_spec": spec.model_dump(), "slug": slug, "source_url": k.source_url, "license": spdx,
                                         "template": tid}, knowledge_id=k.id)
                    db.add(p)
                    k.status = "proposed"
                    db.commit()
                    out = await process_proposal(db, client, p, universe, allow_promising=True)
                    tested.append(_row(p, out, spec_tools(spec), full))
                if len(tested) < max_tests and readme:
                    dist = await _distill(db, full, spdx, k.license_class, readme)
                    if dist:
                        k.idea = guard.sanitize(dist.idea, 1500)
                        db.commit()
                        if dist.useful and dist.proposal_type != "none":
                            p = proposal_from(db, k, dist)
                            if p:
                                p.agent_id = "researcher"
                                p.spec = {**p.spec, "source_url": k.source_url, "license": spdx}
                                db.commit()
                                out = await process_proposal(db, client, p, universe, allow_promising=True)
                                tools = spec_tools(dist.rule_spec) if dist.rule_spec else set()
                                tested.append(_row(p, out, tools, full))
            t.result = {"summary": f"{done} repo(s) studied, {len(tested)} idea(s) tested for '{tp[:50]}'"}
    result = {"topics": topics, "repos": repos, "tested": tested}
    if repos:
        body = summary_md(result)
        save_report(db, "research", f"Opportunity research: {len(repos)} repo(s), {len(tested)} idea(s) tested", body, result,
                    "researcher")
        if notify_owner and tested:
            await notify_owner(md_to_telegram(body))
    return result


async def _distill(db: Session, full: str, spdx: str, lclass: str, readme: str) -> Distillation | None:
    data = await llm.complete_json(db, "researcher", distill_prompt(full, spdx, lclass, readme), "distill", 1500)
    if not data:
        return None
    try:
        return Distillation.model_validate(data)
    except ValidationError as e:
        log.info("distillation rejected for %s: %s", full, e.errors()[:2])
        return None


def _row(p: HQProposal, out: dict, tools: set, repo: str) -> dict:
    s = (p.gate or {}).get("summary") or {}
    return {"proposal_id": p.id, "title": p.title, "repo": repo, "status": out.get("status"), "evidence": out.get("evidence"),
            "approval_id": out.get("approval_id"), "tools": sorted(tools), "trades": s.get("trades"),
            "expectancy_pct": s.get("expectancy_pct"), "win_rate": s.get("win_rate")}


def summary_md(r: dict) -> str:
    lines = ["# 🔎 هادي (باحث الفرص) — تقرير البحث", "",
             f"المواضيع: {', '.join(r['topics'])}", f"مستودعات GitHub المدروسة: {len(r['repos'])}", ""]
    for x in r["tested"]:
        tools = (" · 🔧 أداة جديدة: " + ", ".join(TOOLS[t]["ar"] for t in x["tools"] if t in TOOLS)) if x["tools"] else ""
        nums = (f"{x['trades']} صفقة على بيانات حقيقية، {x['expectancy_pct']}%/صفقة بعد العمولات"
                if x.get("trades") is not None else "لا توجد صفقات كافية")
        if x["status"] == "awaiting_approval":
            tag = "✅ نجحت — تنتظر موافقتك" if x.get("evidence") == "passed" else "🟡 واعدة لكن غير مثبتة — تنتظر قرارك"
        else:
            tag = "❌ لم تنجح في الاختبار — لن تُفعَّل"
        lines.append(f"- {x['title']}: {tag} ({nums}){tools}")
    if not r["tested"]:
        lines.append("- لم أجد تقنية جديدة لم نختبرها مؤخرًا.")
    lines += ["", "أي فكرة تنتظر موافقتك وصلتك برسالة فيها زر ✅ موافق / ❌ رفض. أو اكتب /pending.",
              "_الكود من GitHub لا يُنفّذ أبدًا؛ نعيد كتابة الفكرة بقواعدنا ونختبرها. ليست نصيحة مالية._"]
    return "\n".join(lines)
