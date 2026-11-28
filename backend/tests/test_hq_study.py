"""Study hall: idle agents with no work due study a skill of their own job on GitHub (safe, gated, owner-approved)."""
from datetime import timedelta

from app.config import get_settings
from app.hq import approvals, scheduler
from app.hq.loops import study, upgrade
from app.hq.loops.library import agent_week, deterministic_lessons
from app.hq.models import HQKnowledge, HQProposal
from app.hq.registry import ensure_agents
from app.models import utcnow
from test_hq_research import gh
from test_hq_trading import FakeMarket
from test_hq_upgrade_repair import fake_gate


def test_every_agent_has_study_topics_except_the_full_time_researcher():
    from app.hq.registry import load_defs
    assert set(load_defs()) - set(study.STUDY_TOPICS) == {"researcher"}


def test_only_agents_with_no_work_due_soon_study(db, monkeypatch):
    ensure_agents(db)
    monkeypatch.setattr(get_settings(), "hq_enabled", True)
    # trading agents have a 30-min cycle due now → they keep working; Sora (weekly) is free
    student = study.pick_student(db)
    assert student is not None and student not in {"scanner", "structure", "strat_breakout", "risk"}


async def test_study_session_writes_knowledge_tests_one_idea_and_respects_the_gap(db, monkeypatch):
    ensure_agents(db)
    monkeypatch.setattr(upgrade, "run_gate", fake_gate(False))
    monkeypatch.setattr(approvals, "_push_to_owner", lambda aid: None)
    out = await study.study_session(db, "strat_breakout", gh(), FakeMarket("up"))
    assert out["repo"] == "someone/bot0" and "supertrend_flip" in out["techniques"]
    k = db.query(HQKnowledge).one()
    assert k.title.startswith("[Bilal") and k.injection_flags  # injection in README noticed, never obeyed
    p = db.query(HQProposal).one()
    assert p.agent_id == "strat_breakout" and len(out["tested"]) == 1  # one test per session
    assert study.study_log(db, "strat_breakout")
    assert study.pick_student(db) != "strat_breakout"  # just studied: 4-hour gap
    f = agent_week(db, "strat_breakout", utcnow() - timedelta(days=7))
    assert any("Studied" in x for x in deterministic_lessons("strat_breakout", f))  # folds into weekly skills


async def test_non_trading_agent_learns_without_creating_trading_rules(db, monkeypatch):
    ensure_agents(db)
    out = await study.study_session(db, "risk", gh(items=2), FakeMarket("up"))
    assert out["repo"] and out["tested"] == [] and db.query(HQProposal).count() == 0
    assert "ما تعلّمه الفريق" in study.digest_md(db, utcnow() - timedelta(days=1))


def test_study_jobs_run_even_with_hq_off(monkeypatch):
    monkeypatch.setattr(get_settings(), "hq_enabled", False)
    monkeypatch.setattr(get_settings(), "hq_research", True)
    assert {"study_hall", "study_digest"} <= {n for n, _ in scheduler.active_jobs()}
