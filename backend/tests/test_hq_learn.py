"""Learning from real outcomes (fingerprint → pattern twin → gate → approval) and extra daily picks up to the cap."""
from datetime import timedelta

from app.hq.agents import manager
from app.hq.agents import risk as riskmod
from app.hq.loops import daily_pick as dp
from app.hq.loops import learn
from app.hq.models import HQApproval, HQBacktestRun, HQKnowledge, HQProposal
from app.hq.strategies.base import Reject, Setup
from app.hq.strategies.builtin import BUILTINS
from app.hq.strategies.registry import active_strategies
from app.market.binance import SymbolInfo
from app.models import Signal, utcnow
from app.trading_config import load_config, save_config
from test_hq_trading import FakeMarket


class LearnMarket(FakeMarket):
    async def klines_since(self, sym, tf, start_ms, limit=1000):
        return [c for c in self.c[tf] if c.open_time >= start_ms][:limit]


def _signal(db, outcome="TARGET_HIT"):
    now = utcnow()
    s = Signal(symbol="TESTUSDT", base_asset="TEST", entry_price=100, take_profit=104.5, stop_loss=98.5, expected_move_pct=4.5,
               risk_reward=3, confidence=70, status=outcome, theoretical_outcome=outcome, theoretical_return_pct=4.5,
               entry_valid_until=now, expires_at=now + timedelta(days=2), created_at=now - timedelta(minutes=1))
    db.add(s)
    db.commit()
    return s


def test_twin_spec_is_safe_scale_free_and_bounded():
    fp = {"rsi_1h": 55.0, "adx_1h": 28.0, "bb_pos_1h": 0.7, "ret_24_1h": 3.0, "atr_pct_1h": 1.1,
          "close_1h>ema50_1h": True, "ema21_4h>ema50_4h": True, "close_4h>ema200_4h": False}
    spec = learn.twin_spec(fp, "Twin of #1")
    assert 4 <= len(spec.conditions) <= 12
    assert {"left": "close_4h", "op": "<", "right": "ema200_4h"} in [c.model_dump() for c in spec.conditions]
    assert all(not c.left.startswith(("close_", "ema")) or isinstance(c.right, str) for c in spec.conditions)  # no raw prices
    assert learn.twin_spec({"rsi_1h": 50}, "x") is None  # too little information → no twin


async def test_winner_is_studied_gated_and_never_activated_without_approval(db):
    sig = _signal(db)
    r = await learn.learn_from_outcomes(db, LearnMarket("up"))
    assert r["studied"] == 1 and r["results"][0]["fingerprint"]
    k = db.query(HQKnowledge).filter_by(slug=f"outcome-signal-{sig.id}").one()
    assert "Winner" in k.title and "not evidence" in k.body_md
    p = db.query(HQProposal).filter_by(knowledge_id=k.id).one()
    assert p.kind == "rule_strategy" and db.query(HQBacktestRun).count() >= 1  # the gate really ran
    assert r["results"][0]["status"] in ("failed", "awaiting_approval")
    assert not any(a.strategy.id.startswith("rule:twin") for a in active_strategies(db))  # never live without approval
    if r["results"][0]["status"] == "awaiting_approval":
        assert db.query(HQApproval).filter_by(status="pending").count() == 1
    again = await learn.learn_from_outcomes(db, LearnMarket("up"))
    assert again["studied"] == 0  # each outcome studied once


async def test_loser_is_noted_without_a_proposal(db):
    _signal(db, "STOP_LOSS_HIT")
    sent = []

    async def owner(msg):
        sent.append(msg)
    r = await learn.learn_from_outcomes(db, LearnMarket("up"), owner)
    assert r["results"][0]["status"] == "noted" and db.query(HQProposal).count() == 0
    assert sent and "ليست نصيحة مالية" in sent[0]


def test_manager_routes_learn_from_win():
    assert manager.detect_intent("اريد ان يتعلم البوت من هاذي التوصية")[0] == "lessons"
    assert manager.detect_intent("learn from the winning signal")[0] == "lessons"
    assert manager.detect_intent("learn about breakout strategies")[0] == "learn"


class ThreeMarkets(FakeMarket):
    SYMS = ("AAAUSDT", "BBBUSDT", "CCCUSDT")

    async def exchange_info(self):
        return {s: SymbolInfo(s, s[:-4], "USDT", "TRADING", True, 0.0001, 0.001, 5) for s in (*self.SYMS, "BTCUSDT")}

    async def tickers_24h(self):
        return [{"symbol": s, "lastPrice": "100", "quoteVolume": "90000000", "priceChangePercent": "2", "highPrice": "104",
                 "lowPrice": "98"} for s in self.SYMS]


async def test_extra_picks_fill_owner_cap_only_with_valid_setups(db, monkeypatch):
    for sid in BUILTINS:
        monkeypatch.setattr(BUILTINS[sid].__class__, "evaluate", lambda self, ctx, p, r: Reject(self.id, "no"))

    def ev(self, ctx, p, risk):
        e = ctx.last_price
        return Setup(self.id, ctx.symbol, e, round(e * 0.987, 4), round(e * 1.04, 4), round(e * 0.99, 4), 0.95, ["x"], 3.08, 2.33, 4.0, 1.3)
    monkeypatch.setattr(BUILTINS["oversold_bounce"].__class__, "evaluate", ev)
    monkeypatch.setattr(riskmod, "btc_filter", lambda btc: (True, "BTC market filter: OK"))
    cfg = load_config(db)
    cfg.risk.max_signals_per_day = 5
    save_config(db, cfg)
    r = await dp.run_daily_pick(db, ThreeMarkets("up"))
    refs = sorted(p["ref"].rsplit("-", 1)[-1] for p in r["picks"])
    assert refs == ["extra1", "extra2", "swing"]  # 3 valid setups on 3 coins → 3 signals, never more than exist
    assert len({p["symbol"] for p in r["picks"]}) == 3
    assert any("EXTRA PICK" in s.justification[0] for s in db.query(Signal))
    await dp.run_daily_pick(db, ThreeMarkets("up"))  # later re-check: same coins are in cooldown → nothing forced
    assert db.query(Signal).count() == 3
