"""Weekly Library session (Librarian/Coach).

Each agent's real outcomes and logs → lessons → a new skills version (inactive until the owner approves).
Agents may submit proposals into loop C (they go through the same backtest gate). Produces the weekly
Library report and, with a GitHub token, a draft PR mirroring skills/, knowledge/, reports/ and PROVENANCE.md.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...models import Signal, utcnow
from .. import approvals, guard, llm, runtime, versions
from ..github import GitHub, GitHubError
from ..models import HQAgent, HQBacktestRun, HQDecision, HQKnowledge, HQProposal, HQReport, HQTask, HQXPEvent
from ..registry import LEVEL_THRESHOLDS, load_defs
from ..reports import local_day, md_to_telegram, save_report
from ..notify import notify_owner

log = logging.getLogger("cryptonex.hq.library")


def agent_week(db: Session, agent_id: str, since) -> dict:
    d = load_defs()[agent_id]
    f: dict = {
        "tasks_done": db.scalar(select(func.count()).select_from(HQTask).where(HQTask.agent_id == agent_id, HQTask.status == "done", HQTask.created_at >= since)) or 0,
        "tasks_failed": db.scalar(select(func.count()).select_from(HQTask).where(HQTask.agent_id == agent_id, HQTask.status == "failed", HQTask.created_at >= since)) or 0,
        "xp_week": db.scalar(select(func.coalesce(func.sum(HQXPEvent.xp), 0)).where(HQXPEvent.agent_id == agent_id, HQXPEvent.at >= since)) or 0,
        "failure_samples": [t.error[:160] for t in db.scalars(select(HQTask).where(HQTask.agent_id == agent_id, HQTask.status == "failed",
                                                                                   HQTask.created_at >= since).limit(3)) if t.error],
    }
    if d.all_strategies:
        outs = db.execute(select(Signal.theoretical_outcome, func.count()).where(Signal.strategy_id.in_(d.all_strategies), Signal.created_at >= since)
                          .group_by(Signal.theoretical_outcome)).all()
        f["signal_outcomes"] = {str(k): v for k, v in outs}
        rej = db.execute(select(HQDecision.reasons).where(HQDecision.stage == "strategy", HQDecision.created_at >= since).limit(200)).all()
        mine = [r for (rs,) in rej for r in rs if r.split(":", 1)[0] in d.all_strategies]
        top: dict[str, int] = {}
        for r in mine:
            key = r.split(":", 1)[1].strip()[:80]
            top[key] = top.get(key, 0) + 1
        f["top_reject_reasons"] = sorted(top.items(), key=lambda x: -x[1])[:3]
        gates = {}
        for sid in d.all_strategies:
            run = db.scalar(select(HQBacktestRun).where(HQBacktestRun.subject.startswith(f"strategy:{sid}@"))
                            .order_by(HQBacktestRun.id.desc()).limit(1))
            if run:
                gates[sid] = {"passed": run.passed, "trades": run.result.get("summary", {}).get("trades"),
                              "expectancy_pct": run.result.get("summary", {}).get("expectancy_pct")}
        if gates:
            f["gates"] = gates
            f["latest_gate"] = gates.get(d.strategy) or next(iter(gates.values()))
    if agent_id in ("risk", "publisher", "manager"):
        aud = db.execute(select(HQDecision.audit_result, func.count()).where(HQDecision.stage == agent_id, HQDecision.created_at >= since,
                                                                            HQDecision.audit_result.is_not(None)).group_by(HQDecision.audit_result)).all()
        f["no_trade_audits"] = {str(k): v for k, v in aud}
    return f


def deterministic_lessons(agent_id: str, f: dict) -> list[str]:
    L = []
    if f.get("tasks_failed"):
        L.append(f"{f['tasks_failed']} task(s) failed this week — check: {f['failure_samples'][0] if f['failure_samples'] else 'see logs'}")
    a = f.get("no_trade_audits") or {}
    if a.get("avoided_loss"):
        L.append(f"My NO TRADE calls avoided {a['avoided_loss']} loss(es): keep the same discipline.")
    if a.get("missed_win"):
        L.append(f"{a['missed_win']} rejected setup(s) later reached target — review whether the rule was too strict (needs backtest).")
    if f.get("top_reject_reasons"):
        r, n = f["top_reject_reasons"][0]
        L.append(f"Most common reason I passed on a market: '{r}' ({n}×).")
    g = f.get("latest_gate")
    if g and not g["passed"]:
        L.append(f"My latest walk-forward gate failed ({g['trades']} trades) — no edge demonstrated yet; do not loosen rules without evidence.")
    so = f.get("signal_outcomes") or {}
    if so.get("TARGET_HIT") or so.get("STOP_LOSS_HIT"):
        L.append(f"Outcomes this week: TP {so.get('TARGET_HIT', 0)}, SL {so.get('STOP_LOSS_HIT', 0)}.")
    return L or ["Quiet week: no outcomes or failures to learn from yet."]


async def run_library(db: Session, mirror: bool = True) -> HQReport:
    now = utcnow()
    since = now - timedelta(days=7)
    defs = load_defs()
    version_ids: list[int] = []
    sections: list[str] = []
    with runtime.task(db, "librarian", "library", "Weekly Library session: lessons, skills, level-up plans") as t:
        for aid, d in defs.items():
            f = agent_week(db, aid, since)
            lessons = deterministic_lessons(aid, f)
            text, used = await llm.write_text(db, "librarian", f"Write 2-4 short lessons for the agent '{d.name['en']}' "
                                              "(bullets starting with '- ') and one line 'Plan:' for next week, using only the facts.",
                                              {"agent": aid, "facts": f, "lessons_so_far": lessons}, "library_lessons",
                                              "\n".join(f"- {x}" for x in lessons), 500)
            if guard.scan(text):
                text = "\n".join(f"- {x}" for x in lessons)
            row = db.get(HQAgent, aid) or HQAgent(id=aid, level=1, xp=0)
            need = LEVEL_THRESHOLDS[row.level] - row.xp if row.level < 10 else 0
            plan = f"Next level: {need} XP needed — earned only from verified results." if need else "Max level."
            prev = (versions.active_data(db, f"skills:{aid}") or {}).get("text", f"# {d.name['en']} — skills\n")
            new = (prev + f"\n\n## Week of {local_day(now)}\n{text}\n").strip()[-6000:]
            v = versions.create_candidate(db, f"skills:{aid}", {"text": new}, "librarian", note=f"week {local_day(now)}")
            version_ids.append(v.id)
            sections.append(f"## {d.name['en']}\n{text}\n- {plan}")
        db.commit()
        proposed = await _strategy_proposal(db, since)
        # GitHub finds of the week and their gate results
        finds = list(db.scalars(select(HQKnowledge).where(HQKnowledge.created_at >= since)))
        props = list(db.scalars(select(HQProposal).where(HQProposal.created_at >= since)))
        gh_lines = [f"- {k.source_url} ({k.license}, {k.license_class}) → {k.status}" for k in finds] or ["- none this week"]
        gh_lines += [f"- Proposal #{p.id} [{p.kind}] {p.title}: {p.status}" for p in props]
        a = approvals.request(db, "skills", f"Library: skills updates for {len(version_ids)} agents ({local_day(now)})",
                              "Weekly lessons written from verified outcomes. Approve to make them part of each agent's skills.",
                              {"version_ids": version_ids}, ["librarian"], ref=f"library:{local_day(now)}")
        body = "\n".join([f"# Weekly Library report — {local_day(now)}", "",
                          "## GitHub finds and the backtest gate", *gh_lines, "",
                          "## What each agent learned and how it plans to level up", *sections, "",
                          f"_Skills updates are waiting in 'Needs you' (approval #{a.id}); nothing changes until you approve._"])
        rep = save_report(db, "weekly", f"Weekly Library report {local_day(now)}", body,
                          {"approval_id": a.id, "finds": len(finds), "proposals": len(props), "library_proposal_id": proposed}, "librarian")
        t.result = {"summary": f"lessons for {len(version_ids)} agents; approval #{a.id}"}
    if await notify_owner(md_to_telegram(body)):
        rep.sent_telegram = True
        db.commit()
    if mirror:
        await mirror_to_repo(db)
    return rep


async def _strategy_proposal(db: Session, since) -> int | None:
    """At most ONE parameter proposal per week (budget + overfitting control). It must pass the same gate as GitHub ideas."""
    from ...market.binance import get_client
    from ..strategies.builtin import BUILTINS
    from .upgrade import ParamProposal, process_proposal

    if not llm.budget_status(db)["available"] and not llm._client_factory:
        return None
    candidates = [(aid, d) for aid, d in load_defs().items() if d.strategy in BUILTINS]
    for aid, d in candidates:
        f = agent_week(db, aid, since)
        if not f.get("latest_gate") or f["latest_gate"]["passed"]:
            continue
        data = await llm.complete_json(db, "librarian", "A strategy failed its walk-forward gate. Propose ONE small parameter "
                                       f"change as JSON {{strategy_id, params}} for strategy '{d.strategy}' with current params "
                                       f"{BUILTINS[d.strategy].default_params}, or {{}} if nothing is justified.\n"
                                       + llm.facts_block(f), "library_proposal", 400)
        if not data or not data.get("params"):
            continue
        try:
            pp = ParamProposal.model_validate({"strategy_id": d.strategy, "params": data["params"]})
        except Exception:
            continue
        if set(pp.params) - set(BUILTINS[d.strategy].default_params):
            continue
        prop = HQProposal(agent_id=aid, kind="parameter", title=f"Library: tune {d.strategy} {pp.params}", spec=pp.model_dump())
        db.add(prop)
        db.commit()
        await process_proposal(db, get_client(), prop)
        return prop.id
    return None


async def mirror_to_repo(db: Session, gh: GitHub | None = None) -> str | None:
    """Draft PR on agent/library-<date> mirroring skills/, knowledge/, reports/ and PROVENANCE.md (never merged by the app)."""
    if not get_settings().github_token and gh is None:
        return None
    from .upgrade import provenance_md

    gh = gh or GitHub()
    day = local_day()
    branch = f"agent/library-{day}"
    files: dict[str, str] = {"PROVENANCE.md": provenance_md(db)}
    for v in versions.active_by_prefix(db, "skills:"):
        files[f"skills/{v.key.split(':', 1)[1]}.md"] = v.data.get("text", "")
    for k in db.scalars(select(HQKnowledge)):
        files[f"knowledge/{k.slug}.md"] = k.body_md
    for r in db.scalars(select(HQReport).where(HQReport.created_at >= utcnow() - timedelta(days=7))):
        files[f"reports/{r.day}/{r.kind}-{r.id}.md"] = r.body_md
    try:
        await gh.create_branch(branch)
        for path, content in files.items():
            sha = None
            try:
                sha = (await gh.file(path, branch))[1]
            except GitHubError:
                pass
            await gh.put_file(branch, path, content, f"library: sync {path}", sha)
        pr = await gh.open_draft_pr(branch, f"[agent] Library sync {day}", "Weekly mirror of skills, knowledge, reports and "
                                    "PROVENANCE.md from the HQ database. Review and merge if you want them in the repo.")
        return pr.get("html_url")
    except GitHubError as e:
        log.warning("library mirror failed: %s", e)
        return None
