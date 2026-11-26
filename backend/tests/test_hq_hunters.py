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
