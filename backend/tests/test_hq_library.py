"""Weekly Library: lessons from verified data, skills versions gated by one approval, weekly report, repo mirror PR."""
import json

from app.hq import approvals, versions
from app.hq.github import GitHub
from app.hq.loops import library
from app.hq.models import HQApproval, HQDecision, HQReport
from app.models import utcnow
from test_hq_upgrade_repair import gh_transport


async def test_library_creates_gated_skills_versions_and_report(db):
    db.add(HQDecision(cycle_ref="r", symbol="X", decision="NO_TRADE", stage="risk", reasons=["veto"], audited=True,
                      audit_result="avoided_loss", levels={}))
    db.commit()
    rep = await library.run_library(db, mirror=False)
    assert rep.kind == "weekly" and "What each agent learned" in rep.body_md and "GitHub finds" in rep.body_md
    a = db.get(HQApproval, rep.data["approval_id"])
    assert a.kind == "skills" and len(a.payload["version_ids"]) == 15
    assert versions.active_data(db, "skills:risk") is None  # nothing live before approval
    approvals.approve(db, a.id, "user:1")
    risk_skills = versions.active_data(db, "skills:risk")["text"]
    assert "avoided 1 loss" in risk_skills
    from app.hq.llm import system_prompt
    assert "Your skills file" in system_prompt(db, "risk")  # approved lessons now feed the agent's prompt
    rep2 = await library.run_library(db, mirror=False)  # next week builds on the approved skills
    assert db.get(HQApproval, rep2.data["approval_id"]).id != a.id or rep2.id != rep.id


async def test_library_mirror_opens_draft_pr_on_agent_branch(db):
    t, log = gh_transport()
    await library.run_library(db, mirror=False)
    url = await library.mirror_to_repo(db, GitHub(token="x", transport=t))
    assert url and url.endswith("/pull/7")
    puts = [p for m, p, _ in log if m == "PUT"]
    assert any(p.endswith("/PROVENANCE.md") for p in puts) and any("/reports/" in p for p in puts)
    pr = json.loads(next(c for m, p, c in log if m == "POST" and p.endswith("/pulls")))
    assert pr["draft"] is True and pr["head"].startswith("agent/library-")


async def test_library_strategy_proposal_goes_through_gate(db, monkeypatch):
    from app.hq import llm
    from app.hq.loops import upgrade
    from app.hq.models import HQBacktestRun, HQProposal
    from test_hq_upgrade_repair import FakeLLM, fake_gate
    db.add(HQBacktestRun(subject="strategy:trend_pullback@weekly", passed=False,
                         result={"summary": {"trades": 11, "expectancy_pct": -0.4}}))
    db.commit()
    monkeypatch.setattr(upgrade, "run_gate", fake_gate(False))
    llm.set_client_factory(lambda: FakeLLM({"strategy_id": "trend_pullback", "params": {"rsi_min": 42}}))
    try:
        await library.run_library(db, mirror=False)
    finally:
        llm.set_client_factory(None)
    p = db.query(HQProposal).filter_by(kind="parameter").one()
    assert p.agent_id == "strat_pullback" and p.status == "failed"  # gated, never reached the owner
    assert not any(a.payload.get("proposal_id") == p.id for a in db.query(HQApproval).all())
