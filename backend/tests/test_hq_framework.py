"""HQ framework: registry, migrations, bus, approval gate + versions/rollback, XP rules, LLM budget & number guard, chat."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from app.hq import approvals, bus, llm, registry, versions, xp
from app.hq.models import HQAgent, HQApproval, HQXPEvent
from app.main import app

PW = "CorrectHorse9battery"


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def admin_headers(c):
    r = c.post("/api/auth/register", json={"email": "owner@example.com", "password": PW, "accept_risk_disclosure": True})
    return {"Authorization": f"Bearer {r.json()['token']}"}, r.json()["token"]


# ---------- registry ----------
def test_registry_has_15_agents_with_prompts_and_tools():
    defs = registry.load_defs()
    assert len(defs) == 15 and "manager" in defs
    assert {d.room for d in defs.values()} >= {"scanner_floor", "strategy_lab", "risk_office", "backtest_lab", "library",
                                               "engineering", "content_studio", "manager_office"}
    for d in defs.values():
        assert d.prompt().startswith("#") and "Never invent a number" in d.prompt()
        assert d.tools
    assert defs["manager"].model_name() == "claude-opus-5-5" and defs["scanner"].model_name() == "claude-haiku-5-5"


def test_ensure_agents_and_tool_unlock_by_level(db):
    registry.ensure_agents(db)
    assert db.query(HQAgent).count() == 15
    assert registry.can_use(db, "trend_hunter", "binance_tickers")
    assert not registry.can_use(db, "trend_hunter", "github_trending")  # needs Lv2
    db.get(HQAgent, "trend_hunter").level = 2
    db.commit()
    assert registry.can_use(db, "trend_hunter", "github_trending")
    assert not registry.can_use(db, "scanner", "open_fix_pr")  # not its tool at all


def test_levels():
    assert registry.level_for_xp(0) == 1 and registry.level_for_xp(49) == 1 and registry.level_for_xp(50) == 2
    assert registry.level_for_xp(10**6) == 10


# ---------- migrations ----------
def test_migrations_add_columns_to_existing_signals_table(tmp_path):
    from app.migrations import run_migrations
    eng = create_engine(f"sqlite:///{tmp_path}/old.db")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE signals (id INTEGER PRIMARY KEY, symbol VARCHAR(32))"))
    applied = run_migrations(eng)
    cols = {c["name"] for c in inspect(eng).get_columns("signals")}
    assert {"source", "strategy_id", "invalidation_price", "risk_pct", "score"} <= cols and len(applied) == 5
    assert run_migrations(eng) == []  # idempotent


# ---------- bus ----------
async def test_bus_persists_and_fans_out(db):
    q = bus.subscribe()
    ev = await asyncio.to_thread(lambda: bus.publish(db, "agent_status", "scanner", status="working", task="x"))
    got = await asyncio.wait_for(q.get(), 2)
    bus.unsubscribe(q)
    assert got["id"] == ev["id"] and got["data"]["status"] == "working"


# ---------- approval gate ----------
def test_approval_gate_activation_and_rollback(db):
    v1 = versions.create_candidate(db, "publisher", {"min_score": 60}, "publisher")
    db.commit()
    assert versions.active_data(db, "publisher") is None  # candidate is inert
    a1 = approvals.request(db, "config", "min score 60", "", {"version_id": v1.id}, ["publisher"])
    assert versions.active_data(db, "publisher") is None  # still inert while pending
    approvals.approve(db, a1.id, "user:1")
    assert versions.active_data(db, "publisher") == {"min_score": 60}
    with pytest.raises(approvals.ApprovalError):
        approvals.approve(db, a1.id, "user:1")  # cannot decide twice
    v2 = versions.create_candidate(db, "publisher", {"min_score": 70}, "publisher")
    db.commit()
    a2 = approvals.request(db, "config", "min score 70", "", {"version_id": v2.id}, ["publisher"])
    approvals.approve(db, a2.id, "user:1")
    assert versions.active_data(db, "publisher") == {"min_score": 70}
    versions.rollback(db, "publisher")
    assert versions.active_data(db, "publisher") == {"min_score": 60}
    # rejected proposals never activate
    v3 = versions.create_candidate(db, "publisher", {"min_score": 5}, "publisher")
    db.commit()
    a3 = approvals.request(db, "config", "min 5", "", {"version_id": v3.id}, ["publisher"])
    approvals.reject(db, a3.id, "user:1", "too low")
    assert versions.active_data(db, "publisher") == {"min_score": 60}


def test_config_approval_requires_candidate_version(db):
    with pytest.raises(approvals.ApprovalError):
        approvals.request(db, "config", "x", "", {"data": {"min_score": 1}}, [])
    with pytest.raises(approvals.ApprovalError):
        approvals.request(db, "deploy_anything", "x", "", {}, [])


def test_approval_endpoints_admin_only(client, db):
    h, _ = admin_headers(client)
    r = client.post("/api/auth/register", json={"email": "u@example.com", "password": PW, "accept_risk_disclosure": True})
    hu = {"Authorization": f"Bearer {r.json()['token']}"}
    v = versions.create_candidate(db, "publisher", {"min_score": 65}, "publisher")
    db.commit()
    a = approvals.request(db, "config", "t", "", {"version_id": v.id}, ["publisher"])
    assert client.post(f"/api/hq/approvals/{a.id}/approve", headers=hu).status_code == 403
    assert client.post(f"/api/hq/approvals/{a.id}/approve", headers=h).status_code == 200
    assert client.post("/api/hq/versions/rollback", headers=h, json={"key": "publisher"}).json()["active_version"] is None


# ---------- XP ----------
def test_xp_only_verified_and_never_double_counted(db):
    assert xp.award(db, "strat_pullback", "signal_tp_hit", "signal:7") == 40
    assert xp.award(db, "strat_pullback", "signal_tp_hit", "signal:7") == 0  # same event again
    with pytest.raises(ValueError):
        xp.award(db, "strat_pullback", "i_feel_great", "self")  # self-reported success is not a source
    assert db.get(HQAgent, "strat_pullback").xp == 40
    xp.award(db, "strat_pullback", "proposal_approved", "approval:1")
    assert db.get(HQAgent, "strat_pullback").level == 2
    assert db.query(HQXPEvent).count() == 2
    assert xp.level_weight(1) == 1.0 and xp.level_weight(10) == pytest.approx(1.09)


def test_approval_awards_xp_to_credited_agents(db):
    v = versions.create_candidate(db, "publisher", {"min_score": 61}, "publisher")
    db.commit()
    a = approvals.request(db, "config", "t", "", {"version_id": v.id}, ["publisher", "backtester"])
    approvals.approve(db, a.id, "user:1")
    assert db.get(HQAgent, "publisher").xp == 50 and db.get(HQAgent, "backtester").xp == 50


# ---------- LLM guard & budget ----------
class FakeLLM:
    def __init__(self, text, inp=1000, out=500):
        self.text, self.inp, self.out = text, inp, out
        self.messages = self
        self.beta = SimpleNamespace(messages=self)

    async def create(self, **kw):
        return SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=self.inp, output_tokens=self.out),
                               content=[SimpleNamespace(type="text", text=self.text)])


def test_check_numbers():
    facts = {"entry": 68924.46, "rr": 2.67, "count": 12}
    assert llm.check_numbers("Entry 68,924.46 with R:R 2.67 across 12 markets", facts) == []
    assert llm.check_numbers("Expect +15% gains", facts) == ["+15"]
    assert llm.check_numbers("two or 3 agents", facts) == []


async def test_write_text_rejects_invented_numbers(db):
    llm.set_client_factory(lambda: FakeLLM("BTC will pump 37% this week"))
    try:
        txt, used = await llm.write_text(db, "manager", "say", {"x": 1}, "t", "FALLBACK")
        assert txt == "FALLBACK" and not used
        llm.set_client_factory(lambda: FakeLLM("All good, 12 markets scanned"))
        txt, used = await llm.write_text(db, "manager", "say", {"markets": 12}, "t", "FALLBACK")
        assert used and "12" in txt
    finally:
        llm.set_client_factory(None)


async def test_budget_cap_falls_back(db, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "hq_llm_daily_budget_usd", 0.001)
    llm.set_client_factory(lambda: FakeLLM("hello", inp=100000, out=50000))
    try:
        # pre-flight: the worst-case cost of an Opus call exceeds the cap → refused before any spend
        assert await llm.complete(db, "manager", "hi", "t") is None
        assert llm.spent_today(db) == 0
        monkeypatch.setattr(get_settings(), "hq_llm_daily_budget_usd", 0.5)
        assert await llm.complete(db, "scanner", "hi", "t") == "hello"  # haiku call fits, gets billed
        spent = llm.spent_today(db)
        assert spent == pytest.approx(llm.cost("claude-haiku-5-5", 100000, 50000))
        monkeypatch.setattr(get_settings(), "hq_llm_daily_budget_usd", spent)
        assert await llm.complete(db, "scanner", "hi", "t") is None  # cap reached → template fallback
    finally:
        llm.set_client_factory(None)


# ---------- chat + SSE auth ----------
def test_manager_chat_without_llm_uses_templates(client):
    h, _ = admin_headers(client)
    r = client.post("/api/hq/chat", headers=h, json={"text": "ما حالة الفريق؟"})
    assert r.status_code == 200 and "الفريق" in r.json()["text"]
    hist = client.get("/api/hq/chat", headers=h).json()
    assert [m["role"] for m in hist] == ["owner", "manager"]
    assert len(client.get("/api/hq/agents", headers=h).json()) == 15
    d = client.get("/api/hq/agents/manager", headers=h).json()
    assert d["tasks"] and d["tasks"][0]["kind"] == "chat"


def test_sse_requires_admin_token(client):
    assert client.get("/api/hq/stream", params={"token": "bad"}).status_code == 401
    r = client.post("/api/auth/register", json={"email": "a@example.com", "password": PW, "accept_risk_disclosure": True})
    r2 = client.post("/api/auth/register", json={"email": "b@example.com", "password": PW, "accept_risk_disclosure": True})
    assert client.get("/api/hq/stream", params={"token": r2.json()["token"]}).status_code == 403
    assert r.json()["user"]["role"] == "admin"
