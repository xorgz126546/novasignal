"""Content paused: Tariq hunts movers (hot list → universe) and Celine watches trigger levels (→ immediate re-check)."""
from app.hq import approvals, scheduler
from app.hq.loops import movers, watcher
from app.hq.models import HQApproval, HQReport
from app.models import utcnow
from conftest import make_candles, uptrend
from test_hq_trading import FakeMarket


class MoverMarket(FakeMarket):
    def __init__(self):
        super().__init__("up")
        now_ms = int(utcnow().timestamp() * 1000)
        vols = [100.0] * 298 + [400.0, 400.0, 400.0]
        self.c["1h"] = make_candles(uptrend(301, drift=0.0005, amp=0.01, period=10), 3_600_000, start_ms=now_ms - 301 * 3_600_000 - 1, vols=vols)

    async def tickers_24h(self):
        return [{"symbol": "TESTUSDT", "lastPrice": "100", "quoteVolume": "90000000", "priceChangePercent": "6",
                 "highPrice": "104", "lowPrice": "95"}]

    async def price(self, sym):
        return 100.0


async def test_movers_hot_list_feeds_the_universe(db):
    hot = await movers.hunt_movers(db, MoverMarket())
    assert [h["symbol"] for h in hot] == ["TESTUSDT"] and hot[0]["vol_rel_1h"] >= 1.2
    assert movers.hot_symbols(db) == ["TESTUSDT"]
    assert await movers.hunt_movers(db, FakeMarket("down")) == []  # falling coin is never "hot"


async def test_watcher_rechecks_once_when_a_trigger_level_is_crossed(db):
    db.add(HQReport(day="2026-11-26", kind="daily_pick", title="t", body_md="",
                    data={"triggers": [{"symbol": "TESTUSDT", "strategy": "donchian_breakout", "level": 99.0, "distance_pct": 1.0},
                                       {"symbol": "TESTUSDT", "strategy": "oversold_bounce", "level": 120.0, "distance_pct": 9.0}]}))
    db.commit()
    calls = []

    async def recheck():
        calls.append(1)
    r = await watcher.watch_triggers(db, MoverMarket(), recheck)
    assert [h["strategy"] for h in r["hits"]] == ["donchian_breakout"] and calls == [1]
    r2 = await watcher.watch_triggers(db, MoverMarket(), recheck)
    assert r2["hits"] == [] and calls == [1]  # cooldown: no repeated re-checks


async def test_content_is_paused_and_never_pings_the_owner(db, monkeypatch):
    sent = []
    monkeypatch.setattr(approvals, "_push_to_owner", lambda aid: sent.append(aid))
    a = approvals.request(db, "content", "Draft: reel", "s", {"draft_id": 1}, ["content"])
    assert sent == []
    assert scheduler.retire_content_approvals() == 1
    db.expire_all()
    assert db.get(HQApproval, a.id).status == "rejected"
    assert {n for n, _ in scheduler.jobs()} & {"trends", "content"} == set()


# ---------------- momentum ignition (catch a move in its first days) ----------------
def _ign_ctx(r3_pct=14.0, vol_mult=2.0):
    from app.hq.features import Context
    now_ms = int(utcnow().timestamp() * 1000)
    flat = [100.0] * 280
    rise = [100.0 * (1 + r3_pct / 100) ** (k / 18) for k in range(1, 19)]
    c4 = make_candles(flat + rise, 14_400_000, start_ms=now_ms - 298 * 14_400_000)
    d = make_candles([100.0] * 40, 86_400_000, start_ms=now_ms - 41 * 86_400_000, vols=[100.0] * 37 + [100.0 * vol_mult] * 3)
    ctx = Context("IGNUSDT", {"4h": c4, "1d": d}, {}, {}, c4[-1].close, 0.0001, 0.001, 5.0, True)
    ctx.extras["res_tfs"] = ()
    return ctx


def test_momentum_ignition_rules_and_single_target():
    from app.hq.profiles import profile_risk
    from app.hq.strategies.base import Reject, Setup
    from app.hq.strategies.builtin import BUILTINS
    from app.trading_config import RiskConfig
    s = BUILTINS["momentum_ignition"]
    risk = profile_risk(RiskConfig(), "momentum")
    r = s.evaluate(_ign_ctx(), s.default_params, risk)
    assert isinstance(r, Setup) and abs(r.stop_pct - 6.0) < 0.1 and 12.0 <= r.move_pct <= 15.0 and r.stop < r.entry < r.take_profit
    assert isinstance(s.evaluate(_ign_ctx(r3_pct=4), s.default_params, risk), Reject)  # no ignition
    assert "without volume" in s.evaluate(_ign_ctx(vol_mult=1.0), s.default_params, risk).reason
    assert "parabolic" in s.evaluate(_ign_ctx(r3_pct=60), s.default_params, risk).reason


async def test_new_strategy_stays_off_until_owner_approves(db, monkeypatch):
    from app.hq.loops import enable
    from app.hq.strategies.registry import active_strategies
    from test_hq_upgrade_repair import fake_gate
    monkeypatch.setattr(enable, "run_gate", fake_gate(False))
    monkeypatch.setattr(approvals, "_push_to_owner", lambda aid: None)
    assert "momentum_ignition" not in {a.strategy.id for a in active_strategies(db)}
    ids = await enable.ensure_enable_requests(db, object())
    assert len(ids) == 1 and await enable.ensure_enable_requests(db, object()) == []  # asked once
    a = db.get(HQApproval, ids[0])
    assert "momentum" in a.title.lower() and "ليست نصيحة مالية" in a.summary
    approvals.approve(db, a.id, "owner")
    assert "momentum_ignition" in {x.strategy.id for x in active_strategies(db)}


async def test_ignition_alert_once_per_move_and_triggers_recheck(db):
    class IgnMarket(MoverMarket):
        async def klines(self, sym, tf, limit=300):
            c = _ign_ctx()
            return c.candles["4h"] if tf == "4h" else c.candles["1d"] if tf == "1d" else self.c[tf]
    msgs, rechecks = [], []

    async def owner(m, markup=None):
        msgs.append(m)

    async def recheck():
        rechecks.append(1)
    hot = await movers.hunt_movers(db, IgnMarket(), notify_owner=owner, on_ignition=recheck)
    assert any(h.get("ignition") for h in hot) and len(msgs) == 1 and rechecks == [1]
    assert "وليست توصية" in msgs[0]
    await movers.hunt_movers(db, IgnMarket(), notify_owner=owner, on_ignition=recheck)
    assert len(msgs) == 1  # same move: no second alert


def test_status_shows_every_agent_with_last_work_and_next_run(db, monkeypatch):
    from datetime import timedelta

    from app.config import get_settings
    from app.hq import runtime
    from app.hq.agents.manager import _template_reply, status_facts
    from app.hq.registry import ensure_agents
    from app.hq.scheduler import next_run
    now = utcnow()
    assert next_run("every 30m", now - timedelta(minutes=10), now) == now + timedelta(minutes=20)
    assert next_run("daily 08:00", None, now) > now
    monkeypatch.setattr(get_settings(), "hq_enabled", True)
    ensure_agents(db)
    with runtime.task(db, "trend_hunter", "movers", "Hunting") as t:
        t.result = {"summary": "hot list: PORTALUSDT"}
    txt = _template_reply("status", status_facts(db), True)
    assert txt.count("\n⚪") + txt.count("\n🟢") + txt.count("\n🔴") == 15  # every agent except Maya, one line each
    assert "hot list: PORTALUSDT" in txt and "التالي" in txt
